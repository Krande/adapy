// Narrow the change marks to the rows one change touched.

import React from "react";
import type { AssetView } from "@/assets/assetView";
import { changeOwners, subjectsByOwner } from "@/assets/rowFacts";

/** The "changed by" filter -- offered ONLY when `view.hasChangeOwners`
 *  (at least one loaded manifest carries a `publishedBy` or `sourceActor`);
 *  otherwise this renders nothing, not a disabled control, because a filter
 *  over zero owners is a dead end dressed up as an affordance. Narrows to a
 *  flat clickable list rather than pruning the tree itself -- the same choice
 *  `Orphans` below makes for the same reason: an owner is a property of a
 *  SUBJECT, not a shape the hierarchy needs to know about, and jumping
 *  `select()` to a match is enough to act on it. */
export const ChangedByFilter: React.FC<{ view: AssetView; selected: string | null; onSelect: (id: string) => void }> = ({
    view,
    selected,
    onSelect,
}) => {
    const [owner, setOwner] = React.useState<string>("");
    if (!view.hasChangeOwners) return null;
    const owners = changeOwners(view);
    const matches = owner ? subjectsByOwner(view, owner) : [];
    return (
        // Inline in the chip row under the search box: one row for every narrowing of the view.
        <div className="contents">
            <select
                aria-label="Changed by"
                className="h-6 rounded-full border border-gray-600 bg-gray-800 px-2 text-[11px] text-gray-200 hover:bg-gray-700 max-w-[60%] truncate"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
            >
                <option value="">Changed by: anyone</option>
                {owners.map((o) => (
                    <option key={o.id} value={o.id}>
                        {o.display ? `${o.display} (${o.id})` : o.id}
                    </option>
                ))}
            </select>
            {owner && (
                <span className="text-[10px] text-gray-400 truncate">
                    {matches.length} subject(s)
                    {matches.map((id) => (
                        <button
                            key={id}
                            type="button"
                            className={`ml-1 rounded-sm px-1 ${selected === id ? "bg-blue-700 text-white" : "bg-gray-700 hover:text-white"}`}
                            onClick={() => onSelect(id)}
                        >
                            {id}
                        </button>
                    ))}
                </span>
            )}
        </div>
    );
};
