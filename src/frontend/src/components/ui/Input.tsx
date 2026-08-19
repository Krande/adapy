import React from "react";
import {cn} from "./cn";

// Text-entry controls. Replaces ~115 hand-styled <input> and 2 <textarea>.

export type FieldSize = "sm" | "md" | "lg";

const SIZE: Record<FieldSize, string> = {
    sm: "h-control-sm min-h-control-sm px-1.5 text-xs rounded-sm",
    md: "h-control-md min-h-control-md px-2 text-sm rounded-md",
    lg: "h-control-lg min-h-control-lg px-2.5 text-base rounded-md",
};

export const FIELD_BASE =
    "ada-focus w-full bg-surface-2 text-content border border-edge " +
    "placeholder:text-content-subtle transition-colors duration-(--ada-dur-fast) " +
    "pointer-fine:hover:border-edge-strong " +
    "disabled:opacity-50 disabled:pointer-events-none " +
    // Invalid state is driven by aria-invalid rather than a prop, so native
    // constraint validation and manual validation converge on one visual.
    "aria-[invalid=true]:border-fail aria-[invalid=true]:bg-fail-subtle";

export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "size"> {
    fieldSize?: FieldSize;
    /** Monospace — for ids, keys, coordinates and other machine values. */
    mono?: boolean;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
    {fieldSize = "md", mono, className, ...rest},
    ref,
) {
    return (
        <input
            ref={ref}
            className={cn(FIELD_BASE, SIZE[fieldSize], mono && "font-mono", className)}
            {...rest}
        />
    );
});

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
    mono?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
    {mono, className, rows = 3, ...rest},
    ref,
) {
    return (
        <textarea
            ref={ref}
            rows={rows}
            className={cn(FIELD_BASE, "py-1 px-2 text-sm rounded-md h-auto", mono && "font-mono", className)}
            {...rest}
        />
    );
});

/**
 * Label + control + optional hint/error, wired together.
 *
 * Exists because the audit found `flex flex-col gap-0.5` + `text-xs text-gray-400`
 * repeated 25 and 19 times respectively — always this pattern, always hand-built,
 * and usually with the label not actually associated with its control.
 */
export interface FieldProps {
    label: React.ReactNode;
    /** Supply when the control has its own id; otherwise one is generated. */
    htmlFor?: string;
    hint?: React.ReactNode;
    error?: React.ReactNode;
    required?: boolean;
    className?: string;
    children: React.ReactNode;
}

export function Field({label, htmlFor, hint, error, required, className, children}: FieldProps) {
    const auto = React.useId();
    const id = htmlFor ?? auto;
    const describedBy = error ? `${id}-err` : hint ? `${id}-hint` : undefined;

    return (
        <div className={cn("flex flex-col gap-1", className)}>
            <label htmlFor={id} className="text-xs text-content-muted">
                {label}
                {required && <span className="text-fail ml-0.5" aria-hidden="true">*</span>}
            </label>
            {/* Clone so the caller writes <Field label="X"><Input/></Field> without
                having to thread id/aria wiring by hand. */}
            {React.isValidElement(children)
                ? React.cloneElement(children as React.ReactElement<Record<string, unknown>>, {
                      id,
                      "aria-describedby": describedBy,
                      "aria-invalid": error ? true : undefined,
                  })
                : children}
            {error ? (
                <span id={`${id}-err`} role="alert" className="text-xs text-fail">
                    {error}
                </span>
            ) : hint ? (
                <span id={`${id}-hint`} className="text-xs text-content-subtle">
                    {hint}
                </span>
            ) : null}
        </div>
    );
}
