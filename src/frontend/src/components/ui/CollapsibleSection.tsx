// A disclosure: a header button that shows or hides the content under it.
//
// Two looks, each exactly the markup a panel drew before it adopted this:
//
//   * `"section"` (default) -- a bold header with a turning chevron, a rule
//     between consecutive sections; the Scene panel's groups.
//   * `"caret"` -- a semibold header with the caret at the far end, no rule;
//     the Options panel's sections.
//
// Uncontrolled: each section owns its open state.

import React, {useState} from "react";

import ChevronRightIcon from "@/components/icons/ChevronRightIcon";

export interface CollapsibleSectionProps {
    title: string;
    /** Default true. */
    defaultOpen?: boolean;
    /** Default `"section"`. */
    variant?: "section" | "caret";
    /** Classes after the header's own. */
    headerClassName?: string;
    /** Classes after the body's own. */
    bodyClassName?: string;
    children: React.ReactNode;
}

export function CollapsibleSection({
    title,
    defaultOpen = true,
    variant = "section",
    headerClassName = "",
    bodyClassName = "",
    children,
}: CollapsibleSectionProps) {
    const [open, setOpen] = useState(defaultOpen);
    const toggle = () => setOpen((v) => !v);
    if (variant === "caret") {
        return (
            <div>
                <button
                    type="button"
                    className={`w-full flex items-center justify-between py-1 text-left font-semibold ${headerClassName}`.trim()}
                    onClick={toggle}
                    aria-expanded={open}
                >
                    <span>{title}</span>
                    <span className="text-gray-300 text-xs">{open ? "▾" : "▸"}</span>
                </button>
                {open && <div className={`mt-2 ${bodyClassName}`.trim()}>{children}</div>}
            </div>
        );
    }
    return (
        <div className="border-t border-white/20 first:border-t-0">
            <button
                type="button"
                onClick={toggle}
                className={`w-full flex items-center gap-1 py-1 text-left font-bold select-none ${headerClassName}`.trim()}
                aria-expanded={open}
            >
                <ChevronRightIcon className={`transition-transform shrink-0 ${open ? "rotate-90" : ""}`} />
                <span>{title}</span>
            </button>
            {open && <div className={`pb-1 ${bodyClassName}`.trim()}>{children}</div>}
        </div>
    );
}

export default CollapsibleSection;
