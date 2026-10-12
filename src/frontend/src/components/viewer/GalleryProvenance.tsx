// The gallery's folder walk, annotated: where the file on screen came from, and a strip of its
// neighbours to jump between.
//
// PROVENANCE. A file an export wrote carries where it came from -- a `.meta.json` sidecar, or the
// same facts inside the file itself -- served as one record by `/files/meta`: the provider's
// collection and node, its revision, the query that found it and what of it matched, how many of
// its elements came with attributes. The card shows that, and "Show in source tree" walks the
// Sources tree to the node it was exported from (`revealSearchHit`, the search panel's own reveal)
// -- switching to the node's scope first when it is another one, since the tree shows the current
// scope (`revealInSourceScope`); a scope the user cannot open is said instead.
// A file with no provenance (hand-uploaded) shows no card at all.
//
// THUMBNAILS. Each written file may have a `.thumb.png` beside it; the listing hides those, so they
// are fetched by key (with auth, hence object URLs). Only a window around the current file is
// drawn -- an export can hold two thousand files -- and a file without one gets its name as a chip.

import React, {useEffect, useMemo, useRef, useState} from "react";

import {compactQuery, hitFromMeta, thumbSidecarKey} from "@/assets/searchExport";
import {searchExportApi, type ExportDoc, type FileMeta} from "@/services/api/searchExport";
import {revealInSourceScope} from "@/utils/tree_view/revealInSourceScope";

const WINDOW = 6;

const base = (k: string) => k.slice(k.lastIndexOf("/") + 1);
const stem = (k: string) => base(k).replace(/\.[^.]+$/, "");

/** A thumbnail, or the file's name when there is none. */
const Thumb: React.FC<{url: string | null | undefined; name: string; current: boolean; onClick: () => void}> = ({url, name, current, onClick}) => (
    <button
        type="button"
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
    files: readonly string[];
    index: number;
    doc: ExportDoc | null;
    onJump: (index: number) => void;
}> = ({scope, files, index, doc, onJump}) => {
    const current = files[index] ?? null;

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

    const hit = useMemo(() => hitFromMeta(meta), [meta]);
    // The tree shows the CURRENT scope; a node of another scope is revealed after switching to it
    // (`revealInSourceScope`), or the reason it cannot be is shown.
    const sourceScope = meta?.source?.scope ?? scope;
    const revealable = !!hit;
    const [revealNote, setRevealNote] = useState<string | null>(null);
    useEffect(() => setRevealNote(null), [current]);
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
    const thumbOf = useMemo(() => {
        const m = new Map<string, string | null>();
        for (const f of doc?.files ?? []) m.set(f.file, f.thumb);
        return (k: string) => (m.has(k) ? m.get(k)! : thumbSidecarKey(k));
    }, [doc]);
    const lo = Math.max(0, index - WINDOW);
    const hi = Math.min(files.length, index + WINDOW + 1);
    const windowKeys = files.slice(lo, hi);
    // key -> object URL (null = none); kept across steps, released when the walk goes away.
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
        for (const k of windowKeys) {
            if (asked.current.has(k)) continue;
            asked.current.add(k);
            const t = thumbOf(k);
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
        // windowKeys is derived from (files, index); keyed on its content.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, windowKeys.join("\n"), thumbOf]);
    useEffect(
        () => () => {
            for (const u of live.current) URL.revokeObjectURL(u);
            live.current.clear();
        },
        [],
    );
    const matched = Object.entries(meta?.matched ?? {});
    const src = meta?.source;
    return (
        <div className="flex flex-col gap-1 border-t border-gray-700 pt-1 text-[11px]">
            <div className="flex gap-1 overflow-x-auto pb-0.5">
                {windowKeys.map((k, n) => (
                    <Thumb key={k} url={urls.get(k)} name={k} current={lo + n === index} onClick={() => onJump(lo + n)} />
                ))}
            </div>
            {metaError && <div className="text-amber-300 truncate" title={metaError}>provenance: {metaError}</div>}
            {meta && src && (
                <div className="space-y-0.5 text-gray-300">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-gray-400">{src.collection}</span>
                        <span className="font-mono break-all text-gray-100">{src.full_name}</span>
                    </div>
                    {matched.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                            {matched.map(([k, v]) => (
                                <span key={k} className="rounded-sm bg-gray-800 px-1">
                                    {k} = {String(v)}
                                </span>
                            ))}
                        </div>
                    )}
                    <div className="text-gray-500">
                        {src.revision ? `revision ${src.revision}` : "revision ?"}
                        {meta.attributes &&
                            ` · ${meta.attributes.elements} element${meta.attributes.elements === 1 ? "" : "s"}, ${meta.attributes.with_attrs} with attributes` +
                                (meta.attributes.without_attrs ? `, ${meta.attributes.without_attrs} without` : "")}
                        {meta.origin && meta.origin !== "sidecar" && ` · read from the ${meta.origin.toUpperCase()}`}
                    </div>
                    {meta.query && (
                        <div className="truncate text-gray-500" title={compactQuery(meta.query)}>
                            query: {compactQuery(meta.query)}
                        </div>
                    )}
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            disabled={!revealable}
                            onClick={() => void reveal()}
                            title={
                                !revealable
                                    ? "The provenance does not name the source node"
                                    : sourceScope !== scope
                                      ? `Switch to scope ${sourceScope} and open the Sources tree at the node this file was exported from`
                                      : "Open the Sources tree at the node this file was exported from"
                            }
                            className="rounded-sm border border-gray-600 px-1.5 py-0.5 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-default"
                        >
                            Show in source tree
                        </button>
                        {revealNote && <span className="truncate text-amber-300" title={revealNote}>{revealNote}</span>}
                    </div>
                </div>
            )}
        </div>
    );
};

export default GalleryProvenance;
