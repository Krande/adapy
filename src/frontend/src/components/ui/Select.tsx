import React from "react";
import {cn} from "./cn";
import {FIELD_BASE, type FieldSize} from "./Input";

// A styled NATIVE <select>, not a custom listbox.
//
// There are 77 <select> call sites. A custom listbox would mean re-implementing
// keyboard type-ahead, option virtualisation, mobile pickers and screen-reader
// semantics that the platform already gets right — for a control that is, in this
// app, almost always a short list of formats or scopes. A custom Combobox comes
// later (Tier 2) for the cases that genuinely need search or multi-select.
//
// The only real cost is the dropdown arrow: `appearance-none` plus a background SVG,
// since the native arrow cannot be recoloured.

const SIZE: Record<FieldSize, string> = {
    sm: "h-control-sm min-h-control-sm pl-1.5 pr-6 text-xs rounded-sm",
    md: "h-control-md min-h-control-md pl-2 pr-7 text-sm rounded-md",
    lg: "h-control-lg min-h-control-lg pl-2.5 pr-8 text-base rounded-md",
};

// Chevron as a data URI so it needs no network request and no icon component.
// `currentColor` cannot be used inside a background image, so this is the muted
// text colour baked in; it reads acceptably on every preset.
const CHEVRON =
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")";

export interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, "size"> {
    fieldSize?: FieldSize;
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(function Select(
    {fieldSize = "md", className, style, children, ...rest},
    ref,
) {
    return (
        <select
            ref={ref}
            className={cn(FIELD_BASE, SIZE[fieldSize], "appearance-none bg-no-repeat cursor-pointer", className)}
            style={{
                backgroundImage: CHEVRON,
                backgroundPosition: "right 6px center",
                ...style,
            }}
            {...rest}
        >
            {children}
        </select>
    );
});
