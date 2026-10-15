// The gallery's folder walk, annotated: where the file on screen came from, and a strip of its
// neighbours to jump between.
//
// PROVENANCE. From the file's `/files/meta` record: its project (collection) and node name, and
// "Go to source tree" to that node -- the hit's own for a hit inside a merged file -- switching to
// the node's scope first when it is another one (`revealInSourceScope`).
//
// THUMBNAILS. Each written file may have a `.thumb.png` beside it; the listing hides those, so they
// are fetched by key (with auth, hence object URLs). Only a window around the current stop is
// drawn -- an export can hold two thousand files -- and one without a picture gets its name as a
// chip. A merged file is one stop PER HIT inside it (`galleryStops`), each with that hit's own
// thumbnail, so the strip shows the hits rather than one picture of the whole collection.
//
// PAGES. The strip (or grid) holds one project at a time (`projectPages`); stepping past its last
// stop turns to the next project's page, and ‹ › / PageUp / PageDown flip whole projects.

import React, {useEffect, useMemo, useRef, useState} from "react";

import {
    hitFromJoint,
    hitFromMeta,
    projectPages,
    stopOfAdjacentProject,
    stripWindow,
    thumbSidecarKey,
    type GalleryStop,
} from "@/assets/searchExport";
import {searchExportApi, type ExportDoc, type FileMeta} from "@/services/api/searchExport";
import {useGalleryStore} from "@/state/galleryStore";
import {revealInSourceScope} from "@/utils/tree_view/revealInSourceScope";

import {RequestDesignGeometryButton} from "./RequestDesignGeometryButton";

/** Thumbnails drawn: one row, or the expanded grid. */
const STRIP_SIZE = 15;
const GRID_SIZE = 90;

const base = (k: string) => k.slice(k.lastIndexOf("/") + 1);
const stem = (k: string) => base(k).replace(/\.[^.]+$/, "");

/** A thumbnail, or the file's name when there is none. */
const Thumb: React.FC<{url: string | null | undefined; name: string; current: boolean; onClick: () => void}> = ({url, name, current, onClick}) => (
    <button
        type="button"
        data-current={current || undefined}
        onClick={onClick}
        title={name}
        className={
            "shrink-0 h-12 w-16 overflow-hidden rounded-sm border text-[9px] leading-tight " +
            (current ? "border-blue-400" : "border-gray-700 hover:border-gray-400")
        }
    >
        {url ? (
            <img src={url} alt={name} className="h-full w-full object-cover" />
        ) : (
            <span className="block h-full w-full break-all bg-gray-800 p-0.5 text-left text-gray-300">{stem(name)}</span>
        )}
    </button>
);

