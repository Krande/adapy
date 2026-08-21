import React from "react";
import {cn} from "./cn";

// A small exclusive choice rendered as one joined control.
//
// Distinct from Tabs: tabs switch a *region of content*, a segmented control sets a
// *value* (material mode, colormap, gizmo axis). Radio semantics, not tab semantics —
// which is why this is a radiogroup and Tabs is a tablist.
//
// This is what the mode switcher collapses to on mobile.

export interface SegmentedOption<T extends string> {
    value: T;
    label: React.ReactNode;
    /** Accessible name when `label` is an icon. */
    title?: string;
    disabled?: boolean;
}

export interface SegmentedControlProps<T extends string> {
    options: SegmentedOption<T>[];
    value: T;
    onChange: (value: T) => void;
    /** Accessible name for the group. */
    label: string;
    size?: "sm" | "md";
    block?: boolean;
    className?: string;
}

export function SegmentedControl<T extends string>({
    options,
    value,
    onChange,
    label,
    size = "md",
    block,
    className,
}: SegmentedControlProps<T>) {
    const refs = React.useRef<Record<string, HTMLButtonElement | null>>({});

    const onKeyDown = (e: React.KeyboardEvent) => {
        const enabled = options.filter((o) => !o.disabled);
        const at = enabled.findIndex((o) => o.value === value);
        if (at < 0) return;
        let next = -1;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (at + 1) % enabled.length;
        else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (at - 1 + enabled.length) % enabled.length;
        if (next < 0) return;
        e.preventDefault();
        const v = enabled[next].value;
        onChange(v);
        refs.current[v]?.focus();
    };

    return (
        <div
            role="radiogroup"
            aria-label={label}
            onKeyDown={onKeyDown}
            className={cn("inline-flex p-0.5 gap-0 bg-surface-2 border border-edge rounded-md", block && "w-full", className)}
        >
            {options.map((o) => {
                const active = o.value === value;
                return (
                    <button
                        key={o.value}
                        ref={(n) => {
                            refs.current[o.value] = n;
                        }}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        title={o.title}
                        aria-label={o.title}
                        disabled={o.disabled}
                        tabIndex={active ? 0 : -1}
                        onClick={() => onChange(o.value)}
                        className={cn(
                            "ada-focus inline-flex items-center justify-center gap-1.5 flex-1 whitespace-nowrap",
                            "font-medium rounded-sm transition-colors duration-(--ada-dur-fast)",
                            "disabled:opacity-40 disabled:pointer-events-none",
                            size === "sm" ? "h-control-sm min-h-control-sm px-2 text-xs" : "h-control-md min-h-control-md px-3 text-sm",
                            active
                                ? "bg-surface-1 text-content shadow-panel"
                                : "text-content-muted pointer-fine:hover:text-content",
                        )}
                    >
                        {o.label}
                    </button>
                );
            })}
        </div>
    );
}
