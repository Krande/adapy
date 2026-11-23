// The arithmetic of the playback clock's timeline (./playbackClock.ts) and its transport
// (components/playback/PlaybackTransport.tsx): step sizes and where a step lands, the fine-scrub
// speed of a drag, the zoomed window of the scrub bar, the precise time readout and what a typed
// time means, and the name of a captured frame. Pure functions, no DOM and no three.js, so each is
// tested on its own (__tests__/state/playbackTimeline.test.ts).

// ── Step sizes ───────────────────────────────────────────────────────────────

/** A step of the transport's ◀| / |▶ and ← / →: one recorded sample (or display frame where the
 *  timeline has no samples), or a number of seconds. */
export type PlaybackStepSize = "frame" | number;

/** The sizes the transport offers, smallest first. "frame" is the default. */
export const STEP_SIZES: readonly PlaybackStepSize[] = ["frame", 0.01, 0.1, 1, 10];

export function stepSizeLabel(size: PlaybackStepSize): string {
  if (size === "frame") return "1 frame";
  if (size < 1) return `${Math.round(size * 1000)} ms`;
  return `${size} s`;
}

/** A step as it is taken: `count` samples (frames), or `seconds`. */
export type ResolvedStep = { kind: "samples"; count: number } | { kind: "seconds"; seconds: number };

/** The step a key press or a button press takes: Shift makes it ten times larger, Alt ten times
 *  smaller. A frame step ×10 is ten samples; ÷10 is a tenth of a display frame, in time (for a
 *  source that interpolates between its samples). */
export function resolveStep(size: PlaybackStepSize, mods: { shift?: boolean; alt?: boolean } = {}, frameSeconds = 1 / 60): ResolvedStep {
  const factor = (mods.shift ? 10 : 1) / (mods.alt ? 10 : 1);
  if (size === "frame") {
    if (factor >= 1) return { kind: "samples", count: Math.round(factor) };
    return { kind: "seconds", seconds: frameSeconds * factor };
  }
  return { kind: "seconds", seconds: size * factor };
}

/** Two times closer than this are the same moment (a sample is not "after" itself). */
export const TIME_EPSILON = 1e-6;

/** The first index of ascending `times` whose value is greater than `t + eps`. */
function upperBound(times: ArrayLike<number>, t: number): number {
  let lo = 0;
  let hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] > t + TIME_EPSILON) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/** The first index of ascending `times` whose value is at least `t - eps`. */
function lowerBound(times: ArrayLike<number>, t: number): number {
  let lo = 0;
  let hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] >= t - TIME_EPSILON) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}

/** The sample `count` samples after (`direction` 1) or before (-1) `t` in ascending `times`; null
 *  when there is none that way. A `t` between two samples steps to the next one, not past it. */
export function stepSamples(times: ArrayLike<number>, t: number, direction: 1 | -1, count = 1): number | null {
  if (!times.length || count < 1) return null;
  if (direction > 0) {
    const i = upperBound(times, t) + count - 1;
    return i < times.length ? times[i] : null;
  }
  const i = lowerBound(times, t) - count;
  return i >= 0 ? times[i] : null;
}

/** The sample nearest to `t`, or null for none. */
export function nearestSample(times: ArrayLike<number>, t: number): number | null {
  if (!times.length) return null;
  const i = lowerBound(times, t);
  if (i <= 0) return times[0];
  if (i >= times.length) return times[times.length - 1];
  return t - times[i - 1] <= times[i] - t ? times[i - 1] : times[i];
}

/** Several ascending sample lists as one, ascending, without repeats (within `TIME_EPSILON`). */
export function mergeSamples(lists: readonly ArrayLike<number>[]): Float64Array {
  let total = 0;
  for (const l of lists) total += l.length;
  const all = new Float64Array(total);
  let n = 0;
  for (const l of lists) for (let i = 0; i < l.length; i++) if (Number.isFinite(l[i])) all[n++] = l[i];
  const sorted = all.subarray(0, n).sort();
  let w = 0;
  for (let i = 0; i < sorted.length; i++) {
    if (w === 0 || sorted[i] - sorted[w - 1] > TIME_EPSILON) sorted[w++] = sorted[i];
  }
  return sorted.slice(0, w);
}

