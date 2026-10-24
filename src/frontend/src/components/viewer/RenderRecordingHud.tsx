import React, {useEffect, useState} from "react";
import {useMeStore} from "@/state/meStore";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {type LiveProfile, renderProfiler} from "@/utils/scene/renderProfiler";

// Shown while a render-profiling session records (Shift+R, or the Performance
// option): that it is recording, for how long, and the live frame rate and
// frame cost it is sampling. Polled twice a second, so the HUD itself adds
// nothing to the frames it measures.

const POLL_MS = 500;

function clock(ms: number): string {
    const s = Math.max(0, Math.floor(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function fpsClass(fps: number): string {
    if (fps >= 55) return "text-emerald-300";
    if (fps >= 30) return "text-amber-300";
    return "text-red-300";
}

const RenderRecordingHud: React.FC = () => {
    const on = useViewMetricsStore((s) => s.collectRenderMetrics);
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [live, setLive] = useState<LiveProfile | null>(null);

    useEffect(() => {
        if (!on || !isAdmin) return;
        const id = setInterval(() => setLive(renderProfiler.live()), POLL_MS);
        return () => {
            clearInterval(id);
            setLive(null);
        };
    }, [on, isAdmin]);

    if (!on || !isAdmin) return null;

    return (
        <div
            className="pointer-events-auto fixed right-2 top-2 z-[60] flex items-center gap-3 rounded-md border border-gray-600 bg-gray-900/85 px-2 py-1 font-mono text-xs text-gray-100 shadow-lg backdrop-blur-sm"
            role="status"
            aria-label="Render profiling"
        >
            <span className="flex items-center gap-1.5 font-semibold text-red-400" title="Recording render metrics">
                <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-500"/>
                REC {live ? clock(live.elapsedMs) : "…"}
            </span>
            {live?.fps != null ? (
                <>
                    <span className={fpsClass(live.fps)} title="Frames per second, over the last second">
                        {live.fps.toFixed(0)} fps
                    </span>
                    <span title="CPU time of the last render call">cpu {live.renderMs!.toFixed(1)} ms</span>
                    {live.gpuMs != null && <span title="GPU time of a recent frame">gpu {live.gpuMs.toFixed(1)} ms</span>}
                </>
            ) : (
                <span className="text-gray-400" title="Nothing is rendering: the view is still">idle</span>
            )}
            {live && (
                <span
                    className={live.longTasks ? "text-amber-300" : "text-gray-400"}
                    title="Main-thread tasks over 50 ms this session"
                >
                    {live.longTasks} long
                </span>
            )}
            {live && <span className="text-gray-400" title="8-second windows posted this session">{live.windowsPosted} win</span>}
            <button
                type="button"
                className="rounded-sm border border-gray-600 px-1.5 hover:bg-gray-700"
                title="Stop recording (Shift+R)"
                onClick={() => useViewMetricsStore.getState().setCollectRenderMetrics(false)}
            >
                Stop
            </button>
        </div>
    );
};

export default RenderRecordingHud;
