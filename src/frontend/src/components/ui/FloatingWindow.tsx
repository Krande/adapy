// A floating window over the viewer: dragged by its title bar, resized from any
// edge or corner, and on narrow screens (below the `wide` breakpoint) a
// full-screen takeover instead, because drag-resizing a 360 px window on a touch
// screen is hostile.
//
// One implementation for every window core floats. The in-viewer admin /
// convert window, the attribute search window and the node editor each wrapped
// `react-rnd` themselves, with their own copy of the mobile switch; they now
// pass what differs as props:
//
//   * where it opens -- a fixed first rect (`initialRect`), or a rect remembered
//     per browser (`remember`), which always passes through `clampRect` (on open
//     and whenever the browser window is resized) so a position saved on a larger
//     screen still opens on this one; or a rect the caller keeps (`rect`,
//     `onRectChange`), clamped the same way;
//   * how it stacks -- `zIndex` on the window, or, with `overlay`, a click-through
//     fixed layer the size of the viewport drawn in a portal, so the window's
//     coordinates are the viewport's whatever the layout around the caller;
//   * whether it takes the screen below `wide` (`fullScreenWhenNarrow`).
//
// The geometry is the pure `@/assets/floatingWindow`. The window frame (title
// bar, close button) is the caller's: see `WindowFrame`.

