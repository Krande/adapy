/**
 * The mobile BOTTOM-SHEET drag.
 *
 * Owns: the phone layout's grab handle — drag the sheet taller or shorter, flick
 * down to dismiss, snap to peek/half/full on release. Heights are kept in PIXELS
 * of the visible viewport, not vh: CSS vh on mobile means the LARGE viewport
 * (toolbar retracted), and mixing the two let the sheet push its own grab handle
 * off the top of the screen. During a drag the height is written imperatively so
 * a heavy panel body never makes the gesture sluggish.
 */

import React from "react";


export interface SheetDrag {
  panelRef: React.RefObject<HTMLDivElement | null>;
  isMobile: boolean;
  sheetPx: number | null;
  onGrabDown: (e: React.PointerEvent) => void;
  onGrabMove: (e: React.PointerEvent) => void;
  onGrabUp: (e: React.PointerEvent) => void;
}

/** `dismiss` closes the sheet when it is flicked down small. */
export function useSheetDrag(dismiss: () => void): SheetDrag {
  const panelRef = React.useRef<HTMLDivElement>(null);
  // Sheet height is stored in PIXELS (not vh). The drag math works in the
  // visible viewport (window.innerHeight), whereas CSS `vh` on mobile refers to
  // the *large* viewport (browser toolbar retracted) — mixing the two let the
  // sheet grow taller than the visible area and push its grab handle above the
  // top of the screen, out of reach. Pixels keep drag and layout in one space.
  const [sheetPx, setSheetPx] = React.useState<number | null>(null);
  // Lazily seed from matchMedia so the very first render already knows it's
  // mobile — children (e.g. the Cells & equipment section) read this to pick
  // their initial collapsed state, which useState captures once at mount.
  const [isMobile, setIsMobile] = React.useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(max-width: 639px)").matches,
  );
  // During an active drag we mutate the panel height imperatively (see
  // onGrabMove) instead of via setState — re-rendering this whole panel on every
  // pointermove is what made the drag sluggish on mid-range phones. `livePx`
  // carries the current height across move events so onGrabUp can snap from it.
  const dragRef = React.useRef<{ startY: number; startPx: number; livePx: number } | null>(
    null,
  );
  // Never let the sheet's top rise above this margin from the screen top, so the
  // grab handle (and thus the ability to shrink/dismiss it) is always reachable.
  const TOP_MARGIN = 56;
  const maxSheetPx = () => Math.max(120, (window.innerHeight || 1) - TOP_MARGIN);
  const clampPx = (px: number) => Math.max(80, Math.min(maxSheetPx(), px));
  React.useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const on = () => setIsMobile(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  // When the viewport shrinks (mobile toolbar shows, rotation, keyboard) re-clamp
  // so a previously-set height can't leave the handle stranded off-screen.
  React.useEffect(() => {
    const onResize = () =>
      setSheetPx((prev) => (prev == null ? prev : clampPx(prev)));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const onGrabDown = (e: React.PointerEvent) => {
    const curPx =
      panelRef.current?.getBoundingClientRect().height ??
      (window.innerHeight || 1) * 0.82;
    dragRef.current = { startY: e.clientY, startPx: curPx, livePx: curPx };
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
      dismiss(); // flicked down small ⇒ dismiss the sheet
      setSheetPx(null);
      return;
    }
    const snaps = [0.32, 0.58, 0.84].map((f) => clampPx(vh * f)); // peek / half / full
    const snapped = snaps.reduce(
      (a, b) => (Math.abs(b - d.livePx) < Math.abs(a - d.livePx) ? b : a),
      snaps[0],
    );
    setSheetPx(snapped);
  };

  return {panelRef, isMobile, sheetPx, onGrabDown, onGrabMove, onGrabUp};
}
