import React, {Suspense} from "react";
import {Icon, IconButton, cn} from "@/components/ui";
import {ErrorBoundary} from "@/components/common/ErrorBoundary";
import {DOCK_LABEL, type DockedId} from "./regions";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";
import {resolvePanel, type PanelId} from "./panelRegistry";
import {Z} from "./zIndex";

// One docked region: a tab strip plus the active panel's body.
//
// Tabs rather than stacking is deliberate. The old UI let every panel be open at once
// in the same column, which is how "too much on screen" happened; a dock shows one
// panel at a time and the strip tells you what else is there. Blender's non-blocking
// rule still holds — nothing is hidden behind a modal, it is one click away and always
// visible in the strip.

export interface DockHostProps {
    dock: DockedId;
}

/** Placement-agnostic: it fills whatever box AppShell puts it in. Keeping grid-area
 *  ownership in one file (AppShell) is what stops the template and the panels drifting
 *  apart. */
export default function DockHost({dock}: DockHostProps) {
    const mode = useModeStore((s) => s.mode);
    const state = useLayoutStore((s) => s.perMode[mode]?.docks[dock]);
    const activateTab = useLayoutStore((s) => s.activateTab);
    const closePanel = useLayoutStore((s) => s.closePanel);
    const toggleDock = useLayoutStore((s) => s.toggleDock);
    const floatPanel = useLayoutStore((s) => s.floatPanel);
    const togglePin = useLayoutStore((s) => s.togglePin);
    const pinned = useLayoutStore((s) => s.perMode[mode]?.pinned ?? []);

    // Resolve through the registry so a stale persisted id, or a REST-only panel in a
    // desktop build, degrades to an empty dock rather than crashing the shell.
    const defs = React.useMemo(
        () => (state?.tabs ?? []).map((id) => resolvePanel(id)).filter((d): d is NonNullable<typeof d> => d != null),
        [state?.tabs],
    );

    if (!state || defs.length === 0) return null;

    const activeDef = defs.find((d) => d.id === state.active) ?? defs[0];
    const collapsed = state.collapsed;
    const horizontal = dock === "bottom";

    return (
        <section
            aria-label={DOCK_LABEL[dock]}
            style={{zIndex: Z.dock}}
            className={cn(
                "flex flex-col flex-1 min-w-0 min-h-0 bg-surface-1 border-edge",
                dock === "left" && "border-r",
                dock === "right" && "border-l",
                dock === "bottom" && "border-t",
            )}
        >
            {/* Tab strip. Scrolls rather than wrapping: 14 admin tabs in a narrow dock
                must stay one row, or the strip's height changes as you resize. */}
            <div className="flex items-center gap-0.5 shrink-0 px-1 h-8 border-b border-edge overflow-x-auto scrollbar">
                <div role="tablist" aria-label={`${DOCK_LABEL[dock]} panels`} className="flex items-center gap-0.5 min-w-0">
                    {defs.map((def) => {
                        const active = def.id === activeDef.id && !collapsed;
                        return (
                            <button
                                key={def.id}
                                role="tab"
                                type="button"
                                aria-selected={active}
                                tabIndex={active ? 0 : -1}
                                onClick={() => activateTab(mode, dock, def.id)}
                                title={def.hint ?? def.title}
                                className={cn(
                                    "ada-focus inline-flex items-center gap-1.5 shrink-0 px-2 h-6 rounded-sm",
                                    "text-xs font-medium whitespace-nowrap transition-colors duration-(--ada-dur-fast)",
                                    active
                                        ? "bg-surface-3 text-content"
                                        : "text-content-muted pointer-fine:hover:text-content pointer-fine:hover:bg-surface-2",
                                )}
                            >
                                <Icon name={def.icon} size="sm" />
                                {def.title}
                                {pinned.includes(def.id) && <Icon name="pin" size="sm" className="opacity-60" />}
                            </button>
                        );
                    })}
                </div>

                <span className="flex-1 min-w-0" />

                <div className="flex items-center gap-0.5 shrink-0">
                    {activeDef.pinnable && (
                        <IconButton
                            size="sm"
                            tooltip={pinned.includes(activeDef.id) ? "Unpin panel" : "Pin panel (survives mode switches)"}
                            pressed={pinned.includes(activeDef.id)}
                            icon={<Icon name="pin" size="sm" />}
                            onClick={() => togglePin(mode, activeDef.id)}
                        />
                    )}
                    <IconButton
                        size="sm"
                        tooltip="Float panel"
                        icon={<Icon name="float" size="sm" />}
                        onClick={() => floatPanel(mode, activeDef.id, {x: 140, y: 120, w: 380, h: 460})}
                    />
                    <IconButton
                        size="sm"
                        tooltip={collapsed ? `Expand ${DOCK_LABEL[dock].toLowerCase()}` : `Collapse ${DOCK_LABEL[dock].toLowerCase()}`}
                        icon={
                            <Icon
                                name={horizontal ? "dock-bottom" : dock === "left" ? "dock-left" : "dock-right"}
                                size="sm"
                            />
                        }
                        pressed={collapsed}
                        onClick={() => toggleDock(mode, dock)}
                    />
                    <IconButton
                        size="sm"
                        tooltip={`Close ${activeDef.title}`}
                        icon={<Icon name="close" size="sm" />}
                        onClick={() => closePanel(mode, activeDef.id)}
                    />
                </div>
            </div>

            {/* Body. `hidden` rather than unmounted when collapsed, so panel state
                (scroll position, expanded sections, in-flight edits) survives a
                collapse — the same reason ViewportHost never unmounts. */}
            <div className={cn("flex-1 min-h-0 min-w-0 overflow-auto scrollbar", collapsed && "hidden")}>
                {defs.map((def) => (
                    <div key={def.id} className={def.id === activeDef.id ? "h-full" : "hidden"}>
                        {/* Per-panel boundary: one panel throwing must not take the
                            shell down with it. */}
                        <ErrorBoundary label={def.title}>
                            <Suspense fallback={<PanelSkeleton />}>
                                <def.component />
                            </Suspense>
                        </ErrorBoundary>
                    </div>
                ))}
            </div>
        </section>
    );
}

function PanelSkeleton() {
    return (
        <div className="p-3 flex flex-col gap-2" aria-hidden="true">
            {[70, 90, 55].map((w, i) => (
                <div key={i} className="h-3 rounded-sm bg-surface-3 opacity-50" style={{width: `${w}%`}} />
            ))}
        </div>
    );
}

export type {PanelId};
