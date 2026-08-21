import React from "react";
import {cn} from "./cn";

// Toolbar, Group and Separator.
//
// `flex items-center gap-1` and `flex items-center gap-2` were the two most-repeated
// class strings in the whole codebase (49 and 23 call sites). Almost all of them are
// a toolbar. Naming it means the spacing is decided once, and it carries the ARIA
// semantics the hand-rolled versions never did.

export interface ToolbarProps extends React.HTMLAttributes<HTMLDivElement> {
    /** Accessible name — say what the toolbar acts on. */
    label: string;
    orientation?: "horizontal" | "vertical";
    /** Tighter spacing for dense icon rows (the mode tool rail). */
    dense?: boolean;
}

export function Toolbar({label, orientation = "horizontal", dense, className, children, ...rest}: ToolbarProps) {
    return (
        <div
            role="toolbar"
            aria-label={label}
            aria-orientation={orientation}
            className={cn(
                "flex min-w-0",
                orientation === "horizontal" ? "flex-row items-center" : "flex-col items-stretch",
                dense ? "gap-0.5" : "gap-1.5",
                className,
            )}
            {...rest}
        >
            {children}
        </div>
    );
}

/** Related controls, spaced closer than the gap between groups. */
export function ToolbarGroup({
    orientation = "horizontal",
    className,
    children,
    ...rest
}: React.HTMLAttributes<HTMLDivElement> & {orientation?: "horizontal" | "vertical"}) {
    return (
        <div
            className={cn(
                "flex gap-0.5 min-w-0",
                orientation === "horizontal" ? "flex-row items-center" : "flex-col items-stretch",
                className,
            )}
            {...rest}
        >
            {children}
        </div>
    );
}

/** Visual divider. Decorative — hidden from assistive tech, which gets the
 *  grouping from ToolbarGroup instead. */
export function ToolbarSeparator({orientation = "horizontal"}: {orientation?: "horizontal" | "vertical"}) {
    return (
        <span
            aria-hidden="true"
            className={cn("shrink-0 bg-edge", orientation === "horizontal" ? "w-px h-4 mx-1" : "h-px w-4 my-1")}
        />
    );
}

/** Pushes everything after it to the far end of the toolbar. */
export function ToolbarSpacer() {
    return <span aria-hidden="true" className="flex-1 min-w-0" />;
}
