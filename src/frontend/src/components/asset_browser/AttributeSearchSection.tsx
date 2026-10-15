// "Search all collections (attributes)": one launcher row in the Sources tab, below "Request from a
// provider", for the attribute search's own floating window (`AttributeSearchWindow`; the viewer's
// top bar opens it too).
//
// A ROW, NOT THE SEARCH. The search spans every collection of every provider in the scope -- a
// button beside the tree's collection picker read as if it searched that one -- and its form and
// results need more room than the drawer has. So the drawer keeps only this: what the last search
// found, and a way to the window.

import React from "react";

import { totalCount } from "@/assets/attributeSearch";
import { useAttributeSearchStore } from "@/state/attributeSearchStore";
import { SEARCH_WINDOW_TITLE, useSearchWindowStore } from "@/state/searchWindowStore";

const AttributeSearchSection: React.FC = () => {
    const open = useSearchWindowStore((s) => s.open);
    const { show, hide } = useSearchWindowStore.getState();
    const results = useAttributeSearchStore((s) => s.results);
    const total = results ? totalCount(results.counts) : null;

    return (
        <div className="flex items-center gap-2">
            <span
                className="text-gray-400 truncate"
                title="Finds nodes by the attributes their providers indexed, across every collection of every provider in this scope"
            >
                {SEARCH_WINDOW_TITLE}
            </span>
            {total !== null && (
                <span className="text-gray-500 shrink-0" title="Matches of the last search">
                    · {total.toLocaleString()} {total === 1 ? "match" : "matches"}
                </span>
            )}
            <button
                type="button"
                className="ml-auto shrink-0 h-6 px-2 rounded-md border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700"
                aria-pressed={open}
                onClick={open ? hide : show}
            >
                {open ? "Close" : "Open"}
            </button>
        </div>
    );
};

export default AttributeSearchSection;
