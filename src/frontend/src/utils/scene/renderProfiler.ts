// Steady-state render profiler (admin-only, opt-in — ``action='render'``).
//
// Fed by the ThreeCanvas animate loop (beforeRender/afterRender around the
// renderer.render call). It samples, per rendered frame:
//   * inter-frame interval  -> FPS distribution
//   * renderer.render wall   -> CPU frame time (main-thread submission cost)
//   * GPU time per frame     -> EXT_disjoint_timer_query_webgl2 (true GPU ms,
//                               the CPU-bound vs GPU-bound discriminator)
//   * renderer.info          -> draw calls, triangles, programs, geometries
//   * long frames (>50ms)    -> jank count
//
// It rolls those up over a window (a few seconds of *rendered* frames, so
// idle on-demand time doesn't dilute it) and posts ONE aggregated row per
// window, keyed to the loaded model. Gated by ``collectRenderMetrics`` +
// admin; a no-op (single store read) otherwise, so the render loop pays
// nothing by default.

import {rendererRef} from "@/state/refs";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {useMeStore} from "@/state/meStore";
import {CallProfiler} from "@/utils/scene/callProfiler";

const WINDOW_MS = 8000; // flush cadence
const MIN_FRAMES = 30; // don't post a window with too few samples
const LONG_FRAME_MS = 50;

interface GpuQuery {
    q: WebGLQuery;
}

class RenderProfiler {
    private enabled = false;
    private profileCalls = false;
    private callProfiler: CallProfiler | null = null;
    private lastEnabledCheck = 0;
    private windowStart = 0;
    private lastFrameTs = 0;
    private renderStartTs = 0;

    private frameIntervals: number[] = [];
    private renderMs: number[] = [];
    private gpuMs: number[] = [];
    private drawCalls = 0;
    private triangles = 0;
    private programs = 0;
    private geometries = 0;
    private textures = 0;
    private longFrames = 0;

    private gl: WebGL2RenderingContext | null = null;
    private ext: any = null;
    private extResolved = false;
    private pending: GpuQuery[] = [];

    /** Cheap per-frame gate. Re-reads the toggle at most ~once/sec. */
    private checkEnabled(nowTs: number): boolean {
        if (nowTs - this.lastEnabledCheck > 1000) {
            this.lastEnabledCheck = nowTs;
            let on = false;
            try {
                const st = useViewMetricsStore.getState();
                on = st.collectRenderMetrics && useMeStore.getState().isAdmin;
                // Reuse the same "Profile calls" sub-toggle as the load path.
                this.profileCalls = on && st.profileCalls;
            } catch {
                on = false;
            }
            if (on && !this.enabled) this.reset(nowTs); // entering — start a clean window
            this.enabled = on;
        }
        return this.enabled;
    }

    private reset(nowTs: number): void {
        this.windowStart = nowTs;
        this.frameIntervals = [];
        this.renderMs = [];
        this.gpuMs = [];
        this.drawCalls = 0;
        this.triangles = 0;
        this.programs = 0;
        this.geometries = 0;
        this.textures = 0;
        this.longFrames = 0;
        // Start a fresh per-window call profiler when "Profile calls" is on.
        // 16ms interval (~one sample/frame) keeps the sampling overhead — and
        // its observer effect on the frame times we're measuring — modest.
        // flush() hands the previous one to post() to stop + summarize.
        this.callProfiler = null;
        if (this.profileCalls) {
            const p = new CallProfiler(16);
            p.start();
            if (p.active) this.callProfiler = p;
        }
    }

    private resolveGl(): void {
        if (this.extResolved) return;
        this.extResolved = true;
        try {
            const ctx = rendererRef.current?.getContext();
            if (ctx && typeof (ctx as WebGL2RenderingContext).createQuery === "function") {
                this.gl = ctx as WebGL2RenderingContext;
                this.ext = this.gl.getExtension("EXT_disjoint_timer_query_webgl2");
            }
        } catch {
            this.gl = null;
            this.ext = null;
        }
    }

    /** Call immediately before ``renderer.render(...)``. */
    beforeRender(): void {
        const nowTs = performance.now();
        if (!this.checkEnabled(nowTs)) return;
        this.renderStartTs = nowTs;
        this.resolveGl();
        // Start a GPU timer query for this frame (only one TIME_ELAPSED can
        // be active at a time, so begin here and read back a few frames on).
        if (this.gl && this.ext) {
            try {
                const q = this.gl.createQuery();
                if (q) {
                    this.gl.beginQuery(this.ext.TIME_ELAPSED_EXT, q);
                    this.pending.push({q});
                }
            } catch {
                /* ignore */
            }
        }
    }

