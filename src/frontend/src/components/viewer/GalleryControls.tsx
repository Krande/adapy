import React, {useCallback, useEffect, useMemo, useRef, useState} from "react";
import {useGalleryStore} from "@/state/galleryStore";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {useModelState} from "@/state/modelState";
import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useLoadQueueStore} from "@/state/loadQueueStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {convertWithSelection} from "@/services/conversion";
import {SerializerTessellatorSelect} from "@/components/convert/SerializerTessellatorSelect";
import type {SerializerSelection} from "@/services/conversion/serializerMatrix";
import {canLoadIntoSceneLegacy, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {collectGeomEntries, focusGeomEntry, endGeomWalk, type GeomEntry} from "@/utils/scene/galleryWalk";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";

// Gallery HUD. Three "walk" types (dropdown):
//   - "files": cycles the current scope's loadable files (clear → load).
//   - "geoms": cycles every geom in the scene, selecting + framing each,
//     ordered by scene or mesh density.
//   - "tree": the same, in model-tree hierarchy order.
// Desktop: compact card top-right. Mobile: full-width bottom bar.
const GalleryControls: React.FC = () => {
    const enabled = useGalleryStore((s) => s.enabled);
    const walk = useGalleryStore((s) => s.walk);
    const setWalk = useGalleryStore((s) => s.setWalk);
    const geomOrder = useGalleryStore((s) => s.geomOrder);
    const setGeomOrder = useGalleryStore((s) => s.setGeomOrder);
    const hideUnselected = useGalleryStore((s) => s.hideUnselected);
    const toggleHideUnselected = useGalleryStore((s) => s.toggleHideUnselected);
    const setMobileBarHeight = useGalleryStore((s) => s.setMobileBarHeight);

    // Publish the mobile bar's live height so the audit toast can stack above it (not overlap).
    // getBoundingClientRect().height is 0 when the bar is display:none (desktop md:hidden), so the
    // offset naturally applies only on mobile.
    const mobileBarRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        const el = mobileBarRef.current;
        if (!el || typeof ResizeObserver === "undefined") {
            setMobileBarHeight(0);
            return;
        }
        const publish = () => setMobileBarHeight(el.getBoundingClientRect().height);
        const ro = new ResizeObserver(publish);
        ro.observe(el);
        publish();
        return () => {
            ro.disconnect();
            setMobileBarHeight(0);
        };
    }, [setMobileBarHeight, enabled]);

    const fileObjects = useServerInfoStore((s) => s.serverFileObjects);
    // The overlay load path registers into the plural loaded-source SET (not the
    // singular loadedSourceName), so anchor + "is it shown?" checks use the set.
    const loadedSources = useModelState((s) => s.loadedSourceNames);
    const loadBusy = useLoadQueueStore((s) => s.current);
    const selectedName = useObjectInfoStore((s) => s.name);
    // A stable key for the active scope so a scope switch resets the walk (its geom entries point at
    // the old scope's now-disposed meshes).
    const scopeKey = useScopeStore((s) => (s.current ? scopeUrlPart(s.current) : ""));

    const isGeomWalk = walk === "geoms";

    // ---- Files walk -------------------------------------------------------
    // The gallery walks only files the viewer can actually mount.
    const files = useMemo(
        () =>
            fileObjects
                .map((f) => f.name)
                .filter((n) => isStreamingFEAResult(n) || canLoadIntoSceneLegacy(n)),
        [fileObjects],
    );

    const [index, setIndex] = useState(0);

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

    // Keep the index anchored to whatever is actually shown: when a gallery file
    // is in the scene point at it; when the file list changes (scope switch /
    // refresh) clamp into range.
    useEffect(() => {
        if (files.length === 0) return;
        const at = files.findIndex((f) => loadedSources.has(f));
        if (at >= 0) setIndex(at);
        else setIndex((i) => Math.min(i, files.length - 1));
    }, [files, loadedSources]);

    // Auto-load the current pick when the FILES walk turns on with nothing from
    // the scope's files already in the scene — otherwise the HUD shows a path but
    // the viewer stays empty until the user clicks Next/Prev.
    useEffect(() => {
        if (!enabled || isGeomWalk || files.length === 0 || loadBusy) return;
        const anyShown = files.some((f) => loadedSources.has(f));
        if (!anyShown) void go(index);
        // Only re-evaluate on enable / file-list change, not on every index tick.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, isGeomWalk, files]);

    // ---- Geoms / tree walk ------------------------------------------------
    const [geomEntries, setGeomEntries] = useState<GeomEntry[]>([]);
    const [geomIndex, setGeomIndex] = useState(0);
    const focusedOnce = useRef(false);

    const rebuildGeoms = useCallback(() => {
        if (!isGeomWalk) return [] as GeomEntry[];
        // Runs synchronously inside effects; a mid-transition scene must never throw here (that
        // would unmount the whole app). Fall back to an empty walk on any error.
        let entries: GeomEntry[] = [];
        try {
            entries = collectGeomEntries(geomOrder);
        } catch {
            entries = [];
        }
        setGeomEntries(entries);
        return entries;
    }, [isGeomWalk, geomOrder]);

    const goGeom = useCallback(
        async (next: number, entriesOverride?: GeomEntry[]) => {
            const entries = entriesOverride ?? geomEntries;
            if (entries.length === 0) return;
            const wrapped = (next + entries.length) % entries.length;
            setGeomIndex(wrapped);
            await focusGeomEntry(entries[wrapped], {hideUnselected, forceEdges: geomOrder === "distorted"});
        },
        [geomEntries, hideUnselected, geomOrder],
    );

    // Entering a geom walk or changing walk/order (re)builds the list and
    // restarts the slideshow at the first geom — a predictable place to begin.
    useEffect(() => {
        if (!enabled || !isGeomWalk) return;
        const entries = rebuildGeoms();
        setGeomIndex(0);
        if (entries.length > 0) {
            focusedOnce.current = true;
            void goGeom(0, entries);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, isGeomWalk, geomOrder]);

    // A scene change (file loaded/unloaded) rebuilds the list but keeps the
    // current position; focus the first geom if the scene only just populated.
    useEffect(() => {
        if (!enabled || !isGeomWalk) return;
        const entries = rebuildGeoms();
        setGeomIndex((i) => Math.min(i, Math.max(0, entries.length - 1)));
        if (!focusedOnce.current && entries.length > 0) {
            focusedOnce.current = true;
            void goGeom(0, entries);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [loadedSources]);

    // Re-apply isolation when the hide-unselected toggle flips mid-walk.
    useEffect(() => {
        if (!enabled || !isGeomWalk || geomEntries.length === 0) return;
        void goGeom(geomIndex);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hideUnselected]);

    // Switching scope disposes the current scope's meshes; drop the walk's stale entries + any
    // isolation/selection so nothing references a freed mesh. The scene-change effect above then
    // rebuilds cleanly once the new scope's geoms land.
    useEffect(() => {
        endGeomWalk();
        setGeomEntries([]);
        setGeomIndex(0);
        focusedOnce.current = false;
    }, [scopeKey]);

    // Leaving a geom walk (switch to files, disable gallery) restores the scene —
    // but only if we actually entered one, so enabling the files walk never
    // clears a selection the user made before opening the gallery.
    useEffect(() => {
        if ((!enabled || !isGeomWalk) && focusedOnce.current) {
            focusedOnce.current = false;
            endGeomWalk();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, isGeomWalk]);

    // ---- Shared nav -------------------------------------------------------
    const step = useCallback(
        (delta: number) => {
            if (isGeomWalk) void goGeom(geomIndex + delta);
            else void go(index + delta);
        },
        [isGeomWalk, goGeom, geomIndex, go, index],
    );

    // Left/Right arrows cycle prev/next while gallery mode is active (the camera
    // controls don't bind arrow keys, so there's no conflict). Ignored while
    // typing or while a file load is in flight; preventDefault stops scrolling.
    useEffect(() => {
        if (!enabled) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            const t = e.target as HTMLElement | null;
            if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
            if (!isGeomWalk && useLoadQueueStore.getState().current) return; // a load is already running
            e.preventDefault();
            step(e.key === "ArrowRight" ? 1 : -1);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [enabled, step, isGeomWalk]);

    // ---- Tools drawer (re-convert; FILES walk only) -----------------------
    const [toolsOpen, setToolsOpen] = useState(false);
    const [reconverting, setReconverting] = useState(false);
    const [reconvertMsg, setReconvertMsg] = useState<string | null>(null);
    // Serializer/tessellator pick for the reconvert. Empty until the user
    // touches a dropdown; convertWithSelection then normalises against the
    // backend matrix (defaults to the cpp/native server path).
    const [serializerSel, setSerializerSel] = useState<SerializerSelection>({});

    const current = files[index] ?? null;
    const currentExt = current ? (current.slice(current.lastIndexOf(".")).toLowerCase()) : "";

    const reconvert = useCallback(async () => {
        if (!current || reconverting) return;
        const scope = useScopeStore.getState().current;
        const scopePart = scope ? scopeUrlPart(scope) : "";
        setReconverting(true);
        setReconvertMsg("re-converting…");
        try {
            const derivedKey = await convertWithSelection(scopePart, current, "glb", {
                selection: serializerSel,
                reconvert: true,
            });
            await clear_loaded_model();
            const {overlay_file_in_scene} = await import("@/utils/scene/handlers/overlay_file_in_scene");
            await overlay_file_in_scene(current, derivedKey, {scope: scopePart});
            setReconvertMsg("loaded fresh conversion");
        } catch (e) {
            setReconvertMsg(`failed: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setReconverting(false);
        }
    }, [current, reconverting, serializerSel]);

    if (!enabled) return null;

    const count = isGeomWalk ? geomEntries.length : files.length;
    const activeIndex = isGeomWalk ? geomIndex : index;
    const empty = count === 0;
    const navBusy = !isGeomWalk && !!loadBusy;
    const [nameCopied, setNameCopied] = useState(false);
    const copyName = useCallback(async (text: string) => {
        if (!text) return;
        if (await writeToClipboard(text)) {
            setNameCopied(true);
            window.setTimeout(() => setNameCopied(false), 1200);
        }
    }, []);
    const curEntry = isGeomWalk ? geomEntries[geomIndex] : undefined;
    const spikeSuffix =
        geomOrder === "distorted" && curEntry
            ? ` · spike ${curEntry.spike.toFixed(1)}× · ${curEntry.spikeTris} tri${curEntry.spikeTris === 1 ? "" : "s"}`
            : "";
    const primaryLabel = isGeomWalk
        ? empty
            ? geomOrder === "distorted"
                ? "no distorted geoms 🎉"
                : "no geoms in scene"
            : (selectedName ?? `geom ${geomIndex + 1}`) + spikeSuffix
        : empty
          ? "no viewable files in this scope"
          : (current ?? "");

    const walkSelector = (
        <select
            aria-label="Walk type"
            value={walk}
            onChange={(e) => setWalk(e.target.value as typeof walk)}
            className="rounded-sm border border-gray-600 bg-gray-800 px-1 py-0.5 text-[11px] text-gray-100"
        >
            <option value="files">Files</option>
            <option value="geoms">Geoms</option>
        </select>
    );

    // Geom-walk sub-controls: order + isolate toggle + refresh.
    const geomControls = isGeomWalk && (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-300">
            <select
                aria-label="Geom order"
                value={geomOrder}
                onChange={(e) => setGeomOrder(e.target.value as typeof geomOrder)}
                className="rounded-sm border border-gray-600 bg-gray-800 px-1 py-0.5 text-[11px] text-gray-100"
            >
                <option value="scene">Order: scene</option>
                <option value="density">Order: density</option>
                <option value="tree">Order: tree</option>
                <option value="distorted">Order: distorted tris</option>
            </select>
            <label className="flex items-center gap-1" title="Hide everything except the current geom during the walk">
                <input type="checkbox" checked={hideUnselected} onChange={() => toggleHideUnselected()} />
                <span>Isolate</span>
            </label>
            <button
                type="button"
                onClick={() => {
                    const entries = rebuildGeoms();
                    if (entries.length) void goGeom(Math.min(geomIndex, entries.length - 1), entries);
                }}
                title="Rebuild the geom list from the current scene"
                className="rounded-sm border border-gray-600 px-1.5 py-0.5 hover:bg-gray-700"
            >
                ⟳
            </button>
        </div>
    );

    // Re-convert operates on the current file (files[index]), which is the loaded file in BOTH the
    // files walk and the geoms walk — so the reconvert-path tools are available in either mode (a user
    // walking a model's geoms can still reconvert it via a different path, e.g. to pick up a fix).
    const toolsDrawer = (
        <div className="border-t border-gray-700 pt-1">
            <button
                type="button"
                onClick={() => setToolsOpen((v) => !v)}
                className="flex w-full items-center justify-between text-[10px] uppercase tracking-wide text-gray-400 hover:text-gray-200"
            >
                <span>Tools</span>
                <span>{toolsOpen ? "▾" : "▸"}</span>
            </button>
            {toolsOpen && (
                <div className="mt-1 flex flex-col gap-1">
                    {current && (
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <SerializerTessellatorSelect
                                ext={currentExt}
                                target="glb"
                                value={serializerSel}
                                onChange={setSerializerSel}
                                disabled={reconverting}
                                compact
                            />
                        </div>
                    )}
                    <button
                        type="button"
                        disabled={empty || reconverting}
                        onClick={() => void reconvert()}
                        title="Run a fresh conversion (throwaway; never overwrites the audit-run product)"
                        className="rounded-sm border border-amber-600/60 px-2 py-0.5 text-amber-200 hover:bg-amber-900/30 disabled:opacity-40 disabled:cursor-default"
                    >
                        {reconverting ? "Re-converting…" : "↻ Re-convert"}
                    </button>
                    {reconvertMsg && <span className="truncate text-[10px] text-gray-400">{reconvertMsg}</span>}
                </div>
            )}
        </div>
    );

    return (
        <>
            {/* Desktop: compact top-right card. */}
            <div className="hidden md:flex pointer-events-auto absolute right-3 top-3 z-20 max-w-[42vw] flex-col gap-1 rounded-md border border-gray-600 bg-gray-900/85 px-2 py-1.5 text-xs text-gray-100 shadow-lg backdrop-blur-sm">
                <div className="flex items-center gap-2">
                    {walkSelector}
                    <button
                        type="button"
                        aria-label="Previous"
                        disabled={empty || navBusy}
                        onClick={() => step(-1)}
                        className="rounded-sm border border-gray-600 px-2 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                    >
                        ‹ Prev
                    </button>
                    <span className="tabular-nums text-gray-400">
                        {empty ? "0 / 0" : `${activeIndex + 1} / ${count}`}
                    </span>
                    <button
                        type="button"
                        aria-label="Next"
                        disabled={empty || navBusy}
                        onClick={() => step(1)}
                        className="rounded-sm border border-gray-600 px-2 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                    >
                        Next ›
                    </button>
                </div>
                {geomControls}
                <button
                    type="button"
                    onClick={() => void copyName(primaryLabel)}
                    disabled={empty}
                    title={empty ? primaryLabel : `Click to copy: ${primaryLabel}`}
                    className="truncate text-left font-mono text-[11px] text-gray-300 hover:text-gray-100 disabled:cursor-default"
                >
                    {nameCopied ? "✓ copied" : primaryLabel}
                </button>
                {toolsDrawer}
            </div>

            {/* Mobile: bottom bar, larger tap targets, clear of the top menu. */}
            <div ref={mobileBarRef} className="flex md:hidden pointer-events-auto absolute inset-x-2 bottom-2 z-20 flex-col gap-1 rounded-md border border-gray-600 bg-gray-900/90 px-2 py-2 text-xs text-gray-100 shadow-lg backdrop-blur-sm">
                <div className="flex items-center gap-2">
                    {walkSelector}
                    <button
                        type="button"
                        aria-label="Previous"
                        disabled={empty || navBusy}
                        onClick={() => step(-1)}
                        className="shrink-0 rounded-sm border border-gray-600 px-3 py-2 text-base leading-none hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                    >
                        ‹
                    </button>
                    <div className="min-w-0 flex-1 text-center">
                        <button
                            type="button"
                            onClick={() => void copyName(primaryLabel)}
                            disabled={empty}
                            title={empty ? primaryLabel : `Tap to copy: ${primaryLabel}`}
                            className="w-full truncate font-mono text-[11px] text-gray-200 hover:text-white disabled:cursor-default"
                        >
                            {nameCopied ? "✓ copied" : primaryLabel}
                        </button>
                        <div className="tabular-nums text-[10px] text-gray-500">
                            {empty ? "0 / 0" : `${activeIndex + 1} / ${count}`}
                        </div>
                    </div>
                    <button
                        type="button"
                        aria-label="Next"
                        disabled={empty || navBusy}
                        onClick={() => step(1)}
                        className="shrink-0 rounded-sm border border-gray-600 px-3 py-2 text-base leading-none hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                    >
                        ›
                    </button>
                </div>
                {geomControls}
                {toolsDrawer}
            </div>
        </>
    );
};

export default GalleryControls;
