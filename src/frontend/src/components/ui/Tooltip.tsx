import React from "react";
import {cn} from "./cn";

// Hover/focus tooltip rendered in a portal.
//
// Portal rather than a positioned child because tooltips must escape the
// `overflow: auto` of a scrolling panel body — the reason the current UI falls back
// to native `title=` everywhere, which cannot be styled, cannot be triggered by
// keyboard focus, and has a ~1s delay you cannot change.
//
// IconButton still sets a native `title` as well. That is deliberate belt-and-braces:
// the accessible name must survive even where this component is not used.

export interface TooltipProps {
    content: React.ReactNode;
    side?: "top" | "bottom" | "left" | "right";
    /** Milliseconds before showing on hover. Focus always shows immediately —
     *  a keyboard user has already committed to the control. */
    delay?: number;
    /** Single focusable element. */
    children: React.ReactElement;
}

const GAP = 6;

export function Tooltip({content, side = "top", delay = 400, children}: TooltipProps) {
    const [open, setOpen] = React.useState(false);
    const [pos, setPos] = React.useState<{top: number; left: number} | null>(null);
    const anchor = React.useRef<HTMLElement | null>(null);
    const timer = React.useRef<number | undefined>(undefined);
    const id = React.useId();

    const place = React.useCallback(() => {
        const el = anchor.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        const at = {
            top: {top: r.top - GAP, left: r.left + r.width / 2},
            bottom: {top: r.bottom + GAP, left: r.left + r.width / 2},
            left: {top: r.top + r.height / 2, left: r.left - GAP},
            right: {top: r.top + r.height / 2, left: r.right + GAP},
        }[side];
        setPos(at);
    }, [side]);

    const show = React.useCallback(
        (immediate: boolean) => {
            window.clearTimeout(timer.current);
            const go = () => {
                place();
                setOpen(true);
            };
            if (immediate) go();
            else timer.current = window.setTimeout(go, delay);
        },
        [delay, place],
    );

    const hide = React.useCallback(() => {
        window.clearTimeout(timer.current);
        setOpen(false);
    }, []);

    React.useEffect(() => () => window.clearTimeout(timer.current), []);

    // Escape closes without moving focus — the tooltip must never trap the user.
    React.useEffect(() => {
        if (!open) return;
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") hide();
        };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [open, hide]);

    const child = React.cloneElement(children as React.ReactElement<Record<string, unknown>>, {
        ref: (node: HTMLElement | null) => {
            anchor.current = node;
            const r = (children as unknown as {ref?: unknown}).ref;
            if (typeof r === "function") (r as (n: HTMLElement | null) => void)(node);
            else if (r && typeof r === "object") (r as React.MutableRefObject<HTMLElement | null>).current = node;
        },
        "aria-describedby": open ? id : undefined,
        onPointerEnter: () => show(false),
        onPointerLeave: hide,
        onFocus: () => show(true),
        onBlur: hide,
    });

    const translate = {
        top: "translate(-50%, -100%)",
        bottom: "translate(-50%, 0)",
        left: "translate(-100%, -50%)",
        right: "translate(0, -50%)",
    }[side];

    return (
        <>
            {child}
            {open && pos && (
                <span
                    id={id}
                    role="tooltip"
                    style={{
                        position: "fixed",
                        top: pos.top,
                        left: pos.left,
                        transform: translate,
                        zIndex: "var(--ada-z-context-menu)" as unknown as number,
                    }}
                    className={cn(
                        "pointer-events-none max-w-64 px-1.5 py-1 rounded-sm",
                        "bg-surface-0 text-content text-xs leading-tight",
                        "border border-edge shadow-popover",
                    )}
                >
                    {content}
                </span>
            )}
        </>
    );
}
