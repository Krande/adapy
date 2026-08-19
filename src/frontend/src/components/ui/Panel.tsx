import React from "react";
import {cn} from "./cn";

// The panel container — one definition of "a box of UI".
//
// Supersedes the PANEL_CHROME class string in themeStore (which only covered
// background/border/text) and the dozens of hand-built `rounded border p-2` wrappers
// the audit found. The colour tokens are the same ones PANEL_CHROME used, so existing
// panels and new ones match while the migration is in flight.
//
// Header/Body/Footer are separate components rather than props because bodies need to
// scroll independently of a sticky header — the single most common bug in the current
// panels, where a long list pushes the close button off screen.

export interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
    /** `raised` for a floating panel over the 3D, `flat` when docked into a region
     *  that already provides the surround. */
    elevation?: "raised" | "flat";
    children: React.ReactNode;
}

export function Panel({elevation = "raised", className, children, ...rest}: PanelProps) {
    return (
        <div
            className={cn(
                "flex flex-col min-h-0 bg-surface-1 text-content border border-edge rounded-md",
                elevation === "raised" && "shadow-panel",
                className,
            )}
            {...rest}
        >
            {children}
        </div>
    );
}

// `title` is omitted from the DOM attributes: ours is a ReactNode heading, not the
// string that becomes a native tooltip.
export interface PanelHeaderProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
    title: React.ReactNode;
    /** Buttons, menus, a close control. */
    actions?: React.ReactNode;
    /** Small muted line under the title. */
    subtitle?: React.ReactNode;
}

export function PanelHeader({title, actions, subtitle, className, ...rest}: PanelHeaderProps) {
    return (
        <div
            className={cn(
                "flex items-start justify-between gap-2 shrink-0",
                "px-2 py-1.5 border-b border-edge",
                className,
            )}
            {...rest}
        >
            <div className="flex flex-col min-w-0 gap-0.5">
                <span className="text-sm font-semibold truncate">{title}</span>
                {subtitle && <span className="text-xs text-content-subtle truncate">{subtitle}</span>}
            </div>
            {actions && <div className="flex items-center gap-1 shrink-0">{actions}</div>}
        </div>
    );
}

/** Scrolls independently of the header/footer. `min-h-0` is what actually makes
 *  that work inside a flex column — omitting it is why so many current panels
 *  grow instead of scrolling. */
export function PanelBody({className, children, ...rest}: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <div className={cn("flex-1 min-h-0 overflow-auto scrollbar p-2", className)} {...rest}>
            {children}
        </div>
    );
}

export function PanelFooter({className, children, ...rest}: React.HTMLAttributes<HTMLDivElement>) {
    return (
        <div
            className={cn("flex items-center justify-end gap-2 shrink-0 px-2 py-1.5 border-t border-edge", className)}
            {...rest}
        >
            {children}
        </div>
    );
}

/** A titled group within a panel body. Replaces the ad-hoc
 *  `text-xs text-gray-400` heading + wrapper repeated across the options and
 *  admin panels. For collapsible groups use the existing CollapsibleSection. */
export function Section({
    title,
    actions,
    className,
    children,
}: {
    title?: React.ReactNode;
    actions?: React.ReactNode;
    className?: string;
    children: React.ReactNode;
}) {
    return (
        <section className={cn("flex flex-col gap-1.5", className)}>
            {(title || actions) && (
                <div className="flex items-center justify-between gap-2">
                    {title && (
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-content-subtle">{title}</h3>
                    )}
                    {actions}
                </div>
            )}
            {children}
        </section>
    );
}

/** Label-left / control-right row — the single most repeated layout in the
 *  Options drawer and the admin tabs. */
export function PropertyRow({
    label,
    hint,
    className,
    children,
}: {
    label: React.ReactNode;
    hint?: React.ReactNode;
    className?: string;
    children: React.ReactNode;
}) {
    return (
        <div className={cn("flex items-center justify-between gap-3 min-h-control-md", className)}>
            <div className="flex flex-col gap-0.5 min-w-0">
                <span className="text-sm text-content truncate">{label}</span>
                {hint && <span className="text-xs text-content-subtle truncate">{hint}</span>}
            </div>
            <div className="shrink-0">{children}</div>
        </div>
    );
}
