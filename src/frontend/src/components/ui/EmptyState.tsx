import React from "react";
import {cn} from "./cn";

// What a panel says when it has nothing to show.
//
// Every panel grew its own version of this, and they disagreed about everything: some
// centred, some flush left; some 11px, some 13px; some said what was missing, some said
// what to do about it, most said one or the other. A panel with nothing in it is the
// first thing a new user sees, so it is the worst place in the product for the chrome to
// look unfinished.
//
// The shape is deliberate and worth keeping to:
//
//   title  — what is missing, as a statement. "No procedural model is open."
//   hint   — what to do about it, naming the actual control. "Use New procedural model…
//            in the Build toolbar."
//
// The second half is the part panels kept leaving out, and it is the half that matters:
// "Nothing selected" tells you the state you can already see. Naming the door out of it
// is the only thing the panel can add.

export interface EmptyStateProps {
    /** What is missing, as a statement rather than an apology. */
    title: React.ReactNode;
    /** What to do about it. Name the control, in the words the control uses. */
    hint?: React.ReactNode;
    /**
     * Centred in the available height.
     *
     * On for a panel whose whole body is empty; off when this sits above content that is
     * present — a centred message with a list under it reads as a failure rather than as
     * a note about the list.
     */
    centered?: boolean;
    className?: string;
}

export function EmptyState({title, hint, centered = true, className}: EmptyStateProps) {
    return (
        <div
            className={cn(
                "flex flex-col gap-1 p-6",
                centered ? "h-full items-center justify-center text-center" : "p-3 text-left",
                className,
            )}
        >
            <p className="text-sm text-content-muted">{title}</p>
            {hint && <p className="max-w-72 text-xs text-content-subtle">{hint}</p>}
        </div>
    );
}

/**
 * Names a control inside a hint, in the words the control actually uses.
 *
 * A hint that paraphrases — "start a new model" for a button labelled "New procedural
 * model…" — sends people looking for something that is not there.
 */
export function Ui({children}: {children: React.ReactNode}) {
    return <span className="text-content">{children}</span>;
}
