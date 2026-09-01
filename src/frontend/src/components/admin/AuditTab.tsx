import React, {useState} from "react";

import AuditFilterBar from "./AuditFilterBar";
import AuditLogTab from "./AuditLogTab";
import AuditOverviewTab from "./AuditOverviewTab";
import AuditRunsTab from "./AuditRunsTab";
import CorpusTab from "./CorpusTab";
import SchedulesTab from "./SchedulesTab";

// The Audit tab.
//
// Audit Log, Audit Runs, Corpus and Schedules used to be four sibling entries
// in the admin tab strip. They are one feature: CorpusTab and SchedulesTab
// each open by describing themselves as M3 and M4 "of the audit panel design".
// Spread across the strip they read as unrelated, and the strip itself had
// grown past what fits on a laptop, let alone a phone.
//
// Folding them in also lets a single filter drive the job-shaped ones, which is
// what makes the Overview's counts clickable: narrow, see the number, click it,
// land on those exact rows.

export type AuditSubTab = "overview" | "log" | "runs" | "corpora" | "schedules";

export const AUDIT_SUB_TABS: {id: AuditSubTab; label: string}[] = [
    {id: "overview", label: "Overview"},
    {id: "log", label: "Log"},
    {id: "runs", label: "Runs"},
    {id: "corpora", label: "Corpora"},
    {id: "schedules", label: "Schedules"},
];

/** Sub-tabs the shared job filter applies to.
 *
 * Corpora and Schedules manage definitions — a corpus is a set of files, a
 * schedule is a cron row. Neither has a job status, target format or user to
 * narrow by, so the bar comes off the screen there entirely rather than
 * sitting disabled: four dead controls invite a click and answer nothing.
 * The filter itself is kept, so leaving and coming back restores it. */
const FILTERABLE: ReadonlySet<AuditSubTab> = new Set<AuditSubTab>(["overview", "log", "runs"]);

const SubTabButton: React.FC<{
    active: boolean;
    onClick: () => void;
    children: React.ReactNode;
}> = ({active, onClick, children}) => (
    <button
        className={
            "px-3 py-1.5 rounded-t-sm whitespace-nowrap border-b-2 " +
            (active
                ? "bg-gray-800 text-white border-blue-600"
                : "text-gray-400 hover:text-gray-200 border-transparent")
        }
        onClick={onClick}
        aria-current={active ? "page" : undefined}
    >
        {children}
    </button>
);

const AuditTab: React.FC<{initialSubTab?: AuditSubTab}> = ({initialSubTab}) => {
    const [sub, setSub] = useState<AuditSubTab>(initialSubTab ?? "overview");
    const showFilter = FILTERABLE.has(sub);

    return (
        <div className="h-full flex flex-col">
            {showFilter && <AuditFilterBar/>}

            <div className="flex gap-1 px-3 sm:px-4 pt-2 text-xs border-b border-gray-800 shrink-0 overflow-x-auto">
                {AUDIT_SUB_TABS.map((t) => (
                    <SubTabButton key={t.id} active={sub === t.id} onClick={() => setSub(t.id)}>
                        {t.label}
                    </SubTabButton>
                ))}
            </div>

            <div className="flex-1 min-h-0 overflow-hidden">
                {sub === "overview" && <AuditOverviewTab onDrillDown={() => setSub("log")}/>}
                {sub === "log" && <AuditLogTab/>}
                {sub === "runs" && <AuditRunsTab/>}
                {sub === "corpora" && <CorpusTab/>}
                {sub === "schedules" && <SchedulesTab/>}
            </div>
        </div>
    );
};

export default AuditTab;
