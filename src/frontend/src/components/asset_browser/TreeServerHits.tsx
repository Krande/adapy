// Under the Sources tree while its search box is on: what the SERVER's index found for the term
// that the tree has not loaded yet (`@/assets/treeServerSearch`). The box itself filters only the
// rows held, and the tree loads a level at a time, so a deep node in an unopened branch is found
// here instead. A click walks the tree to it and selects it, as the search panel's "Show in tree"
// does (`revealSearchHit`).
//
// Quiet by design: nothing at all while there is no index for the collection, one grey line when
// the server search fails or times out, and every request for an older term is cancelled.

import React, { useEffect, useMemo, useState } from "react";

import { isSearchTerm } from "@/assets/rowFacts";
import { shortName, totalCount } from "@/assets/attributeSearch";
import {
    TREE_SERVER_SEARCH_DEBOUNCE_MS,
    hitPathText,
    serverSearchNote,
    treeSearchPartitions,
    treeServerQuery,
    unloadedHits,
} from "@/assets/treeServerSearch";
import { attributeSearchApi, type SearchCollection, type SearchHit } from "@/services/api/attributeSearch";

type Answer = { term: string; hits: SearchHit[]; total: number } | { term: string; error: string };

const TreeServerHits: React.FC<{
    scope: string;
    /** The collection the tree shows. */
    collection: string | null;
    /** The search box's text, as typed. */
    term: string;
    /** Whether the tree holds a row of this id -- such a hit is the tree's own to show. */
    held: (nodeId: string) => boolean;
}> = ({ scope, collection, term, held }) => {
    // What is indexed in this scope: read per scope and collection; no index (or no database) is
    // simply nothing to ask.
    const [indexed, setIndexed] = useState<readonly SearchCollection[]>([]);
    useEffect(() => {
        let live = true;
        attributeSearchApi
            .searchCollections(scope)
            .then((c) => live && setIndexed(c))
            .catch(() => live && setIndexed([]));
        return () => {
            live = false;
        };
    }, [scope, collection]);
    const partitions = useMemo(() => treeSearchPartitions(indexed, collection), [indexed, collection]);
    const partsKey = partitions.join("\u0000");

    const [answer, setAnswer] = useState<Answer | null>(null);
    useEffect(() => {
        setAnswer(null);
        const query = isSearchTerm(term) ? treeServerQuery(term, partitions) : null;
        if (!query) return;
        const ctl = new AbortController();
        const t = setTimeout(() => {
            attributeSearchApi
                .searchAssetAttributes(scope, query, ctl.signal)
                .then((res) => !ctl.signal.aborted && setAnswer({ term, hits: res.rows, total: totalCount(res.counts) }))
                .catch((e) => !ctl.signal.aborted && setAnswer({ term, error: serverSearchNote(e) }));
        }, TREE_SERVER_SEARCH_DEBOUNCE_MS);
        return () => {
            clearTimeout(t);
            ctl.abort();
        };
        // `partitions` changes exactly when its key does.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope, term, partsKey]);

    const [revealing, setRevealing] = useState<{ id: string; note: string | null } | null>(null);
    useEffect(() => setRevealing(null), [term]);

    if (!answer || !isSearchTerm(term)) return null;
    if ("error" in answer) {
        return <div className="shrink-0 border-t border-gray-700/70 px-2 py-1 text-[11px] text-gray-500">{answer.error}</div>;
    }
    // Re-derived as the tree loads: a hit walked to drops out of the list.
    const hits = unloadedHits(answer.hits, held);
    if (!hits.length) return null;
    const more = answer.total > answer.hits.length;

    const reveal = async (hit: SearchHit) => {
        setRevealing({ id: hit.node_id, note: null });
        try {
            // Imported when used: the reveal reaches back into the tab for its loads.
            const { revealSearchHit } = await import("@/utils/tree_view/revealSearchHit");
            const out = await revealSearchHit(hit);
            const note =
                out.status === "not-found"
                    ? out.message
                    : out.status === "ancestor"
                      ? "only an ancestor is in the tree -- selected that instead"
                      : null;
            setRevealing(note ? { id: hit.node_id, note } : null);
        } catch (e) {
            setRevealing({ id: hit.node_id, note: e instanceof Error ? e.message : String(e) });
        }
    };

    return (
        <div className="shrink-0 border-t border-gray-700/70 px-2 py-1 text-xs" data-testid="tree-server-hits">
            <div
                className="text-[11px] uppercase tracking-wide text-gray-400"
                title={`Found in the scope's index by name or ref, in branches the tree has not opened yet${more ? `; ${answer.total.toLocaleString()} matches in all, the first ${answer.hits.length} asked for` : ""}`}
            >
                Not loaded yet — {hits.length}
                {more ? "+" : ""} found
            </div>
            <ul className="max-h-40 overflow-y-auto">
                {hits.map((h) => {
                    const path = hitPathText(h);
                    const busy = revealing?.id === h.node_id && !revealing.note;
                    return (
                        <li key={`${h.provider}/${h.node_id}`}>
                            <button
                                type="button"
                                className="w-full min-w-0 rounded px-1 py-0.5 text-left hover:bg-gray-700/60 disabled:opacity-50"
                                disabled={busy}
                                title={`${path}${h.source_ref ? `\nref ${h.source_ref}` : ""}${h.kind ? `\n${h.kind}` : ""}\nClick to open the tree at it and select it`}
                                onClick={() => void reveal(h)}
                            >
                                <span className="block truncate font-mono text-gray-100">
                                    {shortName(h.full_name || h.node_id)}
                                    {h.kind && <span className="ml-1 font-sans text-[10px] text-gray-500">{h.kind}</span>}
                                    {busy && <span className="ml-1 font-sans text-[10px] text-gray-400">opening…</span>}
                                </span>
                                <span className="block truncate text-[10px] text-gray-500">{path}</span>
                            </button>
                            {revealing?.id === h.node_id && revealing.note && (
                                <div className="px-1 text-[10px] text-amber-300">{revealing.note}</div>
                            )}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
};

export default TreeServerHits;
