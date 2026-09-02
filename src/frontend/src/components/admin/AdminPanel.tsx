import React, {useEffect, useState} from "react";
import {AdminTab, AdminTabDeepLink} from "@/state/adminPanelStore";
import {useMeStore} from "@/state/meStore";
import ErrorBoundary from "@/components/common/ErrorBoundary";
import {disablePlugin, getAdminTabs, makePluginContextStandalone} from "@/plugins";
import AuditTab from "./AuditTab";
import PerformanceTab from "./PerformanceTab";
import ProceduralTab from "./ProceduralTab";
import {parseTabId} from "./adminTabs";
import type {AuditSubTab, PerformanceSubTab, ProceduralSubTab} from "./adminTabs";
import CliTokenButton from "./CliTokenButton";
import ConversionSettingsTab from "./ConversionSettingsTab";
import IssueTargetTab from "./IssueTargetTab";
import ExternalModelsTab from "./ExternalModelsTab";
import ProjectsTab from "./ProjectsTab";
import StorageTab from "./StorageTab";
import WorkersTab from "./WorkersTab";

// Path-mounted admin page (``/admin``) — full-screen on every
// viewport, with the active tab serialised into the URL hash so a
// browser refresh stays on the same panel. Previously a draggable
// modal overlay on the viewer; the page form survives refreshes
// (the modal mode didn't) and gives every tab room to lay out on
// mobile.
//
// Sub-tabs: ``/admin#audit/runs`` lands directly on the regression
// sweep panel; ``/admin`` with no hash uses ``audit`` as the
// default. Anchor links elsewhere in the SPA can deep-link to a
// specific tab without touching state, e.g. the conversion-toast
// info icon hard-codes ``/admin#audit``.


function readTabFromHash(extra: ReadonlySet<string>): {tab: string; sub?: string} {
    return parseTabId((window.location.hash || "").replace(/^#/, "").trim(), extra);
}

interface AdminPanelProps {
    /** Mounted inside the viewer's floating panel host rather than the
     * full-page ``/admin`` route. Embedded mode keeps the URL alone
     * (no ``#audit`` hash scribbling from a draggable overlay) and
     * hides the "← viewer" link — the host's close button already
     * covers leaving the panel. */
    embedded?: boolean;
    /** Tab to open on in embedded mode (no URL hash to read from). Defaults to
     * "audit". Accepts the retired ids too — they resolve to the matching
     * audit sub-tab. */
    initialTab?: AdminTabDeepLink;
}

const AdminPanel: React.FC<AdminPanelProps> = ({embedded = false, initialTab}) => {
    const syncHash = !embedded;
    const isAdmin = useMeStore((s) => s.isAdmin);
    // Plugin-contributed tabs. Recomputed each render (like PluginPanelRegion)
    // so a plugin whose activation predicate flips shows/hides its tab live.
    const pluginTabs = getAdminTabs(makePluginContextStandalone(""));
    const pluginTabIds = new Set(pluginTabs.map((t) => t.panel.id));
    // One parser for both entry points, so a retired id like "audit_runs"
    // resolves the same whether it arrives in the URL hash or from an in-app
    // trigger (the audit-sweep toast passes exactly that).
    const initial = syncHash
        ? readTabFromHash(pluginTabIds)
        : parseTabId(initialTab ?? "audit", pluginTabIds);
    const [tab, setTab] = useState<string>(initial.tab);
    // Sub-tab is only read FROM the hash (a legacy deep link, or #audit/runs).
    // AuditTab owns it thereafter; re-serialising every sub-tab click into the
    // URL would mean this component re-rendered the whole panel on each one.
    const [initialSub] = useState<string | undefined>(initial.sub);
    const activePlugin = pluginTabs.find((t) => t.panel.id === tab) ?? null;

    // Two-way bind ``tab`` to ``window.location.hash`` so reloads stay
    // on the selected tab AND back/forward navigation works inside
    // the page. setTab writes; popstate / hashchange reads back.
    useEffect(() => {
        if (!syncHash) return;
        const onChange = () => setTab(readTabFromHash(pluginTabIds).tab);
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
                        Audit
                    </TabButton>
                    <TabButton active={tab === "issues"} onClick={() => setTab("issues")}>
                        Issues
                    </TabButton>
                    <TabButton active={tab === "performance"} onClick={() => setTab("performance")}>
                        Performance
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
                    <TabButton active={tab === "procedural"} onClick={() => setTab("procedural")}>
                        Procedural Engine
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
                {tab === "audit" && <AuditTab initialSubTab={initialSub as AuditSubTab | undefined}/>}
                {tab === "issues" && <IssueTargetTab/>}
                {tab === "performance" && (
                    <PerformanceTab initialSubTab={initialSub as PerformanceSubTab | undefined}/>
                )}
                {tab === "projects" && <ProjectsTab/>}
                {tab === "external_models" && <ExternalModelsTab/>}
                {tab === "storage" && <StorageTab/>}
                {tab === "workers" && <WorkersTab/>}
                {tab === "conversion" && <ConversionSettingsTab/>}
                {tab === "procedural" && (
                    <ProceduralTab initialSubTab={initialSub as ProceduralSubTab | undefined}/>
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
