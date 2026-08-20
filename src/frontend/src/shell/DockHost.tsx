import React, {Suspense} from "react";
import {Icon, IconButton, cn} from "@/components/ui";
import {ErrorBoundary} from "@/components/common/ErrorBoundary";
import {DOCK_LABEL, type DockedId} from "./regions";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";
import {resolvePanel, type PanelId} from "./panelRegistry";
import {Z} from "./zIndex";
import {shouldStack} from "./dockArrangement";

// One docked region, in one of two arrangements.
//
// TABBED — a strip plus the active panel. The right answer when the dock is short: the
// old UI let every panel be open at once in one column, which is how "too much on
// screen" happened.
//
// STACKED — every panel visible at once, each under its own header. The right answer
// when the dock is tall, because then tabs are hiding things for no reason. Tabs are a
// response to scarcity; applying them when there is room is just making the user click
// to see what would have fitted anyway.
//
// The dock picks between them from its measured height, so it follows the window and the
// splitter without anyone configuring anything. Panels are mounted either way — the
// tabbed arrangement hides the inactive ones rather than unmounting them — so switching
// arrangement costs nothing and loses no panel state.
//
// The bottom dock is always tabbed: it is wide-and-short by design (the FEA table, the
// conversion log), and stacking wide-and-short panels gives every one of them too little
// height to be useful.

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

    // Arrangement follows the measured height, so it tracks the window and the splitter
    // with nothing to configure. The decision itself lives in dockArrangement.ts —
    // thresholds and hysteresis are the part that misbehaves, and a browser cannot easily
    // be driven to the exact heights where it matters.
    const bodyRef = React.useRef<HTMLDivElement | null>(null);
    const [stacked, setStacked] = React.useState(false);
    // Measure once, synchronously, before paint — then keep the observer for changes.
    //
    // The observer alone is not enough. Its first callback is delivered on the frame
    // pipeline, which the browser suspends for a hidden tab (and throttles under load),
    // so a panel opened in a background tab would sit in the DEFAULT arrangement until
    // something happened to resize it. A layout effect runs regardless of visibility, so
    // the first arrangement is right from the first paint.
    React.useLayoutEffect(() => {
        if (dock === "bottom" || defs.length < 2) {
            setStacked(false);
            return;
        }
        const el = bodyRef.current;
        if (!el) return;
        const measure = () =>
            setStacked((wasStacked) =>
                shouldStack({dock, panelCount: defs.length, heightPx: el.clientHeight, wasStacked}),
            );
        measure();
        if (typeof ResizeObserver === "undefined") return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [dock, defs.length]);

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
                <div
                    role="tablist"
                    aria-label={`${DOCK_LABEL[dock]} panels`}
                    // Stacked, the tabs would be selecting between things that are all
                    // already on screen. The dock's own controls stay.
                    className={cn("flex items-center gap-0.5 min-w-0", stacked && "hidden")}
                >
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
                    {!stacked && activeDef.pinnable && (
                        <IconButton
                            size="sm"
                            tooltip={pinned.includes(activeDef.id) ? "Unpin panel" : "Pin panel (survives mode switches)"}
                            pressed={pinned.includes(activeDef.id)}
                            icon={<Icon name="pin" size="sm" />}
                            onClick={() => togglePin(mode, activeDef.id)}
                        />
                    )}
                    {!stacked && (
                        <IconButton
                            size="sm"
                            tooltip="Float panel"
                            icon={<Icon name="float" size="sm" />}
                            onClick={() => floatPanel(mode, activeDef.id, {x: 140, y: 120, w: 380, h: 460})}
                        />
                    )}
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
                    {!stacked && (
                        <IconButton
                            size="sm"
                            tooltip={`Close ${activeDef.title}`}
                            icon={<Icon name="close" size="sm" />}
                            onClick={() => closePanel(mode, activeDef.id)}
                        />
                    )}
                </div>
            </div>

            {/* Body. `hidden` rather than unmounted when collapsed, so panel state
                (scroll position, expanded sections, in-flight edits) survives a
                collapse — the same reason ViewportHost never unmounts. */}
            <div
                ref={bodyRef}
                className={cn(
                    "flex-1 min-h-0 min-w-0 scrollbar",
                    stacked ? "flex flex-col overflow-y-auto" : "overflow-auto",
                    collapsed && "hidden",
                )}
            >
                {defs.map((def) => (
                    <div
                        key={def.id}
                        className={cn(
                            stacked
                                ? "flex flex-col shrink-0 border-b border-edge last:border-b-0"
                                : def.id === activeDef.id
                                  // A flex column, not just h-full: a panel that wants to
                                  // fill the dock says so with flex-1, and flex-1 inside a
                                  // plain block resolves against content rather than the
                                  // parent — so the panel silently sized to its content
                                  // and its own layout decisions (scroll regions, the
                                  // tabbed/stacked measurement) saw a fraction of the
                                  // height they actually had.
                                  ? "flex h-full flex-col"
                                  : "hidden",
                        )}
                    >
                        {stacked && (
                            <div className="flex items-center gap-1.5 shrink-0 px-2 h-7 bg-surface-2 border-b border-edge">
                                <Icon name={def.icon} size="sm" className="opacity-70" />
                                <span className="flex-1 truncate text-xs font-medium">{def.title}</span>
                                {def.pinnable && (
                                    <IconButton
                                        size="sm"
                                        tooltip={pinned.includes(def.id) ? "Unpin panel" : "Pin panel (survives mode switches)"}
                                        pressed={pinned.includes(def.id)}
                                        icon={<Icon name="pin" size="sm" />}
                                        onClick={() => togglePin(mode, def.id)}
                                    />
                                )}
                                <IconButton
                                    size="sm"
                                    tooltip="Float panel"
                                    icon={<Icon name="float" size="sm" />}
                                    onClick={() => floatPanel(mode, def.id, {x: 140, y: 120, w: 380, h: 460})}
                                />
                                <IconButton
                                    size="sm"
                                    tooltip={`Close ${def.title}`}
                                    icon={<Icon name="close" size="sm" />}
                                    onClick={() => closePanel(mode, def.id)}
                                />
                            </div>
                        )}
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
