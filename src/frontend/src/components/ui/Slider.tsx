import React from "react";
import {cn} from "./cn";

// A range input with an optional numeric readout.
//
// Three places wanted one (point size, pixel-ratio cap, theme opacity) and each had
// hand-styled its own, so the track, thumb and readout all differed. Native
// <input type="range"> underneath: dragging a custom-built slider correctly — pointer
// capture, keyboard steps, touch — is a lot of code the platform already has right.
//
// `readout` is opt-in because some values are self-evident from the thing they change
// (opacity) and some are not (a pixel-ratio cap of 1.75).

export interface SliderProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "type" | "size" | "value" | "onChange"> {
    value: number;
    onValueChange: (n: number) => void;
    min: number;
    max: number;
    step?: number;
    /** Show the current value beside the track. */
    readout?: boolean;
    /** How to render the readout — defaults to the raw number. */
    format?: (n: number) => string;
    label?: React.ReactNode;
}

export const Slider = React.forwardRef<HTMLInputElement, SliderProps>(function Slider(
    {value, onValueChange, min, max, step, readout, format, label, className, disabled, ...rest},
    ref,
) {
    const track = (
        <input
            ref={ref}
            type="range"
            min={min}
            max={max}
            step={step}
            value={value}
            disabled={disabled}
            onChange={(e) => onValueChange(parseFloat(e.target.value))}
            className={cn(
                "ada-focus flex-1 min-w-0 h-control-sm accent-accent cursor-pointer",
                // `no-drag` keeps react-rnd from treating a drag on the track as a drag of
                // the whole panel — the classic UI floats these in draggable windows, and
                // without it the slider is unusable there.
                "no-drag",
                disabled && "opacity-50 cursor-default",
                className,
            )}
            {...rest}
        />
    );

    // `w-full min-w-0` matters: this wrapper is often dropped into a flex row next to a
    // number field. Without a width it resolves to flex-basis auto over zero-width content,
    // collapses to 0, and the track disappears under its neighbour — which is exactly what
    // the point-size row did. w-full sets the basis to the full row and min-w-0 lets it
    // shrink so the sibling field still fits.
    const body = (
        <div className="flex w-full min-w-0 items-center gap-2">
            {track}
            {readout && (
                <span className="shrink-0 w-14 text-right text-xs font-mono tabular-nums text-content-muted">
                    {format ? format(value) : value}
                </span>
            )}
        </div>
    );

    if (!label) return body;

    return (
        <label className="flex flex-col gap-1 min-w-0">
            <span className="text-xs text-content-muted">{label}</span>
            {body}
        </label>
    );
});
