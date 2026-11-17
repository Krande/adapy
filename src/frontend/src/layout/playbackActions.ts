// Buttons a shell or a plugin adds to the playback transport (components/playback/
// PlaybackTransport.tsx): the bar with play, the scrub bar and the run's Console. What plays there
// often has more to show than the model -- a camera's picture, a level of detail -- and the place to
// show and hide those is beside the clock that drives them, not a second floating control.
//
// An action is a toggle or a plain button: a short label, whether it is offered now (`when`),
// whether it is on (`pressed`), and what a press does. The transport draws them after its own
// buttons, in order. Dependency-free: the transport is in the three.js-free viewport's chunk.

export interface PlaybackActionSpec {
    id: string;
    /** The button's text: a word. */
    label: string;
    /** What it does, for the tooltip and for a screen reader. Default: the label. */
    title?: string;
    /** One of the transport's own glyphs, drawn in place of the label (which is still its name
     *  to a screen reader): `"stop"`, the square the simulation controls stop with. */
    icon?: "stop";
    /** `"play"`: drawn beside the play button, before the scrub bar -- for what ends or restarts
     *  what plays. Default: after the transport's own buttons. */
    beside?: "play";
    /** Order among the actions, lower first. Default 0. */
    order?: number;
    /** Whether it is offered now. Default: always, while the transport is shown. */
    when?(): boolean;
    /** For a toggle: whether it is on. Left out, it is a plain button. */
    pressed?(): boolean;
    run(): void;
}

const actions = new Map<string, PlaybackActionSpec & {owner: string}>();
let version = 0;
const listeners = new Set<() => void>();

function changed(): void {
    version += 1;
    for (const cb of listeners) cb();
}

/** Add (or replace) a button of the playback transport. */
export function registerPlaybackAction(spec: PlaybackActionSpec, owner = "core"): void {
    actions.set(spec.id, {...spec, owner});
    changed();
}

export function unregisterPlaybackAction(id: string): void {
    if (actions.delete(id)) changed();
}

/** The actions, in order. */
export function listPlaybackActions(): PlaybackActionSpec[] {
    return [...actions.values()].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.id.localeCompare(b.id));
}

/** What an action's `when` or `pressed` reads has changed: the transport asks them again. */
export function notifyPlaybackActionsChanged(): void {
    changed();
}

export function subscribePlaybackActions(cb: () => void): () => void {
    listeners.add(cb);
    return () => listeners.delete(cb);
}

export const playbackActionsVersion = (): number => version;

/** Forget every action (tests). */
export function resetPlaybackActions(): void {
    actions.clear();
    changed();
}

/** An action as the transport draws it now; null when it is not offered. A `when` or `pressed`
 *  that throws takes that action off the bar, not the bar with it. */
export function playbackActionState(spec: PlaybackActionSpec): {pressed: boolean | undefined} | null {
    try {
        if (spec.when && !spec.when()) return null;
        return {pressed: spec.pressed ? !!spec.pressed() : undefined};
    } catch (err) {
        // eslint-disable-next-line no-console
        console.error(`[playback] action "${spec.id}" threw; it is left off the transport`, err);
        return null;
    }
}