    /** Call immediately after ``renderer.render(...)``. */
    afterRender(): void {
        if (!this.enabled) return;
        const nowTs = performance.now();
        // End the GPU query opened in beforeRender.
        if (this.gl && this.ext) {
            try {
                this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
            } catch {
                /* ignore */
            }
            this.drainGpuQueries();
        }
        // CPU render submission wall time.
        const renderMs = nowTs - this.renderStartTs;
        this.renderMs.push(renderMs);
        // Inter-frame interval -> FPS.
        if (this.lastFrameTs > 0) {
            const interval = nowTs - this.lastFrameTs;
            this.frameIntervals.push(interval);
            if (interval > LONG_FRAME_MS) this.longFrames += 1;
        }
        this.lastFrameTs = nowTs;
        // renderer.info snapshot (max over the window — these are
        // per-frame constants barring LOD/culling swings).
        try {
            const info = rendererRef.current?.info;
            if (info) {
                this.drawCalls = Math.max(this.drawCalls, info.render?.calls || 0);
                this.triangles = Math.max(this.triangles, info.render?.triangles || 0);
                this.programs = Math.max(this.programs, info.programs?.length || 0);
                this.geometries = Math.max(this.geometries, info.memory?.geometries || 0);
                this.textures = Math.max(this.textures, info.memory?.textures || 0);
            }
        } catch {
            /* ignore */
        }
        if (nowTs - this.windowStart >= WINDOW_MS) this.flush(nowTs);
    }

    /** Poll completed GPU timer queries; result is nanoseconds. */
    private drainGpuQueries(): void {
        if (!this.gl || !this.ext) return;
        try {
            const disjoint = this.gl.getParameter(this.ext.GPU_DISJOINT_EXT);
            if (disjoint) {
                // Timing was interrupted (context switch) — drop in-flight queries.
                for (const p of this.pending) this.gl.deleteQuery(p.q);
                this.pending = [];
                return;
            }
            const keep: GpuQuery[] = [];
            for (const p of this.pending) {
                const available = this.gl.getQueryParameter(p.q, this.gl.QUERY_RESULT_AVAILABLE);
                if (available) {
                    const ns = this.gl.getQueryParameter(p.q, this.gl.QUERY_RESULT) as number;
                    this.gpuMs.push(ns / 1e6);
                    this.gl.deleteQuery(p.q);
                } else {
                    keep.push(p);
                }
            }
            // Cap the backlog so a stuck query can't grow unbounded.
            this.pending = keep.slice(-8);
        } catch {
            /* ignore */
        }
    }

    private flush(nowTs: number): void {
        const frames = this.frameIntervals.length;
        if (frames < MIN_FRAMES) {
            this.reset(nowTs);
            return;
        }
        // Hand the window's call profiler to post() to stop + summarize;
        // reset() below then starts a fresh one for the next window.
        const prof = this.callProfiler;
        this.callProfiler = null;
        const pct = (arr: number[], p: number): number | null => {
            if (arr.length === 0) return null;
            const s = [...arr].sort((a, b) => a - b);
            const i = Math.min(s.length - 1, Math.max(0, Math.round(p * (s.length - 1))));
            return Math.round(s[i] * 10) / 10;
        };
        const fpsArr = this.frameIntervals.map((iv) => (iv > 0 ? 1000 / iv : 0));
        const cm: Record<string, unknown> = {
            kind: "render",
            window_ms: Math.round(nowTs - this.windowStart),
            frame_count: frames,
            fps_p50: pct(fpsArr, 0.5),
            fps_min: pct(fpsArr, 0.0),
            frame_ms_p50: pct(this.renderMs, 0.5),
            frame_ms_p95: pct(this.renderMs, 0.95),
            gpu_ms_p50: pct(this.gpuMs, 0.5),
            gpu_ms_p95: pct(this.gpuMs, 0.95),
            draw_calls: this.drawCalls,
            triangles: this.triangles,
            programs: this.programs,
            geometries: this.geometries,
            textures: this.textures,
            long_frames: this.longFrames,
            dpr: window.devicePixelRatio || 1,
        };
        this.post(cm, prof).catch(() => {});
        this.reset(nowTs);
    }

    private async post(cm: Record<string, unknown>, prof: CallProfiler | null): Promise<void> {
        try {
            // Per-window main-thread call hotspots (TS + WASM). Identifies a
            // CPU-bound render; GPU-bound shows in gpu_ms, not here.
            if (prof) {
                const frames = await prof.stop();
                if (frames.length > 0) cm["profile_frames"] = frames;
            }
            const {useModelState} = await import("@/state/modelState");
            const {useScopeStore, scopeUrlPart} = await import("@/state/scopeStore");
            const key = useModelState.getState().loadedSourceName;
            if (!key) return; // nothing loaded — no model to attribute to
            const scope = scopeUrlPart(useScopeStore.getState().current);
            const gr = (() => {
                try {
                    const gl = rendererRef.current?.getContext() as WebGLRenderingContext | undefined;
                    const dbg = gl?.getExtension("WEBGL_debug_renderer_info");
                    return dbg && gl ? String(gl.getParameter((dbg as any).UNMASKED_RENDERER_WEBGL) || "") : "";
                } catch {
                    return "";
                }
            })();
            if (gr) cm["gpu_renderer"] = gr.slice(0, 200);
            const {viewerApi} = await import("@/services/viewerApi");
            await viewerApi.recordRenderProfile(scope, {
                key,
                duration_ms: cm["window_ms"] as number,
                client_metrics: cm,
            });
        } catch {
            /* ignore */
        }
    }
}

export const renderProfiler = new RenderProfiler();
