// Browser model-load instrumentation (admin-only, opt-in).
//
// A LoadMetricsRecorder threads through the REST load chain
// (view_file_object_from_server -> replace_model -> setupModelLoaderAsync
// -> loadGLTF) and timestamps each phase so a slow load is attributable
// to a bottleneck CLASS rather than a single opaque number:
//
//   IO (storage/backend)  — ttfb (request -> first byte)
//   network (transfer)    — download (first byte -> last byte), throughput
//   CPU (main thread)     — decompress (pako path), parse (GLTFLoader),
//                           prepare (mesh/material/tree build)
//   GPU                   — first render (scene-add -> first painted frame)
//
// Network/IO detail comes from the Resource Timing API (authoritative for
// the fetch); CPU phases from performance.now() marks; GPU from a
// post-add rAF. Optionally a JS Self-Profiling pass captures per-function
// (TS + WASM) self-time so the slowest call surfaces — the load-side
// analogue of the conversion cProfile hotspots.
//
// NOTE on cross-origin: a presigned S3 URL is cross-origin, so its
// PerformanceResourceTiming exposes only ``duration`` unless the object
// store returns ``Timing-Allow-Origin``. The same-origin ``/blobs`` relay path
// gives the full DNS/TCP/TLS/TTFB + byte-size split. Wall-clock total /
// parse / prepare always work. To get the full network breakdown on the
// presigned path, set ``Timing-Allow-Origin: *`` (or the viewer origin)
// on the storage responses.
//
// Everything here is best-effort and must NEVER throw into the load path:
// the factory returns ``null`` when collection is off (default), and every
// method body is guarded.

import * as THREE from "three";
import {rendererRef} from "@/state/refs";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {useMeStore} from "@/state/meStore";
import {CallProfiler, type ProfileFrame} from "@/utils/scene/callProfiler";
import {getDeviceId} from "@/utils/deviceId";
import {perfOptionsSnapshot} from "@/state/perfStore";

export type LoadTransport = "presigned" | "relayed" | "blob" | "unknown";

interface LoadMeta {
    scope: string;
    key: string;
    sourceName: string;
    transport: LoadTransport;
}

function gpuRenderer(): string | undefined {
    try {
        const gl = rendererRef.current?.getContext() as WebGLRenderingContext | undefined;
        if (!gl) return undefined;
        const dbg = gl.getExtension("WEBGL_debug_renderer_info");
        if (!dbg) return undefined;
        return String(gl.getParameter((dbg as any).UNMASKED_RENDERER_WEBGL) || "") || undefined;
    } catch {
        return undefined;
    }
}

/** Sum triangles + vertices across every BufferGeometry under a group —
 * the authoritative payload size (independent of frustum culling, unlike
 * renderer.info.render). */
