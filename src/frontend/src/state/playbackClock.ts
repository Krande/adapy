// One playback clock for everything that shows a moment in time: a model's animation, a line that
// is a planned path at that moment, an image that is a map at that moment. Something timed registers
// a track ({id, duration, show(t)}) and the clock calls `show` whenever its time changes; play,
// pause, seek and speed are the clock's, so every track shows the same moment.
//
// The clock has no timer of its own. While it plays, each viewport's frame loop calls `tick` once
// per frame it draws (the three.js render loop, the three.js-free scene's `beforeFrame`), and `tick`
// answers whether another frame is wanted. Paused, or with no track, a frame pays one comparison.
//
// A track's duration may grow while its recording is still being written, and "follow the end" is a
// mode of the clock: the time stays at the end as the end moves.
//
// The FEA result playback is not on this clock: its mode shapes are an oscillator with a period and
// its time histories are steps fetched one at a time (utils/scene/fea/feaAnimationDriver.ts).
//
// Beside the moving time the clock keeps what a transport needs to stop at a precise moment: the
// tracks' recorded sample times (a frame step goes from one to the next), the display's frame (a
// step where nothing has samples) and labelled markers that sources put on the timeline.
//
// No three.js in this module: both viewports and the plugin API use it.

import {mergeSamples, resolveStep, stepSamples, stepTarget, TIME_EPSILON, type PlaybackStepSize} from "./playbackTimeline";

export type {PlaybackStepSize};

export interface PlaybackTrack {
    /** Unique among the tracks; adding a track with an id in use replaces that one. */
    id: string;
    /** How long it is, in seconds. */
    duration: number;
    /** Show the moment `t`, in seconds from the track's start, never beyond its duration: while the
     *  clock is past a shorter track's end, that track keeps showing its last moment. Called once
     *  per frame while the clock plays, with a time that changed: it must not allocate. */
    show(t: number): void;
    /** What the track is, for a person. */
    label?: string;
    /** The times (seconds, ascending) the track has recorded samples at: a frame step of the
     *  transport goes from one to the next. Left out, with no `stepSample` either, the track has
     *  no discrete samples and a frame step is one display frame. */
    samples?: ArrayLike<number>;
    /** For a track that cannot list its samples: the sample after (`direction` 1) or before (-1)
     *  `t`, or null for none that way. Used beside `samples` when both are given. */
    stepSample?(t: number, direction: 1 | -1): number | null;
}

export interface PlaybackTrackHandle {
    /** The recording grew (or shrank): the clock's duration follows the longest track. */
    setDuration(seconds: number): void;
    /** The track's sample times changed (a recording that grew); null: it has none. */
    setSamples(times: ArrayLike<number> | null): void;
    remove(): void;
}

/** What a marker on the timeline stands for; its tick is drawn in the kind's colour. */
export type PlaybackMarkerKind = "stage" | "event" | "warning" | "error";

export const PLAYBACK_MARKER_KINDS: readonly PlaybackMarkerKind[] = ["stage", "event", "warning", "error"];

/** A labelled moment on the scrub bar. */
export interface PlaybackMarker {
    /** Seconds on the clock. */
    t: number;
    label: string;
    /** Default "event". */
    kind?: PlaybackMarkerKind;
    /** A CSS colour for its tick, over the kind's. */
    color?: string;
}

/** A marker as the clock lists it: with the source that set it and a kind. */
export interface PlaybackMarkerEntry extends PlaybackMarker {
    source: string;
    kind: PlaybackMarkerKind;
}

/** The clock as a value: a new object whenever any of it changed. */
export interface PlaybackState {
    /** The longest track's duration, in seconds; 0 without a track. */
    readonly duration: number;
    readonly time: number;
    readonly playing: boolean;
    /** Seconds of the recording per second of the wall clock. */
    readonly speed: number;
    /** At the end, start again instead of stopping. */
    readonly loop: boolean;
    /** The time stays at the end while the duration grows. */
    readonly followEnd: boolean;
    /** How many tracks are registered. */
    readonly tracks: number;
}

/** A track as the clock keeps it, with the moment it was last shown (negative: never). */
interface Held extends PlaybackTrack {
    shown: number;
}

/** Markers further apart than this are different moments for "the next marker". */
const MARKER_EPSILON = 1e-6;
/** The display frame a frame step takes where no track has samples, until frames have been measured. */
const DEFAULT_FRAME_SECONDS = 1 / 60;

