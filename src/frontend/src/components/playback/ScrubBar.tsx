// The transport's scrub bar: the clock's time over a window of the timeline (all of it, or the part
// it is zoomed into), with the markers sources put on it.
//
// - A press seeks there and a drag scrubs. Hold Shift, or pull the pointer up or down off the bar,
//   and the drag slows to 1/10 (further: 1/100), so a pixel is a few milliseconds; on touch a long
//   press before the drag does the same. The speed shows beside the handle while it is slowed.
// - The wheel or a pinch over the bar zooms about the time under it; a drag along the strip above
//   the bar (or a Ctrl-drag on it) zooms into the range dragged over. The transport resets it.
// - A marker is a tick with its label as the tooltip; a click on it goes there.
// The arithmetic is state/playbackTimeline.ts. No three.js in this module.

import React, { useEffect, useRef, useState } from "react";

import type { PlaybackMarkerEntry, PlaybackMarkerKind } from "@/state/playbackClock";
import {
  preciseClockText,
  scrubScale,
  scrubTime,
  timeToFraction,
  type TimeWindow,
  wheelZoomFactor,
  windowFromSelection,
  xToTime,
  zoomAt,
} from "@/state/playbackTimeline";

/** A marker's tick colour by kind, where it gives none of its own. */
export const MARKER_COLORS: Readonly<Record<PlaybackMarkerKind, string>> = {
  stage: "#60a5fa",
  event: "#e5e7eb",
  warning: "#f59e0b",
  error: "#ef4444",
};

/** How long a touch is held still before its drag scrubs finely (ms), and how far it may move meanwhile (px). */
const LONG_PRESS_MS = 400;
const LONG_PRESS_SLOP_PX = 6;
/** A press this close to the handle (px) takes it where it is instead of jumping to the press. */
const HANDLE_GRAB_PX = 8;

export interface ScrubBarProps {
  time: number;
  duration: number;
  /** The part of the timeline the bar shows. */
  view: TimeWindow;
  markers: readonly PlaybackMarkerEntry[];
  onSeek(t: number): void;
  /** Zoom into a window (null: all of it). */
  onZoom(view: TimeWindow | null): void;
}

type Drag =
  | { kind: "scrub"; id: number; lastX: number; time: number; fine: boolean; touch: boolean; moved: boolean; startX: number; startY: number; timer: ReturnType<typeof setTimeout> | null }
  | { kind: "select"; id: number; from: number; to: number }
  | { kind: "pinch"; ids: [number, number]; dist: number; at: number; view: TimeWindow };

export const markerTitle = (m: PlaybackMarkerEntry): string => `${m.label || m.kind} · ${preciseClockText(m.t)}`;