/** Where a step from `t` lands, kept within 0..duration. `nextSample(t, direction)` answers the
 *  next recorded sample (null: none that way, or no samples at all); without samples a frame step
 *  is one display frame (`frameSeconds`). */
export function stepTarget(
  t: number,
  direction: 1 | -1,
  step: ResolvedStep,
  duration: number,
  nextSample: ((t: number, direction: 1 | -1) => number | null) | null,
  hasSamples: boolean,
  frameSeconds = 1 / 60,
): number {
  const clamp = (v: number) => Math.max(0, Math.min(duration, v));
  if (step.kind === "seconds") return clamp(t + direction * step.seconds);
  if (!hasSamples || !nextSample) return clamp(t + direction * step.count * frameSeconds);
  let at = t;
  for (let i = 0; i < step.count; i++) {
    const next = nextSample(at, direction);
    if (next === null) return clamp(direction > 0 ? Math.max(at, duration) : Math.min(at, 0));
    at = next;
  }
  return clamp(at);
}

// ── Fine scrubbing ───────────────────────────────────────────────────────────

/** How far (px) the pointer is pulled up or down off the bar before a drag slows to 1/10, and to 1/100. */
export const FINE_PULL_PX = [40, 120] as const;

/** The speed of a scrub drag: 1 on the bar; 1/10 with Shift, or with the pointer pulled away from
 *  the bar; 1/100 with both, or pulled further. `fine` is a touch long-press: at least 1/10. */
export function scrubScale(opts: { shift?: boolean; pullPx?: number; fine?: boolean }): number {
  const pull = Math.abs(opts.pullPx ?? 0);
  let level = pull >= FINE_PULL_PX[1] ? 2 : pull >= FINE_PULL_PX[0] ? 1 : 0;
  if (opts.fine) level = Math.max(level, 1);
  if (opts.shift) level += 1;
  return level >= 2 ? 0.01 : level === 1 ? 0.1 : 1;
}

/** The time after a drag moved `dx` pixels: `pxPerSecond` the bar's resolution, `scale` its speed
 *  (`scrubScale`). Incremental, so a change of speed mid-drag does not make the time jump. */
export function scrubTime(time: number, dx: number, pxPerSecond: number, scale: number, lo: number, hi: number): number {
  if (!(pxPerSecond > 0)) return time;
  return Math.max(lo, Math.min(hi, time + (dx / pxPerSecond) * scale));
}

// ── Zoom ─────────────────────────────────────────────────────────────────────

/** The part of the timeline the scrub bar shows, in seconds. */
export interface TimeWindow {
  start: number;
  end: number;
}

/** The narrowest window the bar zooms into: 10 ms. */
export const MIN_WINDOW_SECONDS = 0.01;

/** `view` kept inside 0..duration and no narrower than the minimum (or the whole, when that is shorter). */
export function clampWindow(view: TimeWindow, duration: number): TimeWindow {
  const total = Math.max(0, duration);
  const span = Math.min(total, Math.max(MIN_WINDOW_SECONDS, view.end - view.start));
  let start = Math.max(0, Math.min(view.start, total - span));
  if (!Number.isFinite(start)) start = 0;
  return { start, end: start + span };
}

export const fullWindow = (duration: number): TimeWindow => ({ start: 0, end: Math.max(0, duration) });

export const isZoomed = (view: TimeWindow, duration: number): boolean =>
  view.start > TIME_EPSILON || view.end < duration - TIME_EPSILON;

/** The time at `x` px of a bar `width` px wide that shows `view`. */
export function xToTime(x: number, width: number, view: TimeWindow): number {
  if (!(width > 0)) return view.start;
  const f = Math.max(0, Math.min(1, x / width));
  return view.start + f * (view.end - view.start);
}

/** Where on a bar that shows `view` the time `t` is, as a fraction 0..1 of its width (outside the
 *  window: below 0 or above 1). */
