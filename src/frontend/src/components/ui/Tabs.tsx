import React from "react";
import {cn} from "./cn";

// Tab strip with real ARIA tab semantics and roving-tabindex keyboard nav.
//
// Three call sites already need exactly this and all three hand-rolled it:
// SceneInfoBox (6 tabs, 2 of them contextual), AdminPanel (14 tabs, hash-routed)
// and the Simulation panel (built-in tabs plus plugin-contributed ones with badges).
// Hence `contextual` and `badge` are first-class here rather than per-site hacks.
//
// Uncontrolled by design: the caller owns `value`. Every existing consumer already
// keeps the active tab in a store (sceneInfoStore.mode, adminPanelStore, …) and
// needs to, because deep links and cross-panel actions set it from outside.

export interface TabItem {
    id: string;
    label: React.ReactNode;
    /** Only meaningful right now — e.g. FEM/Joints appear once such data is loaded.
     *  Rendered with a leading dot so its appearance is noticeable, not silent. */
    contextual?: boolean;
    /** Count or short status shown after the label. */
    badge?: React.ReactNode;
    disabled?: boolean;
}

export type TabsVariant = "underline" | "pill" | "segmented";

export interface TabsProps {
    items: TabItem[];
    value: string;
    onChange: (id: string) => void;
    variant?: TabsVariant;
    /** Accessible name for the tab list — say what is being switched. */
    label: string;
    className?: string;
}

export function Tabs({items, value, onChange, variant = "underline", label, className}: TabsProps) {
    const refs = React.useRef<Record<string, HTMLButtonElement | null>>({});

    // Roving tabindex: arrows move between tabs, Home/End jump to the ends, and only
    // the active tab is in the page tab order (WAI-ARIA tabs pattern).
    const onKeyDown = (e: React.KeyboardEvent) => {
        const enabled = items.filter((i) => !i.disabled);
        const at = enabled.findIndex((i) => i.id === value);
        if (at < 0) return;

        let next = -1;
        if (e.key === "ArrowRight") next = (at + 1) % enabled.length;
        else if (e.key === "ArrowLeft") next = (at - 1 + enabled.length) % enabled.length;
        else if (e.key === "Home") next = 0;
        else if (e.key === "End") next = enabled.length - 1;
        if (next < 0) return;

        e.preventDefault();
        const id = enabled[next].id;
        onChange(id);
        refs.current[id]?.focus();
    };

    const isSegmented = variant === "segmented";

    return (
        <div
            role="tablist"
            aria-label={label}
            onKeyDown={onKeyDown}
            className={cn(
                // Overflow scrolls rather than wraps: 14 admin tabs in a narrow dock
                // must stay one row, or the strip's height changes as you resize.
                "flex items-stretch gap-0.5 overflow-x-auto scrollbar min-w-0",
                variant === "underline" && "border-b border-edge",
                isSegmented && "p-0.5 bg-surface-2 rounded-md gap-0",
                className,
            )}
        >
            {items.map((item) => {
                const active = item.id === value;
                return (
                    <button
                        key={item.id}
                        ref={(n) => {
                            refs.current[item.id] = n;
                        }}
                        role="tab"
                        type="button"
                        aria-selected={active}
                        aria-controls={`panel-${item.id}`}
                        id={`tab-${item.id}`}
                        tabIndex={active ? 0 : -1}
                        disabled={item.disabled}
                        onClick={() => onChange(item.id)}
                        className={cn(
                            "ada-focus inline-flex items-center gap-1.5 shrink-0 whitespace-nowrap",
                            "px-2.5 h-control-md min-h-control-md text-sm font-medium",
                            "transition-colors duration-(--ada-dur-fast)",
                            "disabled:opacity-40 disabled:pointer-events-none",
                            variant === "underline" &&
                                (active
                                    ? "text-content border-b-2 border-accent -mb-px"
                                    : "text-content-muted border-b-2 border-transparent pointer-fine:hover:text-content"),
                            variant === "pill" &&
                                (active
                                    ? "text-accent bg-accent-subtle rounded-md"
                                    : "text-content-muted rounded-md pointer-fine:hover:bg-surface-2 pointer-fine:hover:text-content"),
                            isSegmented &&
                                (active
                                    ? "text-content bg-surface-1 rounded-sm shadow-panel"
                                    : "text-content-muted rounded-sm pointer-fine:hover:text-content"),
                        )}
                    >
                        {item.contextual && (
                            <span
                                aria-hidden="true"
                                className={cn("w-1.5 h-1.5 rounded-full shrink-0", active ? "bg-accent" : "bg-content-subtle")}
                            />
                        )}
                        {item.label}
                        {item.badge != null && item.badge !== "" && (
                            <span className="text-xs px-1 rounded-sm bg-surface-3 text-content-muted tabular-nums">
                                {item.badge}
                            </span>
                        )}
                    </button>
                );
            })}
        </div>
    );
}

/** The region a tab controls. Pairs with the ids Tabs generates. */
export function TabPanel({
    id,
    active,
    className,
    children,
}: {
    id: string;
    active: boolean;
    className?: string;
    children: React.ReactNode;
}) {
    return (
        <div
            role="tabpanel"
            id={`panel-${id}`}
            aria-labelledby={`tab-${id}`}
            hidden={!active}
            // tabIndex 0 so a panel with no focusable content is still reachable
            // by keyboard from its tab.
            tabIndex={active ? 0 : -1}
            className={cn(active ? "" : "hidden", className)}
        >
            {children}
        </div>
    );
}
