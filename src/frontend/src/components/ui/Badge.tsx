import React from "react";
import {cn} from "./cn";

// Status vocabulary: Badge (labelled), StatusDot (compact), Kbd (key hint).
//
// One set of status colours across conversion jobs, worker health, audit results,
// capacity checks and plugin panels — today each of those picked its own greens and
// reds. `tone` is semantic, never a colour name, so a later palette change is one
// edit here rather than a search for `text-green-400`.

export type Tone = "neutral" | "accent" | "pass" | "warn" | "fail" | "info";

const TONE: Record<Tone, string> = {
    neutral: "bg-surface-3 text-content-muted border-edge",
    accent: "bg-accent-subtle text-accent border-accent/40",
    pass: "bg-pass-subtle text-pass border-pass/40",
    warn: "bg-warn-subtle text-warn border-warn/40",
    fail: "bg-fail-subtle text-fail border-fail/40",
    info: "bg-info-subtle text-info border-info/40",
};

const DOT: Record<Tone, string> = {
    neutral: "bg-content-subtle",
    accent: "bg-accent",
    pass: "bg-pass",
    warn: "bg-warn",
    fail: "bg-fail",
    info: "bg-info",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
    tone?: Tone;
    /** Show a leading dot as well as the label. */
    dot?: boolean;
}

export function Badge({tone = "neutral", dot, className, children, ...rest}: BadgeProps) {
    return (
        <span
            className={cn(
                "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm border",
                "text-xs font-medium leading-tight whitespace-nowrap tabular-nums",
                TONE[tone],
                className,
            )}
            {...rest}
        >
            {dot && <span aria-hidden="true" className={cn("w-1.5 h-1.5 rounded-full shrink-0", DOT[tone])} />}
            {children}
        </span>
    );
}

/**
 * Status as a bare dot, for table rows and the status bar.
 *
 * `label` is required and rendered visually-hidden: colour alone is not an
 * accessible status, and it is also unreadable for anyone with a colour-vision
 * deficiency. Callers wanting a visible label should use Badge instead.
 */
export function StatusDot({tone = "neutral", label, pulse, className}: {tone?: Tone; label: string; pulse?: boolean; className?: string}) {
    return (
        <span className={cn("relative inline-flex shrink-0", className)}>
            <span aria-hidden="true" className={cn("w-2 h-2 rounded-full", DOT[tone])} />
            {pulse && (
                <span
                    aria-hidden="true"
                    className={cn("absolute inset-0 rounded-full motion-safe:animate-ping opacity-60", DOT[tone])}
                />
            )}
            <span className="sr-only">{label}</span>
        </span>
    );
}

/** A keyboard key. Used by tooltips, menu items and the shortcuts reference, all of
 *  which are generated from one shortcut registry so the hints cannot go stale. */
export function Kbd({className, children}: {className?: string; children: React.ReactNode}) {
    return (
        <kbd
            className={cn(
                "inline-flex items-center justify-center min-w-[1.4em] px-1 py-px rounded-sm",
                "bg-surface-3 text-content-muted border border-edge border-b-2",
                "font-mono text-xs leading-none whitespace-nowrap",
                className,
            )}
        >
            {children}
        </kbd>
    );
}
