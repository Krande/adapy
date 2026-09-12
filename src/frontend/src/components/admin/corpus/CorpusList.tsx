import React from "react";
import type {Corpus} from "@/services/viewerApi";

// Corpus list — full-width on mobile (collapses when a corpus is selected),
// sidebar on md+. Mobile scroll wiring matches AuditRunsTab: needs flex-1
// min-h-0 in the column-flex context so overflow-auto can actually shrink
// below content size. md:flex-none restores the fixed sidebar sizing in the
// row layout.
const CorpusList: React.FC<{
    corpora: Corpus[];
    selectedSlug: string | null;
    onSelect: (slug: string) => void;
    onArchive: (slug: string) => Promise<void>;
    /** Only matters on mobile: the list hides once a corpus is selected. */
    visible: boolean;
    listError: string | null;
}> = ({corpora, selectedSlug, onSelect, onArchive, visible, listError}) => (
    <div className={
        "md:w-72 md:shrink-0 md:flex-none md:border-r md:border-b-0 " +
        "flex-1 min-h-0 border-b border-gray-800 overflow-auto " +
        (visible ? "block" : "hidden md:block")
    }>
        {listError && (
            <div className="text-xs text-red-400 px-3 py-2">{listError}</div>
        )}
        {corpora.length === 0 && !listError && (
            <div className="text-xs text-gray-500 italic px-3 py-4">
                No corpora yet. Use the form above to create one.
            </div>
        )}
        <ul className="text-xs">
            {corpora.map((c) => {
                const active = c.slug === selectedSlug;
                return (
                    <li
                        key={c.id}
                        onClick={() => onSelect(c.slug)}
                        className={
                            "px-3 py-2 border-b border-gray-800 cursor-pointer " +
                            (active ? "bg-blue-900/40" : "hover:bg-gray-800/40")
                        }
                    >
                        <div className="flex justify-between items-baseline gap-2">
                            <span className="font-mono text-gray-200 truncate">
                                {c.slug}
                            </span>
                            <button
                                type="button"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    void onArchive(c.slug);
                                }}
                                className="text-red-400 hover:text-red-300 text-[10px] shrink-0"
                            >
                                archive
                            </button>
                        </div>
                        <div className="text-gray-400 text-[11px] mt-0.5 truncate">
                            {c.name}
                        </div>
                        {c.description && (
                            <div className="text-gray-500 text-[10px] mt-0.5 truncate" title={c.description}>
                                {c.description}
                            </div>
                        )}
                    </li>
                );
            })}
        </ul>
    </div>
);

export default CorpusList;
