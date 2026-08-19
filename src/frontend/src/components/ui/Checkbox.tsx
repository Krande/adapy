import React from "react";
import {cn} from "./cn";

// Boolean controls. Replaces ~60 hand-built checkbox rows.
//
// Checkbox vs Switch is a semantic choice, not a style one:
//   Checkbox — a value in a set, or a setting that takes effect on save/apply.
//   Switch   — takes effect immediately (every toggle in the Options drawer).
// Both render a real <input type="checkbox">, so keyboard, form participation and
// screen-reader semantics come from the platform; only the visual differs.

export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "size"> {
    label?: React.ReactNode;
    /** Neither on nor off — a parent whose children disagree. */
    indeterminate?: boolean;
    hint?: React.ReactNode;
}

export const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox(
    {label, indeterminate, hint, className, disabled, ...rest},
    ref,
) {
    const inner = React.useRef<HTMLInputElement | null>(null);

    // `indeterminate` is a DOM property with no HTML attribute, so it can only be
    // set imperatively.
    React.useEffect(() => {
        if (inner.current) inner.current.indeterminate = Boolean(indeterminate);
    }, [indeterminate]);

    const control = (
        <input
            ref={(node) => {
                inner.current = node;
                if (typeof ref === "function") ref(node);
                else if (ref) (ref as React.MutableRefObject<HTMLInputElement | null>).current = node;
            }}
            type="checkbox"
            disabled={disabled}
            className={cn(
                "ada-focus shrink-0 w-4 h-4 rounded-sm cursor-pointer",
                "appearance-none bg-surface-2 border border-edge",
                "checked:bg-accent checked:border-accent",
                "indeterminate:bg-accent indeterminate:border-accent",
                // Tick and dash drawn with CSS so they inherit the accent-fg token
                // instead of relying on the platform's own (unstylable) glyph.
                "checked:after:content-[''] checked:after:block checked:after:w-[4px] checked:after:h-[8px]",
                "checked:after:mx-auto checked:after:rotate-45 checked:after:border-accent-fg",
                "checked:after:border-r-2 checked:after:border-b-2 checked:after:-mt-px",
                "indeterminate:after:content-[''] indeterminate:after:block indeterminate:after:w-[8px]",
                "indeterminate:after:h-0 indeterminate:after:mx-auto indeterminate:after:mt-[6px]",
                "indeterminate:after:border-t-2 indeterminate:after:border-accent-fg",
                "transition-colors duration-(--ada-dur-fast)",
                "disabled:opacity-50 disabled:cursor-not-allowed",
                className,
            )}
            {...rest}
        />
    );

    if (!label) return control;

    return (
        <label
            className={cn(
                "flex items-start gap-2 text-sm text-content select-none",
                disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
            )}
        >
            {control}
            <span className="flex flex-col gap-0.5 leading-tight">
                <span>{label}</span>
                {hint && <span className="text-xs text-content-subtle">{hint}</span>}
            </span>
        </label>
    );
});

export interface SwitchProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "size"> {
    label?: React.ReactNode;
    hint?: React.ReactNode;
}

export const Switch = React.forwardRef<HTMLInputElement, SwitchProps>(function Switch(
    {label, hint, className, disabled, ...rest},
    ref,
) {
    const control = (
        <span className="relative inline-flex shrink-0">
            <input
                ref={ref}
                type="checkbox"
                role="switch"
                disabled={disabled}
                className={cn(
                    "ada-focus peer appearance-none w-8 h-[18px] rounded-pill cursor-pointer",
                    "bg-surface-3 border border-edge checked:bg-accent checked:border-accent",
                    "transition-colors duration-(--ada-dur-base)",
                    "disabled:opacity-50 disabled:cursor-not-allowed",
                    className,
                )}
                {...rest}
            />
            {/* Knob. pointer-events-none so the input underneath owns every hit. */}
            <span
                aria-hidden="true"
                className={
                    "pointer-events-none absolute top-[3px] left-[3px] w-3 h-3 rounded-full bg-content " +
                    "transition-transform duration-(--ada-dur-base) ease-(--ada-ease) " +
                    "peer-checked:translate-x-[14px] peer-checked:bg-accent-fg"
                }
            />
        </span>
    );

    if (!label) return control;

    return (
        <label
            className={cn(
                "flex items-center justify-between gap-3 text-sm text-content select-none",
                disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
            )}
        >
            <span className="flex flex-col gap-0.5 leading-tight">
                <span>{label}</span>
                {hint && <span className="text-xs text-content-subtle">{hint}</span>}
            </span>
            {control}
        </label>
    );
});