const GalleryProvenance: React.FC<{
    scope: string;
    stops: readonly GalleryStop[];
    index: number;
    doc: Pick<ExportDoc, "files"> | null;
    onJump: (index: number) => void;
}> = ({scope, stops, index, doc, onJump}) => {
    const stop = stops[index] ?? null;
    const current = stop?.file ?? null;
    const joint = stop?.joint ?? null;

    // ---- Provenance of the file on screen ------------------------------------
    const [meta, setMeta] = useState<FileMeta | null>(null);
    const [metaError, setMetaError] = useState<string | null>(null);
    useEffect(() => {
        setMeta(null);
        setMetaError(null);
        if (!current) return;
        const ctl = new AbortController();
        searchExportApi
            .fileMeta(scope, current, ctl.signal)
            .then(setMeta)
            .catch((e) => {
                if (!ctl.signal.aborted) setMetaError(e instanceof Error ? e.message : String(e));
            });
        return () => ctl.abort();
    }, [scope, current]);

    // A hit inside a merged file is found by its own path; a file of one hit by its provenance.
    const hit = useMemo(() => (joint ? hitFromJoint(joint, meta) : hitFromMeta(meta)), [joint, meta]);
    // The tree shows the CURRENT scope; a node of another scope is revealed after switching to it
    // (`revealInSourceScope`), or the reason it cannot be is shown.
    const sourceScope = meta?.source?.scope ?? scope;
    const revealable = !!hit;
    const [revealNote, setRevealNote] = useState<string | null>(null);
    useEffect(() => setRevealNote(null), [current, joint]);
    const reveal = async () => {
        if (!hit) return;
        setRevealNote(sourceScope !== scope ? `switching to ${sourceScope}…` : "opening…");
        try {
            const out = await revealInSourceScope(hit, sourceScope);
            if (out.status === "not-found") setRevealNote(out.message);
            else if (out.status === "ancestor") setRevealNote("only an ancestor is in the tree -- shown that instead");
            else setRevealNote(null);
        } catch (e) {
            setRevealNote(e instanceof Error ? e.message : String(e));
        }
    };

    // ---- Thumbnails around it --------------------------------------------------
    // A stop with no word on its thumbnail (a file the doc does not list) looks beside the file.
    const thumbOf = (s: GalleryStop) => (s.thumb === undefined ? thumbSidecarKey(s.file) : s.thumb);
    const expanded = useGalleryStore((s) => s.expanded);
    const toggleExpanded = useGalleryStore((s) => s.toggleExpanded);
    const gridHeight = useGalleryStore((s) => s.gridHeight);
    // One project per page: the strip shows the current project's stops only.
    const pages = useMemo(() => projectPages(stops), [stops]);
    const pageAt = Math.max(0, pages.findIndex((p) => index >= p.lo && index < p.hi));
    const page = pages[pageAt] ?? {project: null, lo: 0, hi: stops.length};
    const [wlo, whi] = stripWindow(page.hi - page.lo, index - page.lo, expanded ? GRID_SIZE : STRIP_SIZE);
    const [lo, hi] = [page.lo + wlo, page.lo + whi];
    const windowStops = stops.slice(lo, hi);
    const flip = (delta: 1 | -1) => onJump(stopOfAdjacentProject(stops, index, delta));
    // Keep the current thumbnail in view as the walk moves (only then: scrolling by hand stays put).
    const stripRef = useRef<HTMLDivElement | null>(null);
    useEffect(() => {
        const strip = stripRef.current;
        const el = strip?.querySelector<HTMLElement>("[data-current]");
        if (!strip || !el) return;
        const r = el.getBoundingClientRect();
        const box = strip.getBoundingClientRect();
        if (r.left < box.left) strip.scrollLeft -= box.left - r.left;
        else if (r.right > box.right) strip.scrollLeft += r.right - box.right;
        if (r.top < box.top) strip.scrollTop -= box.top - r.top;
        else if (r.bottom > box.bottom) strip.scrollTop += r.bottom - box.bottom;
    }, [index, expanded]);
    const windowKeys = windowStops.map((s) => s.id);
    // stop id -> object URL (null = none); kept across steps, released when the walk goes away.
    const [urls, setUrls] = useState<ReadonlyMap<string, string | null>>(new Map());
    const asked = useRef(new Set<string>());
    const live = useRef(new Set<string>());
    // A different scope or doc means different thumbnails: forget what was asked. Declared before
    // the fetch below so, on the same change, it runs first.
    useEffect(() => {
        asked.current.clear();
        setUrls(new Map());
    }, [scope, doc]);
    useEffect(() => {
        const ctl = new AbortController();
        for (const s of windowStops) {
            const k = s.id;
            if (asked.current.has(k)) continue;
            asked.current.add(k);
            const t = thumbOf(s);
            if (!t) {
                setUrls((m) => new Map(m).set(k, null));
                continue;
            }
            searchExportApi
                .blobObjectUrl(scope, t, ctl.signal)
                .then((u) => {
                    if (u) live.current.add(u);
                    setUrls((m) => new Map(m).set(k, u));
                })
                .catch(() => {
                    asked.current.delete(k); // aborted or a blip: ask again when it is in view next
                });
        }
        return () => ctl.abort();
        // windowStops is derived from (stops, index); keyed on its content.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, windowKeys.join("\n")]);
    useEffect(
        () => () => {
            for (const u of live.current) URL.revokeObjectURL(u);
            live.current.clear();
        },
        [],
    );
    const src = meta?.source;
    const project = src?.collection ?? null;
    const name = joint ? (joint.full_name ?? joint.name) : (src?.full_name ?? null);
    const noReveal = joint
        ? "This export predates per-hit source paths -- export again to enable it"
        : "The provenance does not name the source node";
    return (
        <div className="flex min-w-0 flex-col gap-1 border-t border-gray-700 pt-1 text-[11px]">
            <div className="flex items-center justify-between gap-2 text-[10px] text-gray-500">
                {pages.length > 1 ? (
                    <span className="flex min-w-0 items-center gap-1">
                        <button type="button" onClick={() => flip(-1)} title="Previous project (PageUp)" className="rounded-sm px-1 hover:bg-gray-700 hover:text-gray-200">
                            ‹
                        </button>
                        <span className="truncate text-gray-300" title={page.project ?? ""}>
                            {page.project ?? "other files"}
                        </span>
                        <span className="tabular-nums">
                            ({pageAt + 1}/{pages.length})
                        </span>
                        <button type="button" onClick={() => flip(1)} title="Next project (PageDown)" className="rounded-sm px-1 hover:bg-gray-700 hover:text-gray-200">
                            ›
                        </button>
                        <span className="tabular-nums">
                            · {wlo + 1}–{whi} of {page.hi - page.lo}
                        </span>
                    </span>
                ) : (
                    <span className="tabular-nums">{stops.length ? `${lo + 1}–${hi} of ${stops.length}` : ""}</span>
                )}
                <button type="button" onClick={toggleExpanded} className="rounded-sm px-1 hover:bg-gray-700 hover:text-gray-200">
                    {expanded ? "Collapse ▴" : "Expand ▾"}
                </button>
            </div>
            <div
                ref={stripRef}
                className={expanded ? "flex flex-wrap content-start gap-1 overflow-y-auto pb-0.5" : "flex gap-1 overflow-x-auto pb-0.5"}
                style={expanded ? {maxHeight: gridHeight} : undefined}
            >
                {windowStops.map((s, n) => (
                    <Thumb
                        key={s.id}
                        url={urls.get(s.id)}
                        name={s.joint ? s.joint.name : s.file}
                        current={lo + n === index}
                        onClick={() => onJump(lo + n)}
                    />
                ))}
            </div>
            {metaError && <div className="text-amber-300 truncate" title={metaError}>provenance: {metaError}</div>}
            {(project || name) && (
                <div className="space-y-0.5 text-gray-300">
                    <div className="truncate">
                        <span className="text-gray-500">Project: </span>
                        {project ?? "?"}
                    </div>
                    <div className="truncate font-mono" title={name ?? ""}>
                        <span className="font-sans text-gray-500">Name: </span>
                        {name ?? "?"}
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            disabled={!revealable}
                            onClick={() => void reveal()}
                            title={
                                !revealable
                                    ? noReveal
                                    : sourceScope !== scope
                                      ? `Switch to scope ${sourceScope} and open the Sources tree at this node`
                                      : "Open the Sources tree at this node"
                            }
                            className="rounded-sm border border-gray-600 px-1.5 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                        >
                            Go to source tree
                        </button>
                        {current && !joint && meta && <RequestDesignGeometryButton scope={scope} fileKey={current} meta={meta} />}
                        {revealNote && <span className="truncate text-amber-300" title={revealNote}>{revealNote}</span>}
                    </div>
                </div>
            )}
        </div>
    );
};

export default GalleryProvenance;
