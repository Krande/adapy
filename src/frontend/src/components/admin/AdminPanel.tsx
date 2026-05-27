import React, {useEffect, useState} from "react";
import {AdminTab} from "@/state/adminPanelStore";
import {useMeStore} from "@/state/meStore";
import AuditLogTab from "./AuditLogTab";
import AuditRunsTab from "./AuditRunsTab";
import CliTokenButton from "./CliTokenButton";
import ConversionSettingsTab from "./ConversionSettingsTab";
import CorpusTab from "./CorpusTab";
import IssueTargetTab from "./IssueTargetTab";
import PerformanceTab from "./PerformanceTab";
import ProjectsTab from "./ProjectsTab";
import SchedulesTab from "./SchedulesTab";
import StorageTab from "./StorageTab";
import WorkersTab from "./WorkersTab";

// Path-mounted admin page (``/admin``) — full-screen on every
// viewport, with the active tab serialised into the URL hash so a
// browser refresh stays on the same panel. Previously a draggable
// modal overlay on the viewer; the page form survives refreshes
// (the modal mode didn't) and gives every tab room to lay out on
// mobile.
//
// Sub-tabs: ``/admin#audit_runs`` lands directly on the regression
// sweep panel; ``/admin`` with no hash uses ``audit`` as the
// default. Anchor links elsewhere in the SPA can deep-link to a
// specific tab without touching state, e.g. the conversion-toast
// info icon hard-codes ``/admin#audit``.

const VALID_TABS = new Set<AdminTab>([
    "audit", "audit_runs", "schedules", "issues", "performance",
    "corpus", "projects", "storage", "workers", "conversion",
]);

function readTabFromHash(): AdminTab {
    const raw = (window.location.hash || "").replace(/^#/, "").trim() as AdminTab;
    return VALID_TABS.has(raw) ? raw : "audit";
}

const AdminPanel: React.FC = () => {
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [tab, setTab] = useState<AdminTab>(() => readTabFromHash());

    // Two-way bind ``tab`` to ``window.location.hash`` so reloads stay
    // on the selected tab AND back/forward navigation works inside
    // the page. setTab writes; popstate / hashchange reads back.
    useEffect(() => {
        const onChange = () => setTab(readTabFromHash());
        window.addEventListener("hashchange", onChange);
        return () => window.removeEventListener("hashchange", onChange);
    }, []);

    useEffect(() => {
        const desired = `#${tab}`;
        if (window.location.hash !== desired) {
            // ``replaceState`` so each tab switch doesn't pollute the
            // back-button history with a long chain of admin tabs.
            window.history.replaceState(null, "", desired);
        }
    }, [tab]);

    if (!isAdmin) {
        // Non-admin landed on /admin directly (or auth dropped them
        // mid-session). Render a clear refusal rather than a blank
        // screen so a confused user knows what's going on.
        return (
            <div className="min-h-screen w-full flex items-center justify-center bg-gray-900 text-white">
                <div className="max-w-sm text-center space-y-3 px-6">
                    <h1 className="text-lg font-semibold">Admin only</h1>
                    <p className="text-sm text-gray-400">
                        Your account isn't a member of the admin group on
                        this deployment.
                    </p>
                    <a
                        href="/"
                        className="inline-block text-sm text-blue-400 hover:text-blue-300"
                    >
                        ← back to viewer
                    </a>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen flex flex-col bg-gray-900 text-white">
            <header className="flex items-center gap-2 border-b border-gray-800 px-3 py-2 sm:px-4 shrink-0">
                <div className="flex-1 min-w-0 overflow-x-auto flex gap-1 text-sm">
                    <TabButton active={tab === "audit"} onClick={() => setTab("audit")}>
                        Audit Log
                    </TabButton>
                    <TabButton active={tab === "audit_runs"} onClick={() => setTab("audit_runs")}>
                        Audit Runs
                    </TabButton>
                    <TabButton active={tab === "schedules"} onClick={() => setTab("schedules")}>
                        Schedules
                    </TabButton>
                    <TabButton active={tab === "issues"} onClick={() => setTab("issues")}>
                        Issues
                    </TabButton>
                    <TabButton active={tab === "performance"} onClick={() => setTab("performance")}>
                        Performance
                    </TabButton>
                    <TabButton active={tab === "corpus"} onClick={() => setTab("corpus")}>
                        Corpus
                    </TabButton>
                    <TabButton active={tab === "projects"} onClick={() => setTab("projects")}>
                        Projects
                    </TabButton>
                    <TabButton active={tab === "storage"} onClick={() => setTab("storage")}>
                        Storage
                    </TabButton>
                    <TabButton active={tab === "workers"} onClick={() => setTab("workers")}>
                        Workers
                    </TabButton>
                    <TabButton active={tab === "conversion"} onClick={() => setTab("conversion")}>
                        Conversion
                    </TabButton>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    <CliTokenButton/>
                    <a
                        href="/"
                        className="text-sm text-blue-400 hover:text-blue-300 px-2 py-1"
                        title="Back to viewer"
                    >
                        ← viewer
                    </a>
                </div>
            </header>
            <main className="flex-1 overflow-hidden">
                {tab === "audit" && <AuditLogTab/>}
                {tab === "audit_runs" && <AuditRunsTab/>}
                {tab === "schedules" && <SchedulesTab/>}
                {tab === "issues" && <IssueTargetTab/>}
                {tab === "performance" && <PerformanceTab/>}
                {tab === "corpus" && <CorpusTab/>}
                {tab === "projects" && <ProjectsTab/>}
                {tab === "storage" && <StorageTab/>}
                {tab === "workers" && <WorkersTab/>}
                {tab === "conversion" && <ConversionSettingsTab/>}
            </main>
        </div>
    );
};

const TabButton: React.FC<{
    active: boolean;
    onClick: () => void;
    children: React.ReactNode;
}> = ({active, onClick, children}) => (
    <button
        className={
            "px-3 py-2 rounded-sm text-sm whitespace-nowrap " +
            (active ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800")
        }
        onClick={onClick}
    >
        {children}
    </button>
);

export default AdminPanel;
