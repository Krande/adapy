// The stats panel of the three.js-free viewport (Options > Performance > Show Stats): the frame rate
// and frame times of what was drawn, CPU and GPU time per frame, the renderer's counters, and a
// sparkline of recent frames. Frames are recorded as they are drawn; the text is redrawn four times a
// second, not per frame.
import React from "react";
import {useOptionsStore} from "@/state/optionsStore";
import type {AdacppStats} from "@/viewport/adacpp/types";
import type {NativeScene} from "./nativeScene";
import {createFrameRing, intervalsInOrder, recordFrame, summarize, type FrameSummary} from "./frameStats";

const REFRESH_MS = 250;
const SPARK_W = 200;
const SPARK_H = 36;
/** The sparkline's top, in ms: a frame slower than this is drawn at the top. */
const SPARK_MAX_MS = 50;

const count = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n}`);
const ms = (v: number | null) => (v === null ? "–" : v.toFixed(v < 10 ? 2 : 1));

interface Heap {
    usedJSHeapSize: number;
}

function drawSpark(canvas: HTMLCanvasElement, intervals: number[]): void {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const ratio = globalThis.devicePixelRatio || 1;
    canvas.width = SPARK_W * ratio;
    canvas.height = SPARK_H * ratio;
    ctx.scale(ratio, ratio);
    ctx.clearRect(0, 0, SPARK_W, SPARK_H);
    const y = (v: number) => SPARK_H - (Math.min(v, SPARK_MAX_MS) / SPARK_MAX_MS) * SPARK_H;
    // 60 and 30 frames per second.
    ctx.strokeStyle = "rgba(255,255,255,0.15)";
    for (const guide of [1000 / 60, 1000 / 30]) {
        ctx.beginPath();
        ctx.moveTo(0, y(guide));
        ctx.lineTo(SPARK_W, y(guide));
        ctx.stroke();
    }
    const step = SPARK_W / Math.max(1, intervals.length - 1);
    ctx.strokeStyle = "#7dd3fc";
    ctx.beginPath();
    let open = false;
    intervals.forEach((v, i) => {
        if (Number.isNaN(v)) {
            open = false; // a run starts here: the idle gap before it is not drawn
            return;
        }
        if (open) ctx.lineTo(i * step, y(v));
        else ctx.moveTo(i * step, y(v));
        open = true;
    });
    ctx.stroke();
}

export const NativeStats: React.FC<{scene: NativeScene}> = ({scene}) => {
    const show = useOptionsStore((s) => s.showPerf);
    const sparkRef = React.useRef<HTMLCanvasElement>(null);
    const [summary, setSummary] = React.useState<FrameSummary | null>(null);
    const [counters, setCounters] = React.useState<AdacppStats | null>(null);
    const [heap, setHeap] = React.useState<number | null>(null);
    const [rates, setRates] = React.useState<{callbacks: number; drawn: number; moves: number} | null>(null);

    React.useEffect(() => {
        if (!show) return;
        const ring = createFrameRing();
        const t = scene.frameTiming;
        const onFrame = () => recordFrame(ring, t.at, t.cpuMs, t.gpuMs);
        scene.measureFrames = true;
        scene.onFrame.add(onFrame);
        // One frame now, so the counters show without waiting for the camera to move.
        scene.invalidate();
        // Rates of the scene's running counts: animation-frame callbacks the browser ran, frames drawn,
        // and pointer moves. Callbacks below the display rate while the camera moves: the browser made
        // fewer frames (compositor or GPU back-pressure); callbacks at the display rate but fewer frames
        // drawn: the scene's own scheduling skipped them; few moves: the pointer reports that slowly.
        const c = scene.frameCounters;
        let mark = {at: performance.now(), callbacks: c.callbacks, drawn: c.drawn, moves: c.pointerMoves};
        const refresh = () => {
            const at = performance.now();
            const span = (at - mark.at) / 1000;
            if (span > 0) setRates({callbacks: (c.callbacks - mark.callbacks) / span, drawn: (c.drawn - mark.drawn) / span, moves: (c.pointerMoves - mark.moves) / span});
            mark = {at, callbacks: c.callbacks, drawn: c.drawn, moves: c.pointerMoves};
            setSummary(summarize(ring, performance.now()));
            setCounters(scene.renderStats());
            const memory = (performance as unknown as {memory?: Heap}).memory;
            setHeap(memory ? memory.usedJSHeapSize : null);
            if (sparkRef.current) drawSpark(sparkRef.current, intervalsInOrder(ring));
        };
        refresh();
        const timer = setInterval(refresh, REFRESH_MS);
        return () => {
            clearInterval(timer);
            scene.onFrame.delete(onFrame);
            scene.measureFrames = false;
        };
    }, [scene, show]);

    if (!show) return null;
    const s = summary;
    return (
        <div
            className="absolute right-2 top-2 z-[1] w-[216px] rounded-sm bg-gray-900/85 p-2 font-mono text-[11px] leading-tight text-gray-200 pointer-events-none"
            data-testid="native-stats"
        >
            <div className="flex items-baseline justify-between">
                <span className="text-base text-sky-300">{s?.idle ? "idle" : s?.fps ? `${s.fps.toFixed(0)} fps` : "–"}</span>
                <span className="text-gray-400">WebGPU</span>
            </div>
            <canvas ref={sparkRef} style={{width: SPARK_W, height: SPARK_H}} className="my-1 block"/>
            <div>frame p50 {ms(s?.p50 ?? null)} · p95 {ms(s?.p95 ?? null)} · max {ms(s?.max ?? null)} ms</div>
            <div>cpu {ms(s?.cpuMs ?? null)} ms · gpu {ms(s?.gpuMs ?? null)} ms</div>
            {rates && <div title="per second: animation-frame callbacks the browser ran / frames drawn / pointer moves">raf {rates.callbacks.toFixed(0)} · drawn {rates.drawn.toFixed(0)} · moves {rates.moves.toFixed(0)} /s</div>}
            {counters && (
                <>
                    <div>tris {count(counters.trianglesDrawn)} / {count(counters.trianglesTotal)}</div>
                    <div>clusters {count(counters.clustersDrawn)} / {count(counters.clustersTotal)}</div>
                    <div>draws {counters.drawCalls} · cull {counters.cullMs.toFixed(2)} ms</div>
                </>
            )}
            {heap !== null && <div>js heap {(heap / 1048576).toFixed(0)} MB</div>}
        </div>
    );
};