/** A frame that comes after a stall (a hidden tab, a long task) advances the time by this at most. */
const MAX_STEP_SECONDS = 0.25;
/** While playing, listeners hear of the moving time this often, not once per frame. */
const NOTIFY_MS = 100;
const MIN_SPEED = 0.01;
const MAX_SPEED = 100;

export class PlaybackClock {
    private readonly tracks: Held[] = [];
    private now = 0;
    private total = 0;
    private isPlaying = false;
    private rate = 1;
    private looping = false;
    private following = false;
    /** When the last frame was, while playing; negative before the first. */
    private lastTick = -1;
    private lastNotify = 0;
    private snapshot: PlaybackState | null = null;
    private readonly listeners = new Set<() => void>();
    private readonly wakers = new Set<() => void>();
    private readonly failed = new Set<string>();
    /** Every track's samples as one ascending list; null: to be merged again. */
    private merged: Float64Array | null = null;
    /** A display frame, measured from the frames the viewport drew while playing (seconds). */
    private frame = DEFAULT_FRAME_SECONDS;
    private readonly markerSets = new Map<string, PlaybackMarkerEntry[]>();
    private markerList: PlaybackMarkerEntry[] | null = null;

    /** Register a track. It is shown at once, at the clock's time. */
    addTrack(track: PlaybackTrack): PlaybackTrackHandle {
        const held: Held = {
            id: track.id,
            duration: seconds(track.duration),
            show: track.show,
            label: track.label,
            samples: sortedSamples(track.samples),
            stepSample: track.stepSample,
            shown: -1,
        };
        this.merged = null;
        const at = this.tracks.findIndex((t) => t.id === held.id);
        if (at >= 0) this.tracks[at] = held;
        else this.tracks.push(held);
        this.failed.delete(held.id);
        this.measure();
        this.showOne(held);
        this.changed();
        return {
            setDuration: (duration) => {
                const next = seconds(duration);
                if (next === held.duration || !this.tracks.includes(held)) return;
                held.duration = next;
                this.measure();
                this.changed();
            },
            setSamples: (times) => {
                if (!this.tracks.includes(held)) return;
                held.samples = sortedSamples(times ?? undefined);
                this.merged = null;
                this.changed();
            },
            remove: () => {
                const index = this.tracks.indexOf(held);
                if (index < 0) return;
                this.tracks.splice(index, 1);
                this.merged = null;
                if (this.tracks.length === 0) {
                    // Nothing left to play: the next recording starts from its beginning.
                    this.isPlaying = false;
                    this.following = false;
                    this.now = 0;
                }
                this.measure();
                this.changed();
            },
        };
    }

    /** The clock's duration from its tracks, and the time kept inside it (at its end while following). */
    private measure(): void {
        let total = 0;
        for (const track of this.tracks) if (track.duration > total) total = track.duration;
        this.total = total;
        const time = this.following || this.now > total ? total : this.now;
        if (time !== this.now) {
            this.now = time;
            this.showAll();
        }
    }

    private showOne(track: Held): void {
        // A track shorter than the clock holds its last moment: shown once there, not every frame.
        const t = this.now < track.duration ? this.now : track.duration;
        if (t === track.shown) return;
        track.shown = t;
        try {
            track.show(t);
        } catch (err) {
            // One track that cannot show a moment must not stop the others; said once per track.
            if (!this.failed.has(track.id)) {
                this.failed.add(track.id);
                console.warn(`[playback] track ${track.id} failed to show a moment:`, err);
            }
        }
    }

    private showAll(): void {
        const tracks = this.tracks;
        for (let i = 0; i < tracks.length; i++) this.showOne(tracks[i]);
    }