export function timeToFraction(t: number, view: TimeWindow): number {
  const span = view.end - view.start;
  return span > 0 ? (t - view.start) / span : 0;
}

/** Zoom by `factor` (above 1: in) about the time `at`, which stays under the pointer. */
export function zoomAt(view: TimeWindow, at: number, factor: number, duration: number): TimeWindow {
  if (!(factor > 0)) return view;
  const span = (view.end - view.start) / factor;
  const f = view.end > view.start ? (at - view.start) / (view.end - view.start) : 0.5;
  return clampWindow({ start: at - f * span, end: at - f * span + span }, duration);
}

/** A wheel's turn as a zoom factor: up (negative deltaY) zooms in, 10% a notch. */
export const wheelZoomFactor = (deltaY: number): number => Math.pow(1.1, -deltaY / 100);

/** The window a drag-selection from `a` to `b` zooms into; null for a selection too small to mean one. */
export function windowFromSelection(a: number, b: number, duration: number): TimeWindow | null {
  const start = Math.min(a, b);
  const end = Math.max(a, b);
  if (end - start < MIN_WINDOW_SECONDS / 2) return null;
  return clampWindow({ start, end }, duration);
}

/** The window moved so that `t` is inside it (playing past its edge pages it on). */
export function followTime(view: TimeWindow, t: number, duration: number): TimeWindow {
  if (t >= view.start && t <= view.end) return view;
  const span = view.end - view.start;
  return clampWindow(t > view.end ? { start: t, end: t + span } : { start: t - span, end: t }, duration);
}

// ── The time, read and typed ─────────────────────────────────────────────────

/** Seconds as "mm:ss.mmm" (minutes past 99 grow: "123:04.567"). */
export function preciseClockText(seconds: number): string {
  const ms = Math.round(Math.max(0, Number.isFinite(seconds) ? seconds : 0) * 1000);
  const minutes = Math.floor(ms / 60000);
  const rest = ms - minutes * 60000;
  const s = Math.floor(rest / 1000);
  const milli = rest - s * 1000;
  return `${String(minutes).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(milli).padStart(3, "0")}`;
}

/** What a typed time means, in seconds; null for text that is no time. Absolute: "1:23.456",
 *  "1:02:03.5" (h:mm:ss), "83.456", "83.456s", "1500ms". Relative to `now` with a leading "+" or
 *  "-": "+0.1", "-250ms". A comma is read as a decimal point. */
export function parseTimeInput(text: string, now = 0): number | null {
  let s = text.trim().toLowerCase().replace(",", ".");
  if (!s) return null;
  let sign = 0;
  if (s[0] === "+" || s[0] === "-") {
    sign = s[0] === "+" ? 1 : -1;
    s = s.slice(1).trim();
  }
  let value: number | null = null;
  const unit = /^(\d+(?:\.\d*)?|\.\d+)\s*(ms|s)?$/.exec(s);
  if (unit) {
    value = Number(unit[1]) / (unit[2] === "ms" ? 1000 : 1);
  } else if (/^\d+(:\d{1,2}){1,2}(\.\d*)?$/.test(s)) {
    const parts = s.split(":");
    const last = Number(parts.pop());
    const whole = parts.map(Number);
    // m:ss or h:mm:ss: every field after the first is below 60.
    if (last >= 60 || whole.slice(1).some((p) => p >= 60)) return null;
    value = whole.reduce((acc, p) => (acc + p) * 60, 0) + last;
  }
  if (value === null || !Number.isFinite(value)) return null;
  return sign === 0 ? value : now + sign * value;
}

// ── Capture ──────────────────────────────────────────────────────────────────

/** The file a frame captured at `t` is saved as: "<prefix>_t00m01.234s.png", the time to the
 *  millisecond, with no character a file system refuses. */
export function captureFilename(t: number, prefix = "frame"): string {
  const [mm, rest] = preciseClockText(t).split(":");
  const safe = prefix.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "frame";
  return `${safe}_t${mm}m${rest}s.png`;
}
