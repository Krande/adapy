import React from "react";
import {cn} from "./cn";

// Drag handle between two regions.
//
// Built in M1 rather than M2 because it is the piece the whole dock layout rests on:
// dragging it mutates one number, the CSS grid reflows, ThreeCanvas's existing
// ResizeObserver fires and three.js resizes itself. The canvas is never covered and
// never re-parented.
//
// Pointer Events (not mouse events) so pen and touch work, with setPointerCapture so
// a fast drag that leaves the 4px handle keeps tracking — the flaw in most
// hand-rolled splitters.
//
// Keyboard-resizable: it is a real ARIA separator with arrow-key support, so the
// layout is operable without a pointer.

export interface SplitterProps {
    orientation: "vertical" | "horizontal";
    /** Current size of the region being resized, in px. */
    value: number;
    onChange: (next: number) => void;
    min?: number;
    max?: number;
    /** Which side of the handle the resized region sits on. Determines drag sign. */
    side?: "before" | "after";
    /** Accessible name — say which region resizes. */
    label: string;
    /** px per arrow-key press; ×4 with shift. */
    step?: number;
    className?: string;
}

export function Splitter({
    orientation,
    value,
    onChange,
    min = 120,
    max = 900,
    side = "before",
    label,
    step = 16,
    className,
}: SplitterProps) {
    const vertical = orientation === "vertical";
    const start = React.useRef<{pos: number; size: number} | null>(null);

    const clamp = React.useCallback((n: number) => Math.min(max, Math.max(min, n)), [min, max]);

    const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
        // Ignore secondary buttons so a right-click never starts a drag.
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = {pos: vertical ? e.clientX : e.clientY, size: value};
    };

    const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
        const s = start.current;
        if (!s) return;
        const delta = (vertical ? e.clientX : e.clientY) - s.pos;
        onChange(clamp(s.size + (side === "before" ? delta : -delta)));
    };

    const end = (e: React.PointerEvent<HTMLDivElement>) => {
        if (!start.current) return;
        start.current = null;
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        const grow = vertical ? "ArrowRight" : "ArrowDown";
        const shrink = vertical ? "ArrowLeft" : "ArrowUp";
        const amount = e.shiftKey ? step * 4 : step;
        let next: number | null = null;
        if (e.key === grow) next = value + (side === "before" ? amount : -amount);
        else if (e.key === shrink) next = value - (side === "before" ? amount : -amount);
        else if (e.key === "Home") next = min;
        else if (e.key === "End") next = max;
        if (next == null) return;
        e.preventDefault();
        onChange(clamp(next));
    };

    return (
        <div
            role="separator"
            aria-orientation={vertical ? "vertical" : "horizontal"}
            aria-label={label}
            aria-valuenow={Math.round(value)}
            aria-valuemin={min}
            aria-valuemax={max}
            tabIndex={0}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={end}
            onPointerCancel={end}
            onKeyDown={onKeyDown}
            className={cn(
                "ada-focus group shrink-0 bg-transparent",
                // The hit area is larger than the visible line: 4px is the visual, but
                // a ~9px target is what makes it grabbable without pixel-hunting.
                vertical ? "w-1 cursor-col-resize -mx-1 px-1" : "h-1 cursor-row-resize -my-1 py-1",
                // touch-action none: without it the browser claims the gesture for
                // scrolling and the drag never reaches us on touch devices.
                "touch-none select-none",
                className,
            )}
        >
            <div
                aria-hidden="true"
                className={cn(
                    "w-full h-full bg-edge transition-colors duration-(--ada-dur-fast)",
                    "pointer-fine:group-hover:bg-accent group-focus-visible:bg-accent",
                )}
            />
        </div>
    );
}