import React, {useEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";
import {Rnd} from "react-rnd";

import {clampRect, defaultRect, parseStoredRect, type Extent, type WindowRect} from "@/assets/floatingWindow";
import {useIsNarrow} from "@/utils/useIsMobile";

export interface RememberedRect {
  /** localStorage key the rect is kept under. */
  key: string;
  /** Size of the first open, centred near the top. */
  preferred: Extent;
}

export interface FloatingWindowProps {
  /** The class of the element the window is dragged by (its title bar). */
  dragHandleClassName: string;
  /** Stacking of the window, or of its layer with `overlay`. */
  zIndex: number;
  /**
   * The same stacking as a Tailwind class (`z-[60]`), for the full-screen takeover
   * and the overlay layer. Spelled out by the caller so Tailwind generates it.
   */
  zClassName?: string;
  minSize?: Extent;
  /** First rect, read once at mount. The window is not remembered. */
  initialRect?: WindowRect | (() => WindowRect);
  /** Remember the rect per browser instead. */
  remember?: RememberedRect;
  /** Or the caller keeps the rect: shown clamped to the viewport, every move and resize reported. */
  rect?: WindowRect;
  onRectChange?: (rect: WindowRect) => void;
  /** Draw in a portal over the page, in a click-through layer the size of the viewport. */
  overlay?: boolean;
  /** Below the `wide` breakpoint, take the whole screen. */
  fullScreenWhenNarrow?: boolean;
  /** Extra style of the window (after `zIndex`). */
  style?: React.CSSProperties;
  /** A press anywhere on the (non-takeover) window, e.g. to bring it to the front. */
  onActivate?: () => void;
  /**
   * The window's content. As a function it learns whether the window is a
   * full-screen takeover, where its title bar is no drag handle.
   */
  children: React.ReactNode | ((ctx: {fullScreen: boolean}) => React.ReactNode);
}

const viewport = (): Extent => ({width: window.innerWidth, height: window.innerHeight});

/** A remembered rect, back on screen; the first-open rect when none is stored or storage is blocked. */
export function readRememberedRect(remember: RememberedRect, min: Extent): WindowRect {
  let stored: WindowRect | null = null;
  try {
    stored = parseStoredRect(globalThis.localStorage?.getItem(remember.key) ?? null);
  } catch {
    // Blocked storage: open where a first open would.
  }
  return clampRect(stored ?? defaultRect(viewport(), remember.preferred, min), viewport(), min);
}

export function writeRememberedRect(remember: RememberedRect, r: WindowRect): void {
  try {
    globalThis.localStorage?.setItem(remember.key, JSON.stringify(r));
  } catch {
    // Remembered for this page only.
  }
}

/** `Rnd`'s press handler, passed only when set. */
function activation(onActivate?: () => void): {onMouseDown?: () => void} {
  return onActivate ? {onMouseDown: () => onActivate()} : {};
}

/** Optional props passed only when set, so an absent one reaches `Rnd` exactly as before: not at all. */
function sizeLimits(min?: Extent): {minWidth?: number; minHeight?: number} {
  return min ? {minWidth: min.width, minHeight: min.height} : {};
}

export function FloatingWindow({
  dragHandleClassName,
  zIndex,
  zClassName = "",
  minSize,
  initialRect,
  remember,
  rect: controlledRect,
  onRectChange,
  overlay = false,
  fullScreenWhenNarrow = false,
  style,
  onActivate,
  children,
}: FloatingWindowProps) {
  const narrow = useIsNarrow();
  const fullScreen = fullScreenWhenNarrow && narrow;
  const content = typeof children === "function" ? children({fullScreen}) : children;
  const min = minSize ?? {width: 0, height: 0};

  const [rect, setRect] = useState<WindowRect | null>(() =>
    remember ? readRememberedRect(remember, min) : null,
  );
  // A remembered window comes back on screen when opened and whenever the browser window changes size.
  useEffect(() => {
    if (!remember) return;
    const fit = () => setRect((r) => (r ? clampRect(r, viewport(), min) : r));
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
    // The key, not the object: callers pass a literal.
  }, [remember?.key]);

  if (fullScreen) {
    const takeover = (
      <div className={`fixed inset-0 ${zClassName} w-screen overflow-hidden`} style={{height: "100dvh"}}>
        {content}
      </div>
    );
    return overlay ? createPortal(takeover, document.body) : takeover;
  }

  const shown = controlledRect ? clampRect(controlledRect, viewport(), min) : remember ? rect : null;
  if (shown) {
    const commit = (r: WindowRect) => {
      const c = clampRect(r, viewport(), min);
      if (controlledRect) {
        onRectChange?.(c);
        return;
      }
      setRect(c);
      if (remember) writeRememberedRect(remember, c);
    };
    const rnd = (
      <Rnd
        className={overlay ? "pointer-events-auto" : undefined}
        position={{x: shown.x, y: shown.y}}
        size={{width: shown.width, height: shown.height}}
        {...sizeLimits(minSize)}
        bounds={overlay ? "parent" : "window"}
        style={overlay ? style : {zIndex, ...style}}
        dragHandleClassName={dragHandleClassName}
        {...activation(onActivate)}
        onDragStop={(_e, d) => commit({...shown, x: d.x, y: d.y})}
        onResizeStop={(_e, _dir, ref, _delta, pos) =>
          commit({x: pos.x, y: pos.y, width: ref.offsetWidth, height: ref.offsetHeight})
        }
      >
        {content}
      </Rnd>
    );
    return overlay ? createPortal(<OverlayLayer zClassName={zClassName}>{rnd}</OverlayLayer>, document.body) : rnd;
  }

  return <UncontrolledWindow {...{dragHandleClassName, zIndex, zClassName, minSize, initialRect, overlay, style, onActivate}}>{content}</UncontrolledWindow>;
}

const OverlayLayer: React.FC<{zClassName: string; children: React.ReactNode}> = ({zClassName, children}) => (
  // A fixed, click-through layer the size of the viewport: the window's coordinates are the
  // viewport's, and it can be dragged anywhere on it.
  <div className={`fixed inset-0 ${zClassName} pointer-events-none`}>{children}</div>
);

/** A window that opens at a fixed rect and is not remembered: `Rnd` keeps its own position. */
function UncontrolledWindow({
  dragHandleClassName,
  zIndex,
  zClassName = "",
  minSize,
  initialRect,
  overlay,
  style,
  onActivate,
  children,
}: Omit<FloatingWindowProps, "children" | "remember" | "fullScreenWhenNarrow"> & {children: React.ReactNode}) {
  const first = useRef<WindowRect | null>(null);
  if (first.current === null) {
    const r = typeof initialRect === "function" ? initialRect() : initialRect;
    first.current = r ?? defaultRect(viewport(), {width: 800, height: 600}, minSize ?? {width: 0, height: 0});
  }
  const rnd = (
    <Rnd
      className={overlay ? "pointer-events-auto" : undefined}
      default={first.current}
      {...sizeLimits(minSize)}
      bounds={overlay ? "parent" : "window"}
      style={overlay ? style : {zIndex, ...style}}
      dragHandleClassName={dragHandleClassName}
      {...activation(onActivate)}
    >
      {children}
    </Rnd>
  );
  return overlay ? createPortal(<OverlayLayer zClassName={zClassName}>{rnd}</OverlayLayer>, document.body) : rnd;
}

/**
 * A pointer drag in raw pixels: `begin` on pointerdown (it captures the pointer),
 * spread the rest on the same element. `apply` receives the value captured at
 * the start and the pointer's offset from where the drag began, on every move.
 * For hand-drawn drag and resize handles; a window uses `FloatingWindow`.
 */
export function usePointerDrag<S>(apply: (start: S, dx: number, dy: number) => void) {
  const applyRef = useRef(apply);
  applyRef.current = apply;
  const drag = useRef<{sx: number; sy: number; start: S} | null>(null);
  const begin = (e: React.PointerEvent, start: S) => {
    e.preventDefault();
    drag.current = {sx: e.clientX, sy: e.clientY, start};
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    applyRef.current(d.start, e.clientX - d.sx, e.clientY - d.sy);
  };
  const end = (e: React.PointerEvent) => {
    if (!drag.current) return;
    drag.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };
  return {begin, onPointerMove, onPointerUp: end, onPointerCancel: end};
}
