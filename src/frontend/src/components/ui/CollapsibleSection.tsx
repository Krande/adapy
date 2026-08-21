import React from "react";
import {Icon} from "../icons";
import {cn} from "./cn";

// The click-to-collapse group. One component for what used to be two:
// `common/CollapsibleSection` (Scene panel, Preferences) and a near-identical private
// `Section` inside the cellbuilder panel, which differed only in chrome and in having a
// count badge.
//
// `Section` in Panel.tsx is the non-collapsible sibling — a titled group that is always
// open. Use that when there is nothing to hide.

export interface CollapsibleSectionProps {
    title: string;
    /** Item count shown right-aligned in the header — "(12)". */
    count?: number;
    /**
     * No default on purpose. The two variants disagreed about it (divider sections
     * opened, boxed ones did not) and inheriting one silently would have flipped five
     * cellbuilder groups open. Say what you want.
     */
    defaultOpen?: boolean;
    /**
     * `divider` — a rule above the header, no box. For a column of primary groups that
     * make up a panel's whole body.
     * `boxed` — an outlined, tinted card. For occasional groups sitting among other
     * controls, where the box says "this is a container, not another row".
     */
    variant?: "divider" | "boxed";
    headerClassName?: string;
    bodyClassName?: string;
    children: React.ReactNode;
}

export function CollapsibleSection({
    title,
    count,
    defaultOpen = true,
    variant = "divider",
    headerClassName = "",
    bodyClassName = "",
    children,
}: CollapsibleSectionProps) {
    const [open, setOpen] = React.useState(defaultOpen);
    const boxed = variant === "boxed";

    return (
        <div
            className={cn(
                boxed
                    ? "overflow-hidden rounded-md border border-edge bg-surface-2/40"
                    : "border-t border-edge first:border-t-0",
            )}
        >
            <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                className={cn(
                    "ada-focus flex w-full select-none items-center gap-1.5 text-left font-semibold",
                    boxed
                        ? "px-2 py-1.5 pointer-fine:hover:bg-surface-3"
                        : "py-1 pointer-fine:hover:text-content",
                    headerClassName,
                )}
            >
                <Icon
                    name="chevron"
                    size="sm"
                    className={cn("shrink-0 transition-transform duration-(--ada-dur-fast)", open && "rotate-90")}
                />
                <span className="truncate">{title}</span>
                {count != null && <span className="ml-auto shrink-0 text-content-muted">({count})</span>}
            </button>
            {open && (
                <div className={cn(boxed ? "flex flex-col gap-2 px-2 pb-2 pt-0.5" : "pb-1", bodyClassName)}>
                    {children}
                </div>
            )}
        </div>
    );
}

export default CollapsibleSection;