    /** For a viewport's frame loop: advance to `nowMs` (as `performance.now()`) and show the moment.
     *  True while the clock wants the next frame too. */
    tick(nowMs: number): boolean {
        if (!this.isPlaying) return false;
        // Held at the end: only a longer recording moves the time, and that shows itself.
        if (this.following) {
            this.lastTick = -1;
            return false;
        }
        const dt = this.lastTick >= 0 ? Math.min((nowMs - this.lastTick) / 1000, MAX_STEP_SECONDS) : 0;
        this.lastTick = nowMs;
        if (dt <= 0) return true;
        // The display's frame, smoothed (a stall is no frame): what a frame step is where nothing
        // has samples.
        if (dt < 0.1) this.frame += (dt - this.frame) * 0.1;
        let time = this.now + dt * this.rate;
        let ended = false;
        if (time >= this.total) {
            if (this.looping && this.total > 0) {
                time %= this.total;
            } else {
                time = this.total;
                ended = true;
            }
        }
        if (time !== this.now) {
            this.now = time;
            this.showAll();
        }
        if (ended) {
            this.isPlaying = false;
            this.changed();
            return false;
        }
        if (this.listeners.size && nowMs - this.lastNotify >= NOTIFY_MS) {
            this.lastNotify = nowMs;
            this.changed();
        }
        return true;
    }

    play(): void {
        if (this.isPlaying || this.tracks.length === 0) return;
        // Play from the end starts over, as a transport does.
        if (!this.following && this.now >= this.total) this.setTime(0);
        this.isPlaying = true;
        this.lastTick = -1;
        this.changed();
        for (const wake of this.wakers) wake();
    }

    /** Stop where it is. Following the end stops too: a paused clock does not move. */
    pause(): void {
        if (!this.isPlaying && !this.following) return;
        this.isPlaying = false;
        this.following = false;
        this.changed();
    }

    toggle(): void {
        if (this.isPlaying) this.pause();
        else this.play();
    }

    /** Go to `time` (seconds, kept within the duration) and show it. Lets go of the end. */
    seek(time: number): void {
        this.following = false;
        this.lastTick = -1;
        this.setTime(Number.isFinite(time) ? Math.max(0, Math.min(this.total, time)) : 0);
        this.changed();
    }

    private setTime(time: number): void {
        if (time === this.now) return;
        this.now = time;
        this.showAll();
    }

    setSpeed(speed: number): void {
        if (!Number.isFinite(speed)) return;
        const next = Math.max(MIN_SPEED, Math.min(MAX_SPEED, speed));
        if (next === this.rate) return;
        this.rate = next;
        this.changed();
    }

    setLoop(loop: boolean): void {
        if (loop === this.looping) return;
        this.looping = loop;
        this.changed();
    }

    /** Keep the time at the end as the duration grows (a recording still being written); turning
     *  it on goes to the end now. */
    setFollowEnd(follow: boolean): void {
        if (follow === this.following) return;
        this.following = follow;
        if (follow) this.setTime(this.total);
        this.lastTick = -1;
        this.changed();
        // Let go while playing: the frames start again from here.
        if (!follow && this.isPlaying) for (const wake of this.wakers) wake();
    }

    /** The time now, in seconds: what a frame reads without making a state object. */
    time(): number {
        return this.now;
    }

    /** True while frames are wanted: playing, and not held at the end. */
    get running(): boolean {
        return this.isPlaying && !this.following;
    }

    /** The clock as a value; the same object until something changed. */
    state(): PlaybackState {
        return (this.snapshot ??= {
            duration: this.total,
            time: this.now,
            playing: this.isPlaying,
            speed: this.rate,
            loop: this.looping,
            followEnd: this.following,
            tracks: this.tracks.length,
        });
    }

    // ── Stepping ────────────────────────────────────────────────────────────

    /** Whether any track has discrete samples (`samples` or `stepSample`). */
    hasSamples(): boolean {
        return this.tracks.some((t) => (t.samples?.length ?? 0) > 0 || !!t.stepSample);
    }

    /** Every track's sample times as one ascending list, without repeats. */
    sampleTimes(): Float64Array {
        return (this.merged ??= mergeSamples(this.tracks.flatMap((t) => (t.samples?.length ? [t.samples] : []))));
    }

    /** The nearest sample of any track after (`direction` 1) or before (-1) `t`; null for none. */
    nextSample(t: number, direction: 1 | -1): number | null {
        let best = stepSamples(this.sampleTimes(), t, direction);
        for (const track of this.tracks) {
            if (!track.stepSample) continue;
            let next: number | null = null;
            try {
                next = track.stepSample(t, direction);
            } catch {
                next = null;
            }
            if (next === null || !Number.isFinite(next)) continue;
            if (direction > 0 ? next <= t + TIME_EPSILON : next >= t - TIME_EPSILON) continue;
            if (best === null || (direction > 0 ? next < best : next > best)) best = next;
        }
        return best;
    }

