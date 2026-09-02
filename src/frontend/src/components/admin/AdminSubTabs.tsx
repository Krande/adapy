import React from "react";

import type {SubTabSpec} from "./adminTabs";

// The sub-tab strip shared by the admin tabs that group several panels.
//
// Extracted once three tabs needed it — Audit, Performance and Procedural —
// because the alternative is three copies of the same active/inactive styling
// drifting apart, and the strip is the one thing that has to look identical
// everywhere or the grouping reads as three unrelated inventions.

interface AdminSubTabsProps<Id extends string> {
    tabs: readonly SubTabSpec<Id>[];
    active: Id;
    onSelect: (id: Id) => void;
}

export function AdminSubTabs<Id extends string>({tabs, active, onSelect}: AdminSubTabsProps<Id>) {
    return (
        <div className="flex gap-1 px-3 sm:px-4 pt-2 text-xs border-b border-gray-800 shrink-0 overflow-x-auto">
            {tabs.map((t) => {
                const on = t.id === active;
                return (
                    <button
                        key={t.id}
                        className={
                            "px-3 py-1.5 rounded-t-sm whitespace-nowrap border-b-2 " +
                            (on
                                ? "bg-gray-800 text-white border-blue-600"
                                : "text-gray-400 hover:text-gray-200 border-transparent")
                        }
                        onClick={() => onSelect(t.id)}
                        aria-current={on ? "page" : undefined}
                    >
                        {t.label}
                        {t.badge != null && (
                            <span className={"ml-1.5 tabular-nums " + (on ? "text-blue-200" : "text-gray-500")}>
                                {t.badge}
                            </span>
                        )}
                    </button>
                );
            })}
        </div>
    );
}

export default AdminSubTabs;