function payloadCounts(root: THREE.Object3D): {triangles: number; vertices: number} {
    let triangles = 0;
    let vertices = 0;
    try {
        root.traverse((o) => {
            const g = (o as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
            if (!g || !g.attributes || !g.attributes.position) return;
            const vcount = g.attributes.position.count;
            vertices += vcount;
            triangles += g.index ? g.index.count / 3 : vcount / 3;
        });
    } catch {
        /* ignore */
    }
    return {triangles: Math.round(triangles), vertices};
}

export class LoadMetricsRecorder {
    private meta: LoadMeta;
    private t0: number;
    private marks: Record<string, number> = {};
    private url: string | null = null;
    private callProfiler = new CallProfiler();
    private longTaskObserver: PerformanceObserver | null = null;
    private longTasks = 0;
    private longTaskMs = 0;
    private blockingMs = 0;
    private done = false;

    constructor(meta: LoadMeta, wantProfile: boolean) {
        this.meta = meta;
        this.t0 = performance.now();
        this.mark("start");
        // longtask observer — main-thread tasks >50ms during the load.
        try {
            this.longTaskObserver = new PerformanceObserver((list) => {
                for (const e of list.getEntries()) {
                    this.longTasks += 1;
                    this.longTaskMs += e.duration;
                    this.blockingMs += Math.max(0, e.duration - 50);
                }
            });
            this.longTaskObserver.observe({type: "longtask", buffered: true});
        } catch {
            this.longTaskObserver = null;
        }
        // JS Self-Profiling — Chromium-only, needs Document-Policy:
        // js-profiling. Guarded start() is a no-op otherwise.
        if (wantProfile) this.callProfiler.start();
    }

    private mark(name: string): void {
        this.marks[name] = performance.now();
    }

    setTransport(t: LoadTransport): void {
        this.meta.transport = t;
    }
    setUrl(u: string): void {
        // First-wins: the overlay path sets the real same-origin /blobs
        // URL (for Resource Timing) before loadGLTF later sets the in-memory
        // ``blob:`` object URL — which carries no network timing. Keep the
        // first, meaningful URL. On the streaming view path nothing sets it
        // before loadGLTF, so the presigned/relayed URL still wins there.
        if (!this.url) this.url = u;
    }
    markDownloadDone(): void {
        if (!this.marks["download_done"]) this.mark("download_done");
    }
    markParseDone(): void {
        this.mark("parse_done");
    }
    markPrepareDone(): void {
        this.mark("prepare_done");
    }

    /** Look up the GLB fetch in the Resource Timing buffer. Returns the
     * network/IO split where the browser exposes it (same-origin or TAO),
     * else just duration + whatever is available. */
    private resourceTiming(): Record<string, number | boolean> {
        const out: Record<string, number | boolean> = {};
        try {
            if (!this.url || this.url.startsWith("blob:")) return out;
            const entries = performance.getEntriesByName(this.url) as PerformanceResourceTiming[];
            const e = entries[entries.length - 1];
            if (!e) return out;
            const pos = (v: number) => (v && v > 0 ? v : 0);
            if (e.domainLookupEnd && e.domainLookupStart)
                out["dns_ms"] = Math.round(pos(e.domainLookupEnd - e.domainLookupStart));
            if (e.connectEnd && e.connectStart)
                out["tcp_ms"] = Math.round(pos(e.connectEnd - e.connectStart));
            if (e.secureConnectionStart && e.connectEnd)
                out["tls_ms"] = Math.round(pos(e.connectEnd - e.secureConnectionStart));
            if (e.responseStart && e.requestStart)
                out["ttfb_ms"] = Math.round(pos(e.responseStart - e.requestStart));
            if (e.responseEnd && e.responseStart)
                out["download_ms"] = Math.round(pos(e.responseEnd - e.responseStart));
            if (e.transferSize) out["transfer_bytes"] = e.transferSize;
            if (e.encodedBodySize) out["encoded_bytes"] = e.encodedBodySize;
            if (e.decodedBodySize) out["decoded_bytes"] = e.decodedBodySize;
            if (e.encodedBodySize && e.decodedBodySize)
                out["gzip"] = e.decodedBodySize > e.encodedBodySize * 1.05;
            // Server-Timing (if the API emits it, e.g. S3 GET duration).
            const st = (e as any).serverTiming as {name: string; duration: number}[] | undefined;
            if (Array.isArray(st)) {
                for (const s of st) {
                    if (s && typeof s.duration === "number" && s.name)
                        out[`server_${s.name}_ms`.slice(0, 48)] = Math.round(s.duration);
                }
            }
        } catch {
            /* ignore */
        }
        return out;
    }

    private deviceContext(): Record<string, number | string> {
        const out: Record<string, number | string> = {};
        try {
            out["device_id"] = getDeviceId();
            out["cores"] = navigator.hardwareConcurrency || 0;
            const dm = (navigator as any).deviceMemory;
            if (typeof dm === "number") out["device_memory_gb"] = dm;
            out["dpr"] = window.devicePixelRatio || 1;
            out["screen_w"] = window.screen?.width || 0;
            out["screen_h"] = window.screen?.height || 0;
            const gr = gpuRenderer();
            if (gr) out["gpu_renderer"] = gr.slice(0, 200);
            out["ua"] = navigator.userAgent.slice(0, 256);
            const mem = (performance as any).memory;
            if (mem) {
                out["js_heap_used_mb"] = Math.round(mem.usedJSHeapSize / 1048576);
                out["js_heap_limit_mb"] = Math.round(mem.jsHeapSizeLimit / 1048576);
            }
        } catch {
            /* ignore */
        }
        return out;
    }

    /** Called once the model is added to the scene. Captures GPU/first-
     * render via a double-rAF, gathers everything, and posts. */
    finalize(group: THREE.Object3D | null, gltf: {parser?: {json?: any}} | null): void {
        if (this.done) return;
        this.done = true;
        if (!this.marks["prepare_done"]) this.mark("prepare_done");
        // Two rAFs: the first schedules past the current frame, the second
        // fires after the freshly-added geometry has been uploaded + drawn.
        const afterPaint = () => {
            this.mark("first_render");
            this.flush(group, gltf).catch(() => {});
        };
        try {
            requestAnimationFrame(() => requestAnimationFrame(afterPaint));
        } catch {
            afterPaint();
        }
    }

    /** Abandon the recorder on a load error — post a failure row so the
     * dashboard sees failed loads too. */
    fail(error: string, stack?: string): void {
        if (this.done) return;
        this.done = true;
        this.flush(null, null, error, stack).catch(() => {});
    }

    private async flush(
        group: THREE.Object3D | null,
        gltf: {parser?: {json?: any}} | null,
        error?: string,
        stack?: string,
    ): Promise<void> {
        try {
            this.longTaskObserver?.disconnect();
        } catch {
            /* ignore */
        }
        const profileFrames: ProfileFrame[] = await this.callProfiler.stop();

        const m = this.marks;
        const dur = (a: string, b: string) =>
            m[a] != null && m[b] != null ? Math.round((m[b] - m[a]) * 10) / 10 : undefined;

        const rt = this.resourceTiming();
        const total_ms = m["first_render"] != null ? Math.round(m["first_render"] - this.t0) : Math.round(performance.now() - this.t0);

        // CPU phases from wall-clock marks. download_done is the loader's
        // 100%-progress edge (approximate); Resource Timing's download_ms
        // is preferred for the network number when present.
        const parse_ms = dur("download_done", "parse_done");
        const prepare_ms = dur("parse_done", "prepare_done");
        const first_render_ms = dur("prepare_done", "first_render");
        const fetch_wall_ms = dur("start", "download_done");

        const cm: Record<string, unknown> = {
            transport: this.meta.transport,
            source_name: this.meta.sourceName,
            total_ms,
            // network / IO — prefer Resource Timing, fall back to wall-clock.
            ttfb_ms: rt["ttfb_ms"],
            download_ms: rt["download_ms"] ?? fetch_wall_ms,
            dns_ms: rt["dns_ms"],
            tcp_ms: rt["tcp_ms"],
            tls_ms: rt["tls_ms"],
            transfer_bytes: rt["transfer_bytes"] ?? rt["encoded_bytes"],
            decoded_bytes: rt["decoded_bytes"],
            gzip: rt["gzip"],
            // CPU (main thread)
            parse_ms,
            prepare_ms,
            // GPU
            first_render_ms,
            // jank
            long_tasks: this.longTasks,
            long_task_ms: Math.round(this.longTaskMs),
            blocking_ms: Math.round(this.blockingMs),
            ...this.deviceContext(),
        };

        // Throughput from the most reliable byte count + download time.
        const dlMs = (cm["download_ms"] as number | undefined) ?? undefined;
        const bytes = (cm["transfer_bytes"] as number | undefined) ?? (cm["decoded_bytes"] as number | undefined);
        if (dlMs && dlMs > 0 && bytes) {
            cm["throughput_mbps"] = Math.round(((bytes * 8) / 1e6 / (dlMs / 1000)) * 10) / 10;
        }

        // Payload counts + renderer.info.
        if (group) {
            const {triangles, vertices} = payloadCounts(group);
            cm["triangles"] = triangles;
            cm["vertices"] = vertices;
            try {
                const ud = (gltf?.parser?.json ?? {}) as {
                    nodes?: unknown[];
                    meshes?: {primitives?: unknown[]}[];
                    materials?: unknown[];
                };
                cm["num_nodes"] = ud.nodes?.length ?? undefined;
                cm["num_meshes"] = ud.meshes?.length ?? undefined;
                cm["num_materials"] = ud.materials?.length ?? undefined;
                cm["num_primitives"] = (ud.meshes ?? []).reduce(
                    (n, mesh) => n + (mesh.primitives?.length ?? 0),
                    0,
                );
            } catch {
                /* ignore */
            }
        }
        try {
            const info = rendererRef.current?.info;
            if (info) {
                cm["draw_calls"] = info.render?.calls;
                cm["geometries"] = info.memory?.geometries;
                cm["textures"] = info.memory?.textures;
            }
        } catch {
            /* ignore */
        }
        if (profileFrames.length > 0) cm["profile_frames"] = profileFrames;
        cm["perf_options"] = perfOptionsSnapshot(); // which perf toggles were active

        const js_heap_used_mb = cm["js_heap_used_mb"] as number | undefined;

        const {viewerApi} = await import("@/services/viewerApi");
        await viewerApi.recordViewLoad(this.meta.scope, {
            key: this.meta.key,
            status: error ? "error" : "ok",
            error: error ?? null,
            // JS error stack for failed loads (e.g. malformed GLB) so the audit
            // Error panel has detail beyond the one-line message.
            traceback: stack ?? null,
            duration_ms: total_ms,
            read_bytes: (cm["transfer_bytes"] as number | undefined) ?? null,
            write_bytes: (cm["decoded_bytes"] as number | undefined) ?? null,
            peak_rss_kb: js_heap_used_mb != null ? js_heap_used_mb * 1024 : null,
            client_metrics: cm,
        });
    }
}

/** Start a recorder for one REST load, or return null when collection is
 * off / the user isn't an admin (so the load path stays zero-cost by
 * default). Every call site uses ``metrics?.method(...)``. */
export function beginLoadMetrics(meta: LoadMeta): LoadMetricsRecorder | null {
    try {
        const st = useViewMetricsStore.getState();
        if (!st.collectLoadMetrics) return null;
        if (!useMeStore.getState().isAdmin) return null;
        return new LoadMetricsRecorder(meta, st.profileCalls);
    } catch {
        return null;
    }
}
