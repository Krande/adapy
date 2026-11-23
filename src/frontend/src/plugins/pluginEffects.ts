// `ctx.effects` (1.40.0): visual effects a plugin shows at runtime -- point lights, particle emitters and
// emissive ramps, as plain data in the shape of a GLB's "ada_effects" -- without writing them into a model's
// file and loading it again.
//
// What a plugin applies is kept here, per plugin and source id, and handed to whichever viewport is on
// screen (`ViewportApi.applyEffects`): again when another one comes on screen. The viewport holds it across
// loads of the model it is applied with. The effects are drawn on the playback clock, so they play, pause
// and scrub with everything else on it.
//
// Only the native (WebGPU) viewport draws effects for now; `drawn()` says whether the one on screen does,
// and `subscribe` tells when that may have changed, so a plugin can say where its effects are seen.

import {playbackClock} from "@/state/playbackClock";
import {activeViewport, followActiveViewport, onActiveViewportChange, type ViewportApi} from "@/viewport/api/viewportApi";
import type {EffectsClock, EffectsDescription, ViewportEffects} from "@/viewport/api/viewportEffects";

export type {EffectsDescription} from "@/viewport/api/viewportEffects";

/** `ctx.effects`. */
export interface PluginEffects {
    /**
     * Show `spec` under `sourceId`, in place of what was shown under it. Times are seconds on the playback
     * clock; positions are in the scene's coordinates (those `ctx.scene.addLineLayer` takes); `post` and
     * `environment` are not taken (the look is the user's Appearance setting). With `model` (a model's
     * source name) the ramps' targets are that model's object ids, and the effects are drawn while that
     * model is on screen.
     */
    apply: (sourceId: string, spec: EffectsDescription, options?: {model?: string}) => void;
    /** Take what is shown under `sourceId` away. */
    clear: (sourceId: string) => void;
    /** Whether the viewport on screen draws effects (the native one does; the three.js one does not yet). */
    drawn: () => boolean;
    /** Called with `drawn()` when another viewport came on screen; returns the unsubscribe. */
    subscribe: (cb: (drawn: boolean) => void) => () => void;
}

interface Entry {
    spec: EffectsDescription;
    model: string | undefined;
    held: ViewportEffects | null;
}

/** Everything applied, by plugin id and source id. */
const entries = new Map<string, Entry>();
let unfollow: (() => void) | null = null;
let viewport: ViewportApi | null = null;

const clock: EffectsClock = {
    time: () => playbackClock.time(),
    subscribe: (listener) => playbackClock.subscribe(listener),
};

function hold(entry: Entry): void {
    entry.held?.remove();
    entry.held = viewport?.applyEffects?.(entry.spec, {model: entry.model, clock}) ?? null;
}

/** Hand everything applied to each viewport that comes on screen, and take it off the one that leaves. */
function follow(): void {
    unfollow ??= followActiveViewport((next) => {
        viewport = next;
        for (const entry of entries.values()) hold(entry);
        return () => {
            for (const entry of entries.values()) {
                entry.held?.remove();
                entry.held = null;
            }
            viewport = null;
        };
    });
}

function drawn(): boolean {
    const v = activeViewport();
    return typeof v?.applyEffects === "function" && (v.drawsEffects?.() ?? false);
}

export function makePluginEffects(pluginId: string, log?: (msg: string) => void): PluginEffects {
    const key = (sourceId: string) => `${pluginId}\u0000${sourceId}`;
    return {
        apply(sourceId, spec, options) {
            if (!spec || typeof spec !== "object") {
                log?.(`effects: "${sourceId}" is not an effects description`);
                return;
            }
            follow();
            const k = key(sourceId);
            const was = entries.get(k);
            if (was && was.held && was.model === options?.model) {
                was.spec = spec;
                was.held.update(spec);
                return;
            }
            was?.held?.remove();
            const entry: Entry = {spec, model: options?.model, held: null};
            entries.set(k, entry);
            hold(entry);
        },
        clear(sourceId) {
            const k = key(sourceId);
            entries.get(k)?.held?.remove();
            entries.delete(k);
            if (entries.size === 0 && unfollow) {
                unfollow();
                unfollow = null;
            }
        },
        drawn,
        subscribe(cb) {
            let last = drawn();
            return onActiveViewportChange(() => {
                const now = drawn();
                if (now === last) return;
                last = now;
                cb(now);
            });
        },
    };
}

/** For tests: forget everything applied. */
export function resetPluginEffects(): void {
    for (const entry of entries.values()) entry.held?.remove();
    entries.clear();
    unfollow?.();
    unfollow = null;
    viewport = null;
}
