import React, {useEffect, useState} from "react";
import {AdminTab} from "@/state/adminPanelStore";
import {useMeStore} from "@/state/meStore";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {disablePlugin, getAdminTabs, makePluginContextStandalone} from "@/plugins";
import AuditLogTab from "./AuditLogTab";
import AuditRunsTab from "./AuditRunsTab";
import CliTokenButton from "./CliTokenButton";
import ConversionSettingsTab from "./ConversionSettingsTab";
import CorpusTab from "./CorpusTab";
import EquipmentAdminPanel from "./EquipmentAdminPanel";
import FrontendLoadsTab from "./FrontendLoadsTab";
import IssueTargetTab from "./IssueTargetTab";
import PerformanceTab from "./PerformanceTab";
import ExternalModelsTab from "./ExternalModelsTab";
import ProjectsTab from "./ProjectsTab";
import SchedulesTab from "./SchedulesTab";
import StorageTab from "./StorageTab";
import SystemAdminPanel from "./SystemAdminPanel";
import ProceduralEngineAdminPanel from "./ProceduralEngineAdminPanel";
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
    "frontend_loads", "corpus", "projects", "external_models", "storage", "workers", "conversion",
    "equipment", "system", "engines",
]);

// A tab id is either one of the built-ins above or a plugin panel's namespaced
// id (``{pluginId}:{panelId}``). ``extra`` carries the plugin ones so a deep link
// to a plugin tab survives a reload instead of falling back to "audit".
function readTabFromHash(extra: ReadonlySet<string>): string {
    const raw = (window.location.hash || "").replace(/^#/, "").trim();
    return VALID_TABS.has(raw as AdminTab) || extra.has(raw) ? raw : "audit";
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
    // Plugin-contributed tabs. Recomputed each render (like PluginPanelRegion)
    // so a plugin whose activation predicate flips shows/hides its tab live.
    const pluginTabs = getAdminTabs(makePluginContextStandalone(""));
    const pluginTabIds = new Set(pluginTabs.map((t) => t.panel.id));
    const [tab, setTab] = useState<string>(() =>
        syncHash ? readTabFromHash(pluginTabIds) : (initialTab ?? "audit"),
    );
    const activePlugin = pluginTabs.find((t) => t.panel.id === tab) ?? null;

    // Two-way bind ``tab`` to ``window.location.hash`` so reloads stay
    // on the selected tab AND back/forward navigation works inside
    // the page. setTab writes; popstate / hashchange reads back.
    useEffect(() => {
        if (!syncHash) return;
        const onChange = () => setTab(readTabFromHash(pluginTabIds));
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
        // ``h-full`` so the panel adapts to whatever container it's
        // mounted into. The full-page route wraps this in a
        // ``h-[100dvh]`` shell (true page mode); the in-viewer
        // ``InViewerPanelHost`` mounts it inside a draggable Rnd
        // whose explicit height drives the same flex chain. Either
        // way the nested ``flex-1 overflow-auto`` columns inside
        // each tab have a definite parent height to clamp against.
        <div className="h-full flex flex-col bg-gray-900 text-white overflow-hidden">
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
                    <TabButton active={tab === "frontend_loads"} onClick={() => setTab("frontend_loads")}>
                        Frontend Loads
                    </TabButton>
                    <TabButton active={tab === "corpus"} onClick={() => setTab("corpus")}>
                        Corpus
                    </TabButton>
                    <TabButton active={tab === "projects"} onClick={() => setTab("projects")}>
                        Projects
                    </TabButton>
                    <TabButton active={tab === "external_models"} onClick={() => setTab("external_models")}>
                        External Models
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
                    <TabButton active={tab === "equipment"} onClick={() => setTab("equipment")}>
                        Equipment
                    </TabButton>
                    <TabButton active={tab === "system"} onClick={() => setTab("system")}>
                        System
                    </TabButton>
                    <TabButton active={tab === "engines"} onClick={() => setTab("engines")}>
                        Engines
                    </TabButton>
                    {pluginTabs.map(({panel, label}) => (
                        <TabButton
                            key={panel.id}
                            active={tab === panel.id}
                            onClick={() => setTab(panel.id)}
                        >
                            {label}
                        </TabButton>
                    ))}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    <CliTokenButton/>
                    {!embedded && (
                        <a
                            href="/"
                            className="text-sm text-blue-400 hover:text-blue-300 px-2 py-1"
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
                {tab === "external_models" && <ExternalModelsTab/>}
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
                {activePlugin && (
                    // Same containment contract as every other plugin slot host:
                    // a crashing panel disables its plugin for the session rather
                    // than white-screening the admin page.
                    <ErrorBoundary
                        key={activePlugin.panel.id}
                        label={`Plugin ${activePlugin.pluginId}`}
                        fallback={(error, reset) => {
                            disablePlugin(
                                activePlugin.pluginId,
                                `admin panel render threw: ${error.message}`,
                            );
                            return (
                                <div className="p-4 text-sm">
                                    <div className="font-semibold text-red-300">
                                        Plugin “{activePlugin.pluginId}” hit an error
                                    </div>
                                    <div className="mt-1 mb-2 break-words text-gray-400 text-xs">
                                        {error.message}
                                    </div>
                                    <button
                                        type="button"
                                        onClick={reset}
                                        className="rounded-sm bg-gray-700 px-2 py-1 text-white hover:bg-gray-600"
                                    >
                                        Retry
                                    </button>
                                </div>
                            );
                        }}
                    >
                        <div className="h-full overflow-y-auto">
                            {activePlugin.panel.render(
                                makePluginContextStandalone(activePlugin.pluginId),
                            )}
                        </div>
                    </ErrorBoundary>
                )}
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
