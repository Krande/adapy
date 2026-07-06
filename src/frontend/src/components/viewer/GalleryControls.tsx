import React, {useCallback, useEffect, useMemo, useState} from "react";
import {useGalleryStore} from "@/state/galleryStore";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {useModelState} from "@/state/modelState";
import {useLoadQueueStore} from "@/state/loadQueueStore";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {canLoadIntoSceneLegacy, isStreamingFEAResult} from "@/utils/scene/fileKinds";

// Gallery HUD — cycles the current scope's loadable files one at a time.
// Desktop: compact card top-right. Mobile: full-width bar pinned to the
// bottom (thumb-reach) so it never collides with the top-left menu row
// (which wraps rightward on narrow screens).
const GalleryControls: React.FC = () => {
    const enabled = useGalleryStore((s) => s.enabled);
    const fileObjects = useServerInfoStore((s) => s.serverFileObjects);
    const loadedSourceName = useModelState((s) => s.loadedSourceName);
    const loadBusy = useLoadQueueStore((s) => s.current);

    // The gallery walks only files the viewer can actually mount.
    const files = useMemo(
        () =>
            fileObjects
                .map((f) => f.name)
                .filter((n) => isStreamingFEAResult(n) || canLoadIntoSceneLegacy(n)),
        [fileObjects],
    );

    const [index, setIndex] = useState(0);

    // Keep the index anchored to whatever is actually shown: when a file
    // loads (via the gallery or the storage panel) point at it; when the
    // file list changes (scope switch / refresh) clamp into range.
    useEffect(() => {
        if (files.length === 0) return;
        const at = loadedSourceName ? files.indexOf(loadedSourceName) : -1;
        if (at >= 0) setIndex(at);
        else setIndex((i) => Math.min(i, files.length - 1));
    }, [files, loadedSourceName]);

    const go = useCallback(
        async (next: number) => {
            if (files.length === 0) return;
            const wrapped = (next + files.length) % files.length;
            setIndex(wrapped);
            // Gallery = one at a time: clear the scene, then load the pick.
            await clear_loaded_model();
            useLoadQueueStore.getState().enqueue({name: files[wrapped]});
        },
        [files],
    );

    if (!enabled) return null;

    const current = files[index] ?? null;
    const empty = files.length === 0;

    return (
        <>
            {/* Desktop: compact top-right card. */}
            <div className="hidden md:flex pointer-events-auto absolute right-3 top-3 z-20 max-w-[42vw] flex-col gap-1 rounded-md border border-gray-600 bg-gray-900/85 px-2 py-1.5 text-xs text-gray-100 shadow-lg backdrop-blur-sm">
                <div className="flex items-center gap-2">
                    <button
                        type="button"
                        aria-label="Previous file"
                        disabled={empty || !!loadBusy}
                        onClick={() => void go(index - 1)}
                        className="rounded-sm border border-gray-600 px-2 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                    >
                        ‹ Prev
                    </button>
                    <span className="tabular-nums text-gray-400">
                        {empty ? "0 / 0" : `${index + 1} / ${files.length}`}
                    </span>
                    <button
                        type="button"
                        aria-label="Next file"
                        disabled={empty || !!loadBusy}
                        onClick={() => void go(index + 1)}
                        className="rounded-sm border border-gray-600 px-2 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                    >
                        Next ›
                    </button>
                </div>
                <div className="truncate font-mono text-[11px] text-gray-300" title={current ?? undefined}>
                    {empty ? "no viewable files in this scope" : current}
                </div>
            </div>

            {/* Mobile: bottom bar, larger tap targets, clear of the top menu. */}
            <div className="flex md:hidden pointer-events-auto absolute inset-x-2 bottom-2 z-20 items-center gap-2 rounded-md border border-gray-600 bg-gray-900/90 px-2 py-2 text-xs text-gray-100 shadow-lg backdrop-blur-sm">
                <button
                    type="button"
                    aria-label="Previous file"
                    disabled={empty || !!loadBusy}
                    onClick={() => void go(index - 1)}
                    className="shrink-0 rounded-sm border border-gray-600 px-3 py-2 text-base leading-none hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                >
                    ‹
                </button>
                <div className="min-w-0 flex-1 text-center">
                    <div className="truncate font-mono text-[11px] text-gray-200" title={current ?? undefined}>
                        {empty ? "no viewable files" : current}
                    </div>
                    <div className="tabular-nums text-[10px] text-gray-500">
                        {empty ? "0 / 0" : `${index + 1} / ${files.length}`}
                    </div>
                </div>
                <button
                    type="button"
                    aria-label="Next file"
                    disabled={empty || !!loadBusy}
                    onClick={() => void go(index + 1)}
                    className="shrink-0 rounded-sm border border-gray-600 px-3 py-2 text-base leading-none hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                >
                    ›
                </button>
            </div>
        </>
    );
};

export default GalleryControls;
