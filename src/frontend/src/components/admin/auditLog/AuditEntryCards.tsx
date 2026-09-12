import React from "react";
import type {AuditEntry} from "@/services/viewerApi";
import {WasmBadge} from "./columns";
import {formatTs, hasDetails, isWasmEntry, shortSub, statusClass, userTooltip} from "./format";

// Mobile layout: card-per-entry, so a 320px viewport stays readable without
// horizontal scrolling.
const AuditEntryCards: React.FC<{
    entries: AuditEntry[];
    onDetails: (entry: AuditEntry) => void;
}> = ({entries, onDetails}) => (
    <ul className="sm:hidden divide-y divide-gray-800">
        {entries.map((e) => (
            <li key={e.id} className="px-3 py-2 text-xs">
                <div className="flex items-baseline justify-between gap-2">
                    <span className="font-medium">
                        <span className="font-mono text-gray-400 mr-1">#{e.id}</span>
                        {e.action}
                    </span>
                    <span className={statusClass(e.status) + " text-[11px]"}>
                        {e.status || ""}
                    </span>
                </div>
                <div className="text-gray-400 mt-0.5">
                    {formatTs(e.ts)} · {e.scope_kind}
                    {e.scope_id ? `:${shortSub(e.scope_id)}` : ""}
                </div>
                {e.key && (
                    <div className="text-gray-300 mt-1 break-all" title={e.key}>
                        {e.key}
                        {e.target_format ? (
                            <span className="text-gray-400"> → {e.target_format}</span>
                        ) : null}
                        {isWasmEntry(e) && <WasmBadge/>}
                    </div>
                )}
                {e.user_sub && (
                    <div className="text-gray-500 mt-0.5" title={userTooltip(e)}>
                        by {e.user_display_name || shortSub(e.user_sub)}
                    </div>
                )}
                {e.error && (
                    <div className="text-red-300 mt-1 break-all flex items-start gap-1" title={e.error}>
                        <span className="flex-1">{e.error}</span>
                        <button
                            type="button"
                            className="shrink-0 inline-flex items-center justify-center w-4 h-4 rounded-full border border-gray-500 text-gray-300 hover:text-white hover:border-white text-[10px] font-bold leading-none mt-0.5 no-drag"
                            onClick={() => onDetails(e)}
                            aria-label="Show details"
                        >
                            i
                        </button>
                    </div>
                )}
                {!e.error && hasDetails(e) && (
                    <div className="mt-1">
                        <button
                            type="button"
                            className="text-[10px] text-gray-400 hover:text-white underline no-drag"
                            onClick={() => onDetails(e)}
                        >
                            details
                        </button>
                    </div>
                )}
            </li>
        ))}
    </ul>
);

export default AuditEntryCards;
