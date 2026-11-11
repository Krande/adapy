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
// No three.js in this module: both viewports and the plugin API use it.

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
}

export interface PlaybackTrackHandle {
    /** The recording grew (or shrank): the clock's duration follows the longest track. */
    setDuration(seconds: number): void;
    remove(): void;
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

    /** Register a track. It is shown at once, at the clock's time. */
    addTrack(track: PlaybackTrack): PlaybackTrackHandle {
        const held: Held = {id: track.id, duration: seconds(track.duration), show: track.show, label: track.label, shown: -1};
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
            remove: () => {
                const index = this.tracks.indexOf(held);
                if (index < 0) return;
                this.tracks.splice(index, 1);
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

/** The viewer's clock. */
export const playbackClock = new PlaybackClock();