const ScrubBar: React.FC<ScrubBarProps> = ({ time, duration, view, markers, onSeek, onZoom }) => {
  const barRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<Drag | null>(null);
  const touches = useRef(new Map<number, number>());
  const [scale, setScale] = useState(1);
  const [selection, setSelection] = useState<{ from: number; to: number } | null>(null);
  // Read by the native listeners, which are added once.
  const latest = useRef({ view, duration, onZoom });
  latest.current = { view, duration, onZoom };

  // The wheel zooms: a listener of the element's own, since React's is passive and cannot keep the
  // page from scrolling.
  useEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const onWheel = (e: WheelEvent) => {
      const { view: v, duration: d, onZoom: zoom } = latest.current;
      if (!(d > 0) || e.deltaY === 0) return;
      e.preventDefault();
      const rect = bar.getBoundingClientRect();
      zoom(zoomAt(v, xToTime(e.clientX - rect.left, rect.width, v), wheelZoomFactor(e.deltaY), d));
    };
    bar.addEventListener("wheel", onWheel, { passive: false });
    return () => bar.removeEventListener("wheel", onWheel);
  }, []);

  const span = view.end - view.start;
  const fraction = Math.max(0, Math.min(1, timeToFraction(Math.min(time, duration), view)));
  const shownMarkers = markers.filter((m) => m.t >= view.start - 1e-9 && m.t <= view.end + 1e-9);

  const rect = () => barRef.current?.getBoundingClientRect() ?? null;
  const timeAt = (clientX: number): number => {
    const r = rect();
    return r ? xToTime(clientX - r.left, r.width, view) : time;
  };
  /** How far (px) the pointer is above or below the bar. */
  const pullOf = (clientY: number): number => {
    const r = rect();
    if (!r) return 0;
    return clientY < r.top ? r.top - clientY : clientY > r.bottom ? clientY - r.bottom : 0;
  };

  const endDrag = () => {
    const d = drag.current;
    if (d?.kind === "scrub" && d.timer) clearTimeout(d.timer);
    drag.current = null;
    setScale(1);
    setSelection(null);
  };

  const startPinch = () => {
    const [a, b] = [...touches.current.entries()];
    if (!a || !b) return;
    const d = drag.current;
    if (d?.kind === "scrub" && d.timer) clearTimeout(d.timer);
    drag.current = { kind: "pinch", ids: [a[0], b[0]], dist: Math.max(1, Math.abs(a[1] - b[1])), at: timeAt((a[1] + b[1]) / 2), view };
    setScale(1);
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    if (e.pointerType === "touch") {
      touches.current.set(e.pointerId, e.clientX);
      if (touches.current.size === 2) return startPinch();
    }
    if (e.ctrlKey || e.metaKey) {
      const t = timeAt(e.clientX);
      drag.current = { kind: "select", id: e.pointerId, from: t, to: t };
      setSelection({ from: t, to: t });
      return;
    }
    const touch = e.pointerType === "touch";
    const r = rect();
    const handleX = r ? r.left + fraction * r.width : e.clientX;
    const onHandle = Math.abs(e.clientX - handleX) <= HANDLE_GRAB_PX;
    // A mouse press jumps there at once; a touch waits to see whether it is a tap, a drag or a hold.
    let t = time;
    if (!touch && !onHandle) {
      t = timeAt(e.clientX);
      onSeek(t);
    }
    const scrub: Drag = { kind: "scrub", id: e.pointerId, lastX: e.clientX, time: t, fine: false, touch, moved: false, startX: e.clientX, startY: e.clientY, timer: null };
    if (touch) {
      scrub.timer = setTimeout(() => {
        const d = drag.current;
        if (d?.kind !== "scrub" || d.moved) return;
        d.fine = true;
        d.timer = null;
        setScale(scrubScale({ fine: true }));
      }, LONG_PRESS_MS);
    }
    drag.current = scrub;
    if (e.shiftKey) setScale(scrubScale({ shift: true }));
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pinch") {
      if (!touches.current.has(e.pointerId)) return;
      touches.current.set(e.pointerId, e.clientX);
      const [a, b] = d.ids.map((id) => touches.current.get(id) ?? 0);
      onZoom(zoomAt(d.view, d.at, Math.max(1, Math.abs(a - b)) / d.dist, duration));
      return;
    }
    if (e.pointerId !== d.id) return;
    if (d.kind === "select") {
      d.to = timeAt(e.clientX);
      setSelection({ from: d.from, to: d.to });
      return;
    }
    if (d.touch) touches.current.set(e.pointerId, e.clientX);
    if (!d.moved && Math.hypot(e.clientX - d.startX, e.clientY - d.startY) < (d.touch ? LONG_PRESS_SLOP_PX : 1)) return;
    if (!d.moved && d.timer) {
      clearTimeout(d.timer);
      d.timer = null;
    }
    // A touch that moves before its long press scrubs from where it is, like a press of the mouse.
    if (!d.moved && d.touch && !d.fine) {
      d.time = timeAt(d.startX);
    }
    d.moved = true;
    const r = rect();
    const s = scrubScale({ shift: e.shiftKey, pullPx: pullOf(e.clientY), fine: d.fine });
    if (s !== scale) setScale(s);
    if (s === 1 && r) {
      d.time = xToTime(e.clientX - r.left, r.width, view);
    } else if (r) {
      d.time = scrubTime(d.time, e.clientX - d.lastX, r.width / (span || 1), s, view.start, view.end);
    }
    d.lastX = e.clientX;
    onSeek(d.time);
  };

  const onPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    touches.current.delete(e.pointerId);
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pinch") {
      if (touches.current.size < 2) endDrag();
      return;
    }
    if (e.pointerId !== d.id) return;
    if (d.kind === "select") {
      const w = windowFromSelection(d.from, d.to, duration);
      if (w) onZoom(w);
    } else if (d.touch && !d.moved && !d.fine) {
      // A tap: there.
      onSeek(timeAt(e.clientX));
    }
    endDrag();
  };

  // The strip above the bar: a drag along it zooms into the range dragged over.
  const stripDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    const t = timeAt(e.clientX);
    drag.current = { kind: "select", id: e.pointerId, from: t, to: t };
    setSelection({ from: t, to: t });
  };

  const pct = (t: number) => `${(Math.max(0, Math.min(1, timeToFraction(t, view))) * 100).toFixed(3)}%`;

  return (
    <div className="flex min-w-0 flex-1 flex-col" data-playback-scrub-wrap="true">
      <div
        className="relative h-2 cursor-crosshair touch-none rounded-sm hover:bg-white/10"
        title="Drag along here to zoom into a range"
        aria-hidden="true"
        data-playback-zoom-strip="true"
        onPointerDown={stripDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={endDrag}
      >
        {selection && (
          <div
            className="absolute inset-y-0 rounded-sm bg-blue-400/50"
            style={{ left: pct(Math.min(selection.from, selection.to)), width: `calc(${pct(Math.max(selection.from, selection.to))} - ${pct(Math.min(selection.from, selection.to))})` }}
          />
        )}
      </div>
      <div
        ref={barRef}
        role="slider"
        tabIndex={0}
        aria-label="Time"
        aria-valuemin={0}
        aria-valuemax={duration}
        aria-valuenow={Math.min(time, duration)}
        aria-valuetext={preciseClockText(time)}
        className="relative h-8 cursor-pointer touch-none select-none sm:h-5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300 rounded-sm"
        data-playback-scrub="true"
        data-scrub-scale={scale}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={(e) => {
          touches.current.delete(e.pointerId);
          endDrag();
        }}
      >
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-white/25" />
        <div className="absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-400" style={{ width: `${(fraction * 100).toFixed(3)}%` }} />
        {selection && (
          <div
            className="absolute inset-y-0 bg-blue-400/30"
            style={{ left: pct(Math.min(selection.from, selection.to)), width: `calc(${pct(Math.max(selection.from, selection.to))} - ${pct(Math.min(selection.from, selection.to))})` }}
          />
        )}
        {shownMarkers.map((m, i) => (
          <button
            key={`${m.source}:${i}:${m.t}`}
            type="button"
            tabIndex={-1}
            className="absolute top-0 h-full w-2 -translate-x-1/2 p-0"
            style={{ left: pct(m.t) }}
            title={markerTitle(m)}
            aria-label={markerTitle(m)}
            data-playback-marker={m.kind}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onSeek(m.t);
            }}
          >
            <span className="mx-auto block h-full w-0.5 rounded-sm" style={{ background: m.color ?? MARKER_COLORS[m.kind] }} />
          </button>
        ))}
        <div
          className="pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-blue-500 shadow"
          style={{ left: `${(fraction * 100).toFixed(3)}%` }}
          data-playback-handle="true"
        />
        {scale < 1 && (
          <span
            className="pointer-events-none absolute -top-5 -translate-x-1/2 rounded-sm bg-black/80 px-1 text-[10px] tabular-nums"
            style={{ left: `${(fraction * 100).toFixed(3)}%` }}
            data-playback-fine="true"
          >
            ×{scale}
          </span>
        )}
      </div>
    </div>
  );
};

export default ScrubBar;
