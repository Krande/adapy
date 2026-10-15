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
import {boolOptionFor, boolOptionSupported} from "@/services/conversion/serializerMatrix";
import {canOpenInScene} from "@/utils/scene/fileKinds";
import {collectGeomEntries, focusGeomEntry, focusJoint, endGeomWalk, type GeomEntry} from "@/utils/scene/galleryWalk";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import FilePickerModal from "@/components/common/FilePickerModal";
import {
    docFromMeta,
    exportFolderCandidates,
    filterByProjects,
    galleryFolderOrder,
    galleryProjects,
    galleryStops,
    stopOfAdjacentFile,
    type GalleryStop,
} from "@/assets/searchExport";
import {useTreeViewStore} from "@/state/treeViewStore";
import {searchExportApi, type ExportDoc} from "@/services/api/searchExport";
import GalleryProvenance from "./GalleryProvenance";

// Gallery HUD. Three "walk" types (dropdown):
//   - "files": cycles the current scope's loadable files (clear → load). In a folder an export
//     wrote, a merged file (a whole collection's hits) is walked hit by hit -- each isolated and
//     framed in turn -- before going on to the next file; PageUp/PageDown / « File » skip files; Shift+W toggles the gallery.
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
    // A folder filter ("Open in gallery" on a folder or an export run) narrows the walk to the files
    // under that folder -- sidecars never -- in the order its `_export.json` lists them when it has
    // one (`galleryFolderOrder`). It applies only while the scope it was opened for is current; with
    // none the walk is the whole scope, as it always was.
    const folderFilter = useGalleryStore((s) => s.folderFilter);
    const setFolderFilter = useGalleryStore((s) => s.setFolderFilter);
    const folder = folderFilter && folderFilter.scope === scopeKey ? folderFilter.folder : null;
    const [exportDoc, setExportDoc] = useState<ExportDoc | null>(null);
    // Re-read when the listing changes too: a running export rewrites the doc as items finish.
    useEffect(() => setExportDoc(null), [scopeKey, folder]);
    useEffect(() => {
        if (folder === null || !enabled) return;
        const ctl = new AbortController();
        searchExportApi
            .exportDoc(scopeKey, folder, ctl.signal)
            .then((d) => !ctl.signal.aborted && setExportDoc(d))
            .catch(() => {
                // No doc (or unreadable): the walk is alphabetical instead.
            });
        return () => ctl.abort();
    }, [scopeKey, folder, enabled, fileObjects]);
    // A merged file's own sidecar, when "Search result gallery" found no folder index listing it.
    const [metaDoc, setMetaDoc] = useState<{scope: string; folder: string; doc: Pick<ExportDoc, "files">} | null>(null);
    const doc: Pick<ExportDoc, "files"> | null =
        exportDoc ?? (metaDoc && metaDoc.scope === scopeKey && metaDoc.folder === folder ? metaDoc.doc : null);

    // The gallery walks only files the viewer can actually mount.
    const folderFiles = useMemo(
        () =>
            folder !== null
                ? galleryFolderOrder(fileObjects.map((f) => f.name), folder, doc, canOpenInScene)
                : fileObjects
                      .map((f) => f.name)
                      .filter((n) => canOpenInScene(n)),
        [fileObjects, folder, doc],
    );
    // Projects (collections) left out of a folder walk; all are in when the folder is opened.
    const projects = useMemo(() => (folder !== null ? galleryProjects(folderFiles, doc) : []), [folder, folderFiles, doc]);
    const [hiddenProjects, setHiddenProjects] = useState<ReadonlySet<string>>(new Set());
    const [projectsOpen, setProjectsOpen] = useState(false);
    useEffect(() => setHiddenProjects(new Set()), [scopeKey, folder]);
    const files = useMemo(
        () => (folder !== null ? filterByProjects(folderFiles, doc, hiddenProjects) : folderFiles),
        [folder, folderFiles, doc, hiddenProjects],
    );
    // The desktop panel: a fixed width, resized from its bottom-left corner (it is anchored top-right,
    // so dragging left widens it); dragging down grows the expanded thumbnail grid.
    const panelWidth = useGalleryStore((s) => s.panelWidth);
    const setPanelSize = useGalleryStore((s) => s.setPanelSize);
    const startResize = useCallback(
        (e: React.PointerEvent<HTMLDivElement>) => {
            e.preventDefault();
            const handle = e.currentTarget;
            handle.setPointerCapture(e.pointerId);
            const {panelWidth: w0, gridHeight: h0} = useGalleryStore.getState();
            const x0 = e.clientX;
            const y0 = e.clientY;
            const maxWidth = window.innerWidth - 24;
            const move = (ev: PointerEvent) =>
                setPanelSize({width: Math.min(maxWidth, w0 + (x0 - ev.clientX)), gridHeight: h0 + (ev.clientY - y0)});
            const up = () => {
                handle.removeEventListener("pointermove", move);
                handle.removeEventListener("pointerup", up);
                handle.removeEventListener("pointercancel", up);
            };
            handle.addEventListener("pointermove", move);
            handle.addEventListener("pointerup", up);
            handle.addEventListener("pointercancel", up);
        },
        [setPanelSize],
    );
    const toggleProject = useCallback(
        (name: string) =>
            setHiddenProjects((h) => {
                const next = new Set(h);
                if (!next.delete(name)) next.add(name);
                return next;
            }),
        [],
    );
    const fileSet = useMemo(() => new Set(files), [files]);
    // The stops: one per file, except a merged export file, which is one per hit inside it (in a
    // folder walk, where the export's doc lists them) -- so Next goes through a collection's hits
    // and then on to the next collection.
    const stops = useMemo(() => galleryStops(files, folder !== null ? doc : null), [files, folder, doc]);
    const hasJoints = useMemo(() => stops.some((s) => s.joint), [stops]);

    const [index, setIndex] = useState(0);
    // The hit to focus once its file is in the scene (and its tree built); null when there is none.
    const [pendingJoint, setPendingJoint] = useState<GalleryStop | null>(null);
    const jointFocused = useRef(false);

    const go = useCallback(
        async (next: number) => {
            if (stops.length === 0) return;
            const wrapped = (next + stops.length) % stops.length;
            const stop = stops[wrapped];
            setIndex(wrapped);
            setPendingJoint(stop.joint ? stop : null);
            // Another hit of the file on screen: no reload, just focus it (the effect below).
            if (useModelState.getState().loadedSourceNames.has(stop.file)) return;
            // Gallery = one at a time: clear the scene, then load the pick.
            if (jointFocused.current) {
                jointFocused.current = false;
                endGeomWalk();
            }
            await clear_loaded_model();
            useLoadQueueStore.getState().enqueue({name: stop.file});
        },
        [stops],
    );

    // Focus the pending hit when it can be: its file loaded, and -- for one found by name -- the
    // model tree built. Re-tried as either lands.
    const treeData = useTreeViewStore((s) => s.treeData);
    useEffect(() => {
        if (!enabled || isGeomWalk || !pendingJoint?.joint || !loadedSources.has(pendingJoint.file)) return;
        if (focusJoint(pendingJoint.joint, {hideUnselected})) {
            jointFocused.current = true;
            setPendingJoint(null);
        }
    }, [enabled, isGeomWalk, pendingJoint, loadedSources, treeData, hideUnselected]);

    // Keep the index anchored to whatever is actually shown: when a gallery file
    // is in the scene point at it (its current hit, or its first); when the list
    // changes (scope switch / refresh) clamp into range.
    useEffect(() => {
        if (stops.length === 0) return;
        setIndex((i) => {
            if (stops[i] && loadedSources.has(stops[i].file)) return i;
            const at = stops.findIndex((s) => loadedSources.has(s.file));
            return at >= 0 ? at : Math.min(i, stops.length - 1);
        });
    }, [stops, loadedSources]);

    // Auto-load the current pick when the FILES walk turns on with nothing from
    // the scope's files already in the scene — otherwise the HUD shows a path but
    // the viewer stays empty until the user clicks Next/Prev.
    useEffect(() => {
        if (!enabled || isGeomWalk || stops.length === 0 || loadBusy) return;
        const anyShown = files.some((f) => loadedSources.has(f));
        // A folder walk starts at its first file, not wherever the whole-scope walk had got to.
        if (!anyShown) void go(folder !== null ? 0 : index);
        // Only re-evaluate on enable / file-list change, not on every index tick.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [enabled, isGeomWalk, files]);

    // A doc read after the file was loaded turns its one stop into its hits: focus the current.
    useEffect(() => {
        if (!enabled || isGeomWalk || !hasJoints) return;
        const stop = stops[index];
        if (stop?.joint && loadedSources.has(stop.file) && !jointFocused.current) setPendingJoint(stop);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hasJoints]);

    // The Isolate toggle re-applies to the hit on screen.
    useEffect(() => {
        if (!enabled || isGeomWalk || !jointFocused.current) return;
        const stop = stops[index];
        if (stop?.joint) setPendingJoint(stop);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [hideUnselected]);

    // Leaving the files walk (or the gallery) drops a hit's isolation + selection.
    useEffect(() => {
        if ((!enabled || isGeomWalk) && jointFocused.current) {
            jointFocused.current = false;
            endGeomWalk();
        }
    }, [enabled, isGeomWalk]);

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
    // Files walk over merged files: straight to the next / previous file's first hit.
    const stepFile = useCallback((delta: 1 | -1) => void go(stopOfAdjacentFile(stops, index, delta)), [go, stops, index]);

    // ←/→ step prev/next; PageUp/PageDown step whole files when the walk goes through the hits
    // inside files. Unmodified keys only (Shift+arrows select tree siblings), and not a key another
    // handler already took. Ignored while typing or while a file load is in flight.
    useEffect(() => {
        if (!enabled) return;
        const onKey = (e: KeyboardEvent) => {
            const arrow = e.key === "ArrowLeft" || e.key === "ArrowRight";
            const page = e.key === "PageUp" || e.key === "PageDown";
            if (!arrow && !page) return;
            if (e.defaultPrevented || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
            if (page && (isGeomWalk || !hasJoints)) return;
            const t = e.target as HTMLElement | null;
            if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
            if (!isGeomWalk && useLoadQueueStore.getState().current) return; // a load is already running
            e.preventDefault();
            if (page) stepFile(e.key === "PageDown" ? 1 : -1);
            else step(e.key === "ArrowRight" ? 1 : -1);
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [enabled, step, stepFile, isGeomWalk, hasJoints]);

    // ---- Tools drawer (re-convert; FILES walk only) -----------------------
    const [toolsOpen, setToolsOpen] = useState(false);
    const [reconverting, setReconverting] = useState(false);
    const [reconvertMsg, setReconvertMsg] = useState<string | null>(null);
    // Serializer/tessellator pick for the reconvert. Empty until the user
    // touches a dropdown; convertWithSelection then normalises against the
    // backend matrix (defaults to the cpp/native server path).
    const [serializerSel, setSerializerSel] = useState<SerializerSelection>({});
    // Embed per-face pick regions in the reconverted GLB (a debugging aid — bigger GLB, serial face
    // tessellation), so individual surfaces become clickable. Only some serializers can produce
    // them; the backend says which via the option's supported_by, and the toggle disables itself
    // rather than quietly converting without them.
    const [faceRegions, setFaceRegions] = useState(false);

    const currentStop: GalleryStop | null = stops[index] ?? null;
    const current = currentStop?.file ?? null;
    const currentExt = current ? (current.slice(current.lastIndexOf(".")).toLowerCase()) : "";

    // Whether the backend offers face regions for THIS source + the currently selected serializer.
    // Checked, not assumed: it re-evaluates when the serializer changes, so switching to a path
    // that can't emit them disables the toggle instead of silently dropping the request.
    const faceRegionsOpt = boolOptionFor(currentExt, "glb", "face_regions");
    const faceRegionsSupported = boolOptionSupported(currentExt, "glb", "face_regions", serializerSel);
    const faceRegionsOn = faceRegions && faceRegionsSupported;

    const reconvert = useCallback(async () => {
        if (!current || reconverting) return;
        const scope = useScopeStore.getState().current;
        const scopePart = scope ? scopeUrlPart(scope) : "";
        setReconverting(true);
        setReconvertMsg("re-converting…");
        try {
            const derivedKey = await convertWithSelection(scopePart, current, "glb", {
                selection: serializerSel,
                // Only send it when the chosen serializer can actually honour it — asking for face
                // regions from a path that ignores the flag would return a GLB without them and
                // report success.
                extraOptions: faceRegionsOn ? {face_regions: true} : undefined,
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
        // faceRegionsOn belongs here: without it useCallback hands back a closure that captured the
        // value from an earlier render, so ticking the toggle re-renders but the memoized reconvert
        // still sends the old answer — the request goes out without face_regions and the conversion
        // reports success. react-hooks/exhaustive-deps would have caught it; eslint isn't wired up
        // for this package, so these arrays are hand-maintained.
    }, [current, reconverting, serializerSel, faceRegionsOn]);

    // NOTE: every hook must be declared before the `if (!enabled) return null` early return below —
    // React counts hooks per render, so a hook after the return changes the count when the gallery is
    // toggled on/off and crashes the app ("rendered more hooks than during the previous render").
    const [nameCopied, setNameCopied] = useState(false);
    const copyName = useCallback(async (text: string) => {
        if (!text) return;
        if (await writeToClipboard(text)) {
            setNameCopied(true);
            window.setTimeout(() => setNameCopied(false), 1200);
        }
    }, []);

    // Clicking the "N / M" counter opens a single-file picker (the shared storage-browser tree) to jump
    // straight to a file instead of stepping Prev/Next through a long scope. The picker returns a blob
    // key; map it back to the gallery's file list (by exact name or basename) and load it via `go` —
    // which works in both walks (in the geoms walk, loading a new file rebuilds the geom list).
    // "Search result gallery": walk the export the file on screen came from -- the folder whose
    // index lists it, else the file's own hits from its sidecar.
    const [searchGalleryMsg, setSearchGalleryMsg] = useState<string | null>(null);
    const openSearchGallery = useCallback(async () => {
        const file = fileObjects.map((f) => f.name).find((f) => loadedSources.has(f));
        if (!file) {
            setSearchGalleryMsg("load an exported file from this scope first");
            return;
        }
        setSearchGalleryMsg("looking for the export…");
        try {
            for (const candidate of exportFolderCandidates(file)) {
                const found = await searchExportApi.exportDoc(scopeKey, candidate).catch(() => null);
                if (found?.files.some((f) => f.file === file)) {
                    setWalk("files");
                    setFolderFilter({scope: scopeKey, folder: candidate});
                    setSearchGalleryMsg(null);
                    return;
                }
            }
            const own = docFromMeta(file, await searchExportApi.fileMeta(scopeKey, file).catch(() => null));
            if (!own) {
                setSearchGalleryMsg("this file is not a search export result");
                return;
            }
            const parent = file.includes("/") ? file.slice(0, file.lastIndexOf("/")) : "";
            setMetaDoc({scope: scopeKey, folder: parent, doc: own});
            setWalk("files");
            setFolderFilter({scope: scopeKey, folder: parent});
            setSearchGalleryMsg(null);
        } catch (e) {
            setSearchGalleryMsg(e instanceof Error ? e.message : String(e));
        }
    }, [fileObjects, loadedSources, scopeKey, setWalk, setFolderFilter]);

    const [filePickerOpen, setFilePickerOpen] = useState(false);
    const jumpToFile = useCallback(
        (key: string) => {
            setFilePickerOpen(false);
            const base = key.split("/").pop() ?? key;
            let at = files.indexOf(key);
            if (at < 0) at = files.findIndex((n) => (n.split("/").pop() ?? n) === base);
            // A merged file is entered at its first hit.
            const stop = at >= 0 ? stops.findIndex((s) => s.fileIndex === at) : -1;
            if (stop >= 0) void go(stop);
        },
        [files, stops, go],
    );

    if (!enabled) return null;

    const count = isGeomWalk ? geomEntries.length : stops.length;
    const activeIndex = isGeomWalk ? geomIndex : index;
    const empty = count === 0;
    const navBusy = !isGeomWalk && !!loadBusy;
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
          ? folder !== null
              ? "no viewable files in this folder"
              : "no viewable files in this scope"
          : (currentStop?.joint?.name ?? current ?? "");

    // The folder the walk is narrowed to, with a way back to the whole scope.
    const folderChip = folder !== null && !isGeomWalk && (
        <div className="flex items-center gap-1 text-[11px] text-gray-300">
            <span className="text-gray-500">Folder</span>
            <span className="truncate font-mono" title={folder}>
                {folder}/
            </span>
            {exportDoc && <span className="text-gray-500" title="Ordered as the export wrote them">· export order</span>}
            <button
                type="button"
                onClick={() => setFolderFilter(null)}
                title="Walk the whole scope again"
                aria-label="Clear the folder filter"
                className="ml-auto rounded-sm px-1 text-gray-400 hover:bg-gray-700 hover:text-white"
            >
                ×
            </button>
        </div>
    );
    const shownProjects = projects.length - projects.filter((p) => hiddenProjects.has(p.name)).length;
    const projectFilter = folder !== null && !isGeomWalk && projects.length > 1 && (
        <div className="text-[11px] text-gray-300">
            <button
                type="button"
                onClick={() => setProjectsOpen((v) => !v)}
                className="flex w-full items-center justify-between text-gray-400 hover:text-gray-200"
            >
                <span>
                    Projects {shownProjects}/{projects.length}
                </span>
                <span>{projectsOpen ? "▾" : "▸"}</span>
            </button>
            {projectsOpen && (
                <div className="mt-0.5 flex flex-col gap-0.5">
                    <div className="flex gap-2 text-[10px]">
                        <button type="button" className="text-blue-300 hover:underline" onClick={() => setHiddenProjects(new Set())}>
                            All
                        </button>
                        <button
                            type="button"
                            className="text-blue-300 hover:underline"
                            onClick={() => setHiddenProjects(new Set(projects.map((p) => p.name)))}
                        >
                            None
                        </button>
                    </div>
                    <div className="grid max-h-40 grid-cols-2 gap-x-3 overflow-y-auto">
                        {projects.map((p) => (
                            <label key={p.name} className="flex min-w-0 items-center gap-1">
                                <input type="checkbox" checked={!hiddenProjects.has(p.name)} onChange={() => toggleProject(p.name)} />
                                <span className="truncate" title={p.name}>
                                    {p.name}
                                </span>
                                <span className="ml-auto text-gray-500 tabular-nums">{p.files}</span>
                            </label>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
    const provenance = folder !== null && !isGeomWalk && !empty && (
        <GalleryProvenance scope={scopeKey} stops={stops} index={index} doc={doc} onJump={(n) => void go(n)} />
    );
    // Walking the hits inside merged files: where in the file, and a step to the next file.
    const fileBtn =
        "rounded-sm border border-gray-600 px-1.5 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default";
    const jointBar = hasJoints && !isGeomWalk && currentStop && (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-300">
            <button type="button" className={fileBtn} disabled={navBusy} onClick={() => stepFile(-1)} title="Previous file (PageUp)">
                « File
            </button>
            <span className="tabular-nums text-gray-400" title={current ?? ""}>
                file {currentStop.fileIndex + 1}/{files.length}
                {currentStop.joint ? ` · hit ${currentStop.jointIndex + 1}/${currentStop.joints}` : ""}
            </span>
            <button type="button" className={fileBtn} disabled={navBusy} onClick={() => stepFile(1)} title="Next file (PageDown)">
                File »
            </button>
            {currentStop.joint && (
                <label className="flex items-center gap-1" title="Hide the rest of the file while a hit is shown">
                    <input type="checkbox" checked={hideUnselected} onChange={() => toggleHideUnselected()} />
                    <span>Isolate</span>
                </label>
            )}
        </div>
    );

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
                    {!isGeomWalk && (
                        <button
                            type="button"
                            onClick={() => void openSearchGallery()}
                            title="Walk the search export this file came from, hit by hit"
                            className="rounded-sm border border-gray-600 px-2 py-0.5 text-left hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                        >
                            Search result gallery
                        </button>
                    )}
                    {searchGalleryMsg && <span className="truncate text-[10px] text-gray-400">{searchGalleryMsg}</span>}
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
                            {faceRegionsOpt && (
                                <label
                                    className={`flex items-center gap-1 ${faceRegionsSupported ? "" : "opacity-40"}`}
                                    title={
                                        faceRegionsSupported
                                            ? (faceRegionsOpt.description ?? "")
                                            : "The selected serializer can't embed face regions — pick one that can."
                                    }
                                >
                                    <input
                                        type="checkbox"
                                        className="no-drag"
                                        checked={faceRegionsOn}
                                        disabled={reconverting || !faceRegionsSupported}
                                        onChange={(e) => setFaceRegions(e.target.checked)}
                                    />
                                    <span>{faceRegionsOpt.title ?? "Clickable surfaces"}</span>
                                </label>
                            )}
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
            <div
                className="hidden md:flex pointer-events-auto absolute right-3 top-3 z-20 flex-col gap-1 rounded-md border border-gray-600 bg-gray-900/85 px-2 py-1.5 text-xs text-gray-100 shadow-lg backdrop-blur-sm"
                style={{width: `min(${panelWidth}px, calc(100vw - 1.5rem))`}}
            >
                <div
                    onPointerDown={startResize}
                    title="Drag to resize"
                    aria-label="Resize the gallery panel"
                    className="absolute bottom-0 left-0 h-3 w-3 cursor-nesw-resize rounded-bl-md border-b-2 border-l-2 border-gray-500 hover:border-blue-400"
                />
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
                    <button
                        type="button"
                        onClick={() => setFilePickerOpen(true)}
                        disabled={files.length === 0}
                        title="Jump to a file…"
                        className="tabular-nums text-gray-400 hover:text-gray-100 hover:underline disabled:cursor-default disabled:no-underline"
                    >
                        {empty ? "0 / 0" : `${activeIndex + 1} / ${count}`}
                    </button>
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
                {jointBar}
                {folderChip}
                {projectFilter}
                {provenance}
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
                        <button
                            type="button"
                            onClick={() => setFilePickerOpen(true)}
                            disabled={files.length === 0}
                            className="tabular-nums text-[10px] text-gray-500 hover:text-gray-300 disabled:cursor-default"
                        >
                            {empty ? "0 / 0" : `${activeIndex + 1} / ${count}`}
                        </button>
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
                {jointBar}
                {/* The provenance card is the desktop card's only: both bars are always mounted (one
                    hidden by CSS), and a second card would fetch everything twice. */}
                {folderChip}
                {projectFilter}
                {toolsDrawer}
            </div>

            <FilePickerModal
                open={filePickerOpen}
                scope={scopeKey}
                title="Jump to file"
                initialKey={current ?? undefined}
                filter={(f) => (folder !== null ? fileSet.has(f.key) : canOpenInScene(f.key))}
                onCancel={() => setFilePickerOpen(false)}
                onPick={jumpToFile}
            />
        </>
    );
};

export default GalleryControls;
