import React, {useEffect, useState} from "react";
import {AdminTab} from "@/state/adminPanelStore";
import {useMeStore} from "@/state/meStore";
import AuditLogTab from "./AuditLogTab";
import AuditRunsTab from "./AuditRunsTab";
import CliTokenButton from "./CliTokenButton";
import ConversionSettingsTab from "./ConversionSettingsTab";
import CorpusTab from "./CorpusTab";
import EquipmentAdminPanel from "./EquipmentAdminPanel";
import FrontendLoadsTab from "./FrontendLoadsTab";
import IssueTargetTab from "./IssueTargetTab";
import PerformanceTab from "./PerformanceTab";
import ProjectsTab from "./ProjectsTab";
import SchedulesTab from "./SchedulesTab";
import StorageTab from "./StorageTab";
import SystemAdminPanel from "./SystemAdminPanel";
import ProceduralEngineAdminPanel from "./ProceduralEngineAdminPanel";
import WorkersTab from "./WorkersTab";
import {Tabs} from "@/components/ui";

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

/**
 * The tab strip, as data.
 *
 * VALID_TABS below is derived from it rather than written out a second time: the two lists
 * disagreeing is how a tab ends up rendering but refusing to survive a reload, since the
 * hash parser would reject the very id the strip just set.
 */
const ADMIN_TABS: {id: AdminTab; label: string}[] = [
    {id: "audit", label: "Audit Log"},
    {id: "audit_runs", label: "Audit Runs"},
    {id: "schedules", label: "Schedules"},
    {id: "issues", label: "Issues"},
    {id: "performance", label: "Performance"},
    {id: "frontend_loads", label: "Frontend Loads"},
    {id: "corpus", label: "Corpus"},
    {id: "projects", label: "Projects"},
    {id: "storage", label: "Storage"},
    {id: "workers", label: "Workers"},
    {id: "conversion", label: "Conversion"},
    {id: "equipment", label: "Equipment"},
    {id: "system", label: "System"},
    {id: "engines", label: "Engines"},
];

const VALID_TABS = new Set<AdminTab>(ADMIN_TABS.map((t) => t.id));

function readTabFromHash(): AdminTab {
    const raw = (window.location.hash || "").replace(/^#/, "").trim() as AdminTab;
    return VALID_TABS.has(raw) ? raw : "audit";
}

interface AdminPanelProps {
    /** Mounted inside the viewer's floating panel host rather than the
     * full-page ``/admin`` route. Embedded mode keeps the URL alone
     * (no ``#audit`` hash scribbling from a draggable overlay) and
     * hides the "← viewer" link — the host's close button already
     * covers leaving the panel. */
    embedded?: boolean;
    /** Tab to open on in embedded mode (no URL hash to read from). Defaults to "audit". */
    initialTab?: AdminTab;
}

const AdminPanel: React.FC<AdminPanelProps> = ({embedded = false, initialTab}) => {
    const syncHash = !embedded;
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [tab, setTab] = useState<AdminTab>(() => (syncHash ? readTabFromHash() : (initialTab ?? "audit")));

    // Two-way bind ``tab`` to ``window.location.hash`` so reloads stay
    // on the selected tab AND back/forward navigation works inside
    // the page. setTab writes; popstate / hashchange reads back.
    useEffect(() => {
        if (!syncHash) return;
        const onChange = () => setTab(readTabFromHash());
        window.addEventListener("hashchange", onChange);
        return () => window.removeEventListener("hashchange", onChange);
    }, [syncHash]);

    useEffect(() => {
        if (!syncHash) return;
        const desired = `#${tab}`;
        if (window.location.hash !== desired) {
            // ``replaceState`` so each tab switch doesn't pollute the
            // back-button history with a long chain of admin tabs.
            window.history.replaceState(null, "", desired);
        }
    }, [syncHash, tab]);

    if (!isAdmin) {
        // Non-admin landed on /admin directly (or auth dropped them
        // mid-session). Render a clear refusal rather than a blank
        // screen so a confused user knows what's going on.
        return (
            <div className="min-h-screen w-full flex items-center justify-center bg-surface-0 text-white">
                <div className="max-w-sm text-center space-y-3 px-6">
                    <h1 className="text-lg font-semibold">Admin only</h1>
                    <p className="text-sm text-content-muted">
                        Your account isn't a member of the admin group on
                        this deployment.
                    </p>
                    <a
                        href="/"
                        className="inline-block text-sm text-accent pointer-fine:hover:text-accent"
                    >
                        ← back to viewer
                    </a>
                </div>
            </div>
        );
    }

    return (
        // ``h-full`` so the panel adapts to whatever container it's
        // mounted into. The full-page route wraps this in a
        // ``h-[100dvh]`` shell (true page mode); the in-viewer
        // ``InViewerPanelHost`` mounts it inside a draggable Rnd
        // whose explicit height drives the same flex chain. Either
        // way the nested ``flex-1 overflow-auto`` columns inside
        // each tab have a definite parent height to clamp against.
        <div className="h-full flex flex-col bg-surface-0 text-white overflow-hidden">
            <header className="flex items-center gap-2 border-b border-edge px-3 py-2 sm:px-4 shrink-0">
                <Tabs
                    label="Admin sections"
                    variant="pill"
                    className="flex-1 min-w-0"
                    value={tab}
                    onChange={(id) => setTab(id as AdminTab)}
                    items={ADMIN_TABS.map((t) => ({id: t.id, label: t.label}))}
                />
                <div className="flex items-center gap-2 shrink-0">
                    <CliTokenButton/>
                    {!embedded && (
                        <a
                            href="/"
                            className="text-sm text-accent pointer-fine:hover:text-accent px-2 py-1"
                            title="Back to viewer"
                        >
                            ← viewer
                        </a>
                    )}
                </div>
            </header>
            <main className="flex-1 min-h-0 overflow-hidden">
                {tab === "audit" && <AuditLogTab/>}
                {tab === "audit_runs" && <AuditRunsTab/>}
                {tab === "schedules" && <SchedulesTab/>}
                {tab === "issues" && <IssueTargetTab/>}
                {tab === "performance" && <PerformanceTab/>}
                {tab === "frontend_loads" && <FrontendLoadsTab/>}
                {tab === "corpus" && <CorpusTab/>}
                {tab === "projects" && <ProjectsTab/>}
                {tab === "storage" && <StorageTab/>}
                {tab === "workers" && <WorkersTab/>}
                {tab === "conversion" && <ConversionSettingsTab/>}
                {tab === "equipment" && (
                    <div className="h-full overflow-y-auto p-3 sm:p-4">
                        <EquipmentAdminPanel embedded/>
                    </div>
                )}
                {tab === "system" && (
                    <div className="h-full overflow-y-auto p-3 sm:p-4">
                        <SystemAdminPanel embedded/>
                    </div>
                )}
                {tab === "engines" && (
                    <div className="h-full overflow-y-auto p-3 sm:p-4">
                        <ProceduralEngineAdminPanel embedded/>
                    </div>
                )}
            </main>
        </div>
    );
};

export default AdminPanel;
