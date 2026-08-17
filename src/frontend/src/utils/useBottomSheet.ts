import React from "react";

/**
 * Mobile bottom-sheet behaviour for a menu-row panel — the draggable grab handle
 * with snap points and flick-to-dismiss, first built for the procedural
 * CellBuilderPanel and shared here so the Scene panel behaves identically.
 *
 * The height is stored in PIXELS (not vh): the drag math works in the visible
 * viewport (`window.innerHeight`), whereas CSS `vh` on mobile tracks the *large*
 * viewport (browser toolbar retracted) — mixing the two lets the sheet grow
 * taller than the visible area and push its grab handle off the top of the
 * screen. Pixels keep drag and layout in one space, clamped so the handle always
 * stays reachable.
 *
 * Usage: spread `grab` onto the handle element, put `panelRef` on the sheet root,
 * and apply `sheetStyle` (only set on mobile) to that root. `onDismiss` fires
 * when the user flicks the sheet down small.
 */
export function useBottomSheet(onDismiss: () => void) {
    const panelRef = React.useRef<HTMLDivElement>(null);
    const [sheetPx, setSheetPx] = React.useState<number | null>(null);
    const [isMobile, setIsMobile] = React.useState(
        () =>
            typeof window !== "undefined" &&
            window.matchMedia("(max-width: 639px)").matches,
    );
    const dragRef = React.useRef<{startY: number; startPx: number; livePx: number} | null>(null);
    // Never let the sheet's top rise above this margin from the screen top.
    const TOP_MARGIN = 56;
    // Visible viewport height — visualViewport tracks the mobile URL-bar/keyboard
    // retract/expand, unlike `vh` (which is the *large* viewport) and even
    // innerHeight on some browsers. This is the number the sheet must never exceed
    // or its top (and grab handle) leave the screen and it can't be dragged closed.
    const visibleH = () => {
        if (typeof window === "undefined") return 1;
        return window.visualViewport?.height ?? window.innerHeight ?? 1;
    };
    const maxSheetPx = () => Math.max(120, visibleH() - TOP_MARGIN);
    const clampPx = (px: number) => Math.max(80, Math.min(maxSheetPx(), px));

    // Track the live cap so the sheet is bounded by the visible viewport even
    // BEFORE any drag (initial render) — the undragged sheet otherwise sized off a
    // `vh` CSS class and could open taller than the screen.
    const [maxPx, setMaxPx] = React.useState<number>(() =>
        typeof window === "undefined" ? 600 : maxSheetPx(),
    );

    React.useEffect(() => {
        const mq = window.matchMedia("(max-width: 639px)");
        const on = () => setIsMobile(mq.matches);
        on();
        mq.addEventListener("change", on);
        return () => mq.removeEventListener("change", on);
    }, []);

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
        const vh = window.innerHeight || 1;
        if (d.livePx < vh * 0.18) {
            onDismiss(); // flicked down small ⇒ dismiss
            setSheetPx(null);
            return;
        }
        const snaps = [0.32, 0.58, 0.84].map((f) => clampPx(vh * f)); // peek / half / full
        setSheetPx(
            snaps.reduce((a, b) => (Math.abs(b - d.livePx) < Math.abs(a - d.livePx) ? b : a), snaps[0]),
        );
    };

    // Always cap the sheet by the visible viewport (maxPx), even undragged, so the
    // top can never leave the screen. Height is only pinned once the user drags.
    const sheetStyle: React.CSSProperties | undefined = isMobile
        ? sheetPx != null
            ? {height: `${Math.min(sheetPx, maxPx)}px`, maxHeight: `${maxPx}px`}
            : {maxHeight: `${maxPx}px`}
        : undefined;

    const grab = {
        onPointerDown: onGrabDown,
        onPointerMove: onGrabMove,
        onPointerUp: onGrabUp,
        onPointerCancel: onGrabUp,
    };

    return {panelRef, isMobile, sheetStyle, grab};
}