    /** A display frame in seconds, as measured while playing (1/60 until then). */
    frameSeconds(): number {
        return this.frame;
    }

    /** Where a step from the time now lands (not going there). */
    stepTarget(direction: 1 | -1, size: PlaybackStepSize = "frame", mods: {shift?: boolean; alt?: boolean} = {}): number {
        const step = resolveStep(size, mods, this.frame);
        return stepTarget(this.now, direction, step, this.total, (t, d) => this.nextSample(t, d), this.hasSamples(), this.frame);
    }

    /** Pause and step: one sample (or display frame) with "frame", else `size` seconds; Shift ×10,
     *  Alt ÷10. Returns the time stepped to. */
    step(direction: 1 | -1, size: PlaybackStepSize = "frame", mods: {shift?: boolean; alt?: boolean} = {}): number {
        const target = this.stepTarget(direction, size, mods);
        this.pause();
        this.seek(target);
        return this.now;
    }

    // ── Markers ─────────────────────────────────────────────────────────────

    /** Put `markers` on the timeline for `source`, in place of what that source had there. */
    setMarkers(source: string, markers: readonly PlaybackMarker[]): void {
        const list: PlaybackMarkerEntry[] = [];
        for (const m of markers) {
            if (!m || !Number.isFinite(m.t)) continue;
            const kind = PLAYBACK_MARKER_KINDS.includes(m.kind as PlaybackMarkerKind) ? (m.kind as PlaybackMarkerKind) : "event";
            list.push({t: Math.max(0, m.t), label: String(m.label ?? ""), kind, ...(m.color ? {color: m.color} : {}), source});
        }
        if (list.length) this.markerSets.set(source, list);
        else if (!this.markerSets.delete(source)) return;
        this.markerList = null;
        this.changed();
    }

    /** Take `source`'s markers off the timeline. */
    clearMarkers(source: string): void {
        if (!this.markerSets.delete(source)) return;
        this.markerList = null;
        this.changed();
    }

    /** Every source's markers, by time (the same array until they change). */
    markers(): readonly PlaybackMarkerEntry[] {
        return (this.markerList ??= [...this.markerSets.values()].flat().sort((a, b) => a.t - b.t || a.source.localeCompare(b.source)));
    }

    /** The first marker after (`direction` 1) or the last before (-1) `t`; null for none. */
    nextMarker(t: number, direction: 1 | -1): PlaybackMarkerEntry | null {
        const list = this.markers();
        if (direction > 0) return list.find((m) => m.t > t + MARKER_EPSILON) ?? null;
        for (let i = list.length - 1; i >= 0; i--) if (list[i].t < t - MARKER_EPSILON) return list[i];
        return null;
    }

    /** Pause and go to the next (or previous) marker. The marker, or null when there is none that way. */
    seekMarker(direction: 1 | -1): PlaybackMarkerEntry | null {
        const marker = this.nextMarker(this.now, direction);
        if (!marker) return null;
        this.pause();
        this.seek(marker.t);
        return marker;
    }

    /** The registered tracks' ids and labels, in the order they were added. */
    trackList(): {id: string; label: string; duration: number}[] {
        return this.tracks.map((t) => ({id: t.id, label: t.label ?? t.id, duration: t.duration}));
    }

    private changed(): void {
        this.snapshot = null;
        for (const listener of this.listeners) listener();
    }

    /** Called when the state changed; while playing, the moving time is told about ten times a
     *  second. Returns the unsubscribe function. */
    subscribe = (listener: () => void): (() => void) => {
        this.listeners.add(listener);
        return () => void this.listeners.delete(listener);
    };

    /** Called when the clock starts wanting frames: a viewport that draws only on change wakes its
     *  frame loop. Returns the unsubscribe function. */
    onWake(listener: () => void): () => void {
        this.wakers.add(listener);
        return () => void this.wakers.delete(listener);
    }
}

function seconds(value: number): number {
    return Number.isFinite(value) && value > 0 ? value : 0;
}

/** A track's samples as an ascending list (a source may hand them in any order); undefined for none. */
function sortedSamples(times: ArrayLike<number> | undefined): Float64Array | undefined {
    if (!times || !times.length) return undefined;
    return mergeSamples([times]);
}

/** The viewer's clock. */
export const playbackClock = new PlaybackClock();
