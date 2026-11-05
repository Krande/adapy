import React from "react";

import {useIsMobile} from "@/utils/useIsMobile";

/**
 * Mobile bottom-sheet behaviour for a menu-row panel — the draggable grab handle
 * with snap points and flick-to-dismiss. The one implementation: the Scene
 * panel, the Simulation panel and the procedural cellbuilder panel all use it
 * (the cellbuilder carried a near-copy of its own until the panels were split
 * into content and frame).
 *
 * The height is stored in PIXELS (not vh): the drag math works in the visible
 * viewport (`window.innerHeight`), whereas CSS `vh` on mobile tracks the *large*
 * viewport (browser toolbar retracted) — mixing the two lets the sheet grow
 * taller than the visible area and push its grab handle off the top of the
 * screen. Pixels keep drag and layout in one space, clamped so the handle always
 * stays reachable.
 *
 * Usage: spread `grab` onto the handle element (`SheetHandle` does), put
 * `panelRef` on the sheet root, and apply `sheetStyle` (only set on mobile) to
 * that root -- or derive a style of your own from `sheetPx`. `onDismiss` fires
 * when the user flicks the sheet down small.
 */

/** Never let the sheet's top rise above this margin from the screen top. */
export const SHEET_TOP_MARGIN = 56;
/** Peek / half / full, as fractions of the viewport height. */
export const SHEET_SNAPS = [0.32, 0.58, 0.84] as const;
/** Released below this fraction of the viewport height: dismissed. */
export const SHEET_DISMISS_BELOW = 0.18;

/** The tallest the sheet may be in a visible viewport of `visibleHeight` px. */
export function sheetMaxPx(visibleHeight: number): number {
    return Math.max(120, visibleHeight - SHEET_TOP_MARGIN);
}

/** `px` clamped between the smallest sheet and `maxPx`. */
export function clampSheetPx(px: number, maxPx: number): number {
    return Math.max(80, Math.min(maxPx, px));
}

/**
 * Where a released sheet settles: `null` when it was flicked down small enough to
 * dismiss, else the nearest snap point.
 */
export function settleSheet(livePx: number, viewportHeight: number, maxPx: number): number | null {
    if (livePx < viewportHeight * SHEET_DISMISS_BELOW) return null;
    const snaps = SHEET_SNAPS.map((f) => clampSheetPx(viewportHeight * f, maxPx));
    return snaps.reduce((a, b) => (Math.abs(b - livePx) < Math.abs(a - livePx) ? b : a), snaps[0]);
}

/** Pointer handlers for the grab handle. */
export interface SheetGrab {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
    onPointerCancel: (e: React.PointerEvent) => void;
}

export interface BottomSheet {
    panelRef: React.RefObject<HTMLDivElement | null>;
    isMobile: boolean;
    /** The height the user dragged the sheet to; null until a drag. */
    sheetPx: number | null;
    /** On mobile: the height cap, and the dragged height once there is one. */
    sheetStyle: React.CSSProperties | undefined;
    grab: SheetGrab;
}

export function useBottomSheet(onDismiss: () => void): BottomSheet {
    const panelRef = React.useRef<HTMLDivElement>(null);
    const [sheetPx, setSheetPx] = React.useState<number | null>(null);
    const isMobile = useIsMobile();
    const dragRef = React.useRef<{startY: number; startPx: number; livePx: number} | null>(null);
    // Visible viewport height — visualViewport tracks the mobile URL-bar/keyboard
    // retract/expand, unlike `vh` (which is the *large* viewport) and even
    // innerHeight on some browsers. This is the number the sheet must never exceed
    // or its top (and grab handle) leave the screen and it can't be dragged closed.
    const visibleH = () => {
        if (typeof window === "undefined") return 1;
        return window.visualViewport?.height ?? window.innerHeight ?? 1;
    };
    const maxSheetPx = () => sheetMaxPx(visibleH());
    const clampPx = (px: number) => clampSheetPx(px, maxSheetPx());

    // Track the live cap so the sheet is bounded by the visible viewport even
    // BEFORE any drag (initial render) — the undragged sheet otherwise sized off a
    // `vh` CSS class and could open taller than the screen.
    const [maxPx, setMaxPx] = React.useState<number>(() =>
        typeof window === "undefined" ? 600 : maxSheetPx(),
    );

    // Re-clamp when the visible viewport shrinks (toolbar shows, rotation,
    // keyboard) so a previously-set height — or the initial cap — can't strand the
    // handle off-screen. visualViewport fires on toolbar show/hide where `resize`
    // alone may not.
    React.useEffect(() => {
        const onResize = () => {
            setMaxPx(maxSheetPx());
            setSheetPx((prev) => (prev == null ? prev : clampPx(prev)));
        };
        window.addEventListener("resize", onResize);
        window.visualViewport?.addEventListener("resize", onResize);
        return () => {
            window.removeEventListener("resize", onResize);
            window.visualViewport?.removeEventListener("resize", onResize);
        };
    }, []);

    const onGrabDown = (e: React.PointerEvent) => {
        const curPx =
            panelRef.current?.getBoundingClientRect().height ?? (window.innerHeight || 1) * 0.7;
        dragRef.current = {startY: e.clientY, startPx: curPx, livePx: curPx};
        e.currentTarget.setPointerCapture(e.pointerId);
    };
    const onGrabMove = (e: React.PointerEvent) => {
        const d = dragRef.current;
        if (!d) return;
        const px = clampPx(d.startPx + (d.startY - e.clientY)); // drag up ⇒ taller
        d.livePx = px;
        // Imperative height write — no React re-render, so the drag stays smooth
        // even while the panel body is heavy. State is reconciled once on release.
        const el = panelRef.current;
        if (el) {
            el.style.height = `${px}px`;
            el.style.maxHeight = `${px}px`;
        }
    };
    const onGrabUp = (e: React.PointerEvent) => {
        const d = dragRef.current;
        if (!d) return;
        dragRef.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
        const settled = settleSheet(d.livePx, window.innerHeight || 1, maxSheetPx());
        if (settled === null) {
            onDismiss(); // flicked down small ⇒ dismiss
            setSheetPx(null);
            return;
        }
        setSheetPx(settled);
    };

    // Always cap the sheet by the visible viewport (maxPx), even undragged, so the
    // top can never leave the screen. Height is only pinned once the user drags.
    const sheetStyle: React.CSSProperties | undefined = isMobile
        ? sheetPx != null
            ? {height: `${Math.min(sheetPx, maxPx)}px`, maxHeight: `${maxPx}px`}
            : {maxHeight: `${maxPx}px`}
        : undefined;

    const grab: SheetGrab = {
        onPointerDown: onGrabDown,
        onPointerMove: onGrabMove,
        onPointerUp: onGrabUp,
        onPointerCancel: onGrabUp,
    };

    return {panelRef, isMobile, sheetPx, sheetStyle, grab};
}
