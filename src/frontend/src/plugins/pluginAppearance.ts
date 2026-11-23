// `ctx.appearance` (1.38.0): a plugin asks for an appearance preset instead of keeping settings of its own.
//
// The Appearance settings (state/appearanceStore.ts) stay the one source of truth, per user and persisted
// as always: a preset only writes them. "realistic" turns on the native viewport's HDR look (neutral tone
// mapping, a moderate bloom, the studio environment, the viewer's own lights turned down) and remembers
// what it replaced; "default" puts that back -- the user's own settings, not the factory defaults. So a
// plugin can offer a "Realistic lighting" switch in its own panel: `current().preset` is its state,
// `subscribe` keeps it in step with the Options panel, and the switch calls `applyPreset`.

import {
    APPEARANCE_PRESETS,
    type AppearancePresetId,
    type ToneMappingSetting,
    isRealisticLook,
    useAppearanceStore,
} from "@/state/appearanceStore";

/** What a plugin reads of the appearance: the preset in effect and the look settings it writes. */
export interface PluginAppearanceSnapshot {
    /** "realistic" while the look settings are the realistic preset's (applied by any caller, or set by
     *  hand to the same values); "default" otherwise. */
    preset: AppearancePresetId;
    toneMapping: ToneMappingSetting;
    exposure: number;
    bloom: boolean;
    environment: boolean;
    environmentIntensity: number;
    brightness: number;
}

/** `ctx.appearance`. */
export interface PluginAppearance {
    /** The presets `applyPreset` takes. */
    readonly presets: readonly AppearancePresetId[];
    /** Write preset `id` into the Appearance settings ("default": put back what "realistic" replaced). An
     *  unknown id changes nothing. */
    applyPreset: (id: AppearancePresetId) => void;
    current: () => PluginAppearanceSnapshot;
    /** Called with the snapshot whenever a look setting changes, by anyone; returns the unsubscribe. */
    subscribe: (cb: (now: PluginAppearanceSnapshot) => void) => () => void;
}

function snapshot(): PluginAppearanceSnapshot {
    const s = useAppearanceStore.getState();
    return {
        preset: isRealisticLook(s) ? "realistic" : "default",
        toneMapping: s.toneMapping,
        exposure: s.exposure,
        bloom: s.bloom,
        environment: s.environment,
        environmentIntensity: s.environmentIntensity,
        brightness: s.brightness,
    };
}

const same = (a: PluginAppearanceSnapshot, b: PluginAppearanceSnapshot) =>
    (Object.keys(a) as (keyof PluginAppearanceSnapshot)[]).every((k) => a[k] === b[k]);

export function makePluginAppearance(log?: (msg: string) => void): PluginAppearance {
    return {
        presets: APPEARANCE_PRESETS,
        applyPreset(id) {
            if (!APPEARANCE_PRESETS.includes(id)) {
                log?.(`appearance: no preset "${String(id)}"`);
                return;
            }
            useAppearanceStore.getState().applyPreset(id);
        },
        current: snapshot,
        subscribe(cb) {
            let last = snapshot();
            return useAppearanceStore.subscribe(() => {
                const now = snapshot();
                if (same(now, last)) return; // a setting the snapshot does not show
                last = now;
                cb(now);
            });
        },
    };
}
