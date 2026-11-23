import {create} from "zustand";
import {createJSONStorage, persist} from "zustand/middleware";

import {linearChannel} from "@/utils/scene/sceneColor";

// How the model looks, whichever renderer draws it: three.js, the adacpp WebGPU renderer that mirrors
// the three.js scene, or the three.js-free viewport. One store, read by all three, so a setting
// means the same picture on each. Persisted like the performance options.
//
// The lights at brightness 1 are the look the viewer had before the settings existed: an ambient
// light of irradiance PI / 2 and a headlight of intensity 1.4. The default brightness is half that,
// with FEA results shaded (version 2 of the stored settings); a #393939 background, and light grey
// (0.8 linear, #e7e7e7) for geometry without a colour of its own.
//
// No three.js in this module or in anything it imports: the three.js-free viewport reads it.

/** The lights before the settings existed: three.js' AmbientLight(PI / 2) and DirectionalLight(1.4). */
export const BASE_AMBIENT = Math.PI / 2;
export const BASE_HEADLIGHT = 1.4;
const BASE_TOTAL = BASE_AMBIENT + BASE_HEADLIGHT;

export interface AppearanceSettings {
    /** The colour of geometry that has none (sRGB, "#rrggbb"). */
    defaultColor: string;
    /** Multiplies both lights; 1 is the look before the settings. */
    brightness: number;
    /** The ambient light's share of the light, 0..1; the headlight has the rest. */
    ambientShare: number;
    /** The viewport's background (sRGB, "#rrggbb"). */
    background: string;
    /** Light a painted FEA result like the model: its legend colour darkened by how its face is
     *  turned, never lightened or greyed (utils/scene/fea/resultShading.ts). Off, a result colour is
     *  drawn exactly as the legend shows it, whichever way its face is turned. */
    shadeResults: boolean;
    /** The colour of an FEA result's element edges: the shell grid and the beam solids' edges (sRGB,
     *  "#rrggbb"). */
    edgeColor: string;
    /** How bright the element edges are drawn: their colour times this, 0..1 (sRGB). The beam
     *  elements' own, dimmer edge colour is scaled by it too. */
    edgeBrightness: number;
    /** The native (adacpp WebGPU) viewport only, for now: how its HDR image is mapped to the screen.
     *  "off" draws as it always has (no HDR target); "neutral" (Khronos PBR Neutral) keeps colours and
     *  rolls off highlights; "aces" is filmic (three.js' ACESFilmicToneMapping). */
    toneMapping: ToneMappingSetting;
    /** Multiplies the image before tone mapping (native viewport, with a tone mapping or bloom). */
    exposure: number;
    /** Glow around what is brighter than white: emissive parts, sparks, highlights (native viewport). */
    bloom: boolean;
    /** Image-based lighting from a studio environment: what makes metal look like metal (native viewport). */
    environment: boolean;
    /** How strongly the environment lights the model. */
    environmentIntensity: number;
    /** What the "realistic" preset replaced (`applyPreset`), for "default" to put back; null while no
     *  preset is on. No control of its own. */
    presetRestore: PresetLook | null;
}

export type ToneMappingSetting = "off" | "neutral" | "aces";
export const TONE_MAPPINGS: readonly ToneMappingSetting[] = ["off", "neutral", "aces"];

/** The settings an appearance preset writes. */
export const PRESET_SETTINGS = ["toneMapping", "exposure", "bloom", "environment", "environmentIntensity", "brightness"] as const;
export type PresetLook = Pick<AppearanceSettings, (typeof PRESET_SETTINGS)[number]>;
export type AppearancePresetId = "realistic" | "default";
export const APPEARANCE_PRESETS: readonly AppearancePresetId[] = ["realistic", "default"];

/** "realistic": the native viewport's HDR look. Neutral tone mapping keeps colours as they are and rolls
 *  off only what is brighter than white; a moderate bloom on what glows (`nativeLook`); the studio
 *  environment for reflections, with the viewer's own lights turned down since it lights the model too. */
export const REALISTIC_LOOK: Readonly<PresetLook> = {
    toneMapping: "neutral",
    exposure: 1,
    bloom: true,
    environment: true,
    environmentIntensity: 1,
    brightness: 0.35,
};

function pickLook(s: PresetLook): PresetLook {
    return Object.fromEntries(PRESET_SETTINGS.map((k) => [k, s[k]])) as unknown as PresetLook;
}

/** Whether the look settings are the realistic preset's. */
export function isRealisticLook(s: PresetLook = useAppearanceStore.getState()): boolean {
    return PRESET_SETTINGS.every((k) => s[k] === REALISTIC_LOOK[k]);
}

export const APPEARANCE_DEFAULTS: Readonly<AppearanceSettings> = {
    defaultColor: "#e7e7e7",
    brightness: 0.5,
    ambientShare: BASE_AMBIENT / BASE_TOTAL,
    background: "#393939",
    shadeResults: true,
    edgeColor: "#ffffff",
    edgeBrightness: 1,
    toneMapping: "off",
    exposure: 1,
    bloom: false,
    environment: false,
    environmentIntensity: 1,
    presetRestore: null,
};

/** The defaults of version 1 of the stored settings, which a version-1 value equal to is taken as
 *  never chosen (see `migrateAppearance`). */
const V1_DEFAULTS = {brightness: 1, shadeResults: false} as const;

export const BRIGHTNESS_RANGE = {min: 0.25, max: 2, step: 0.05} as const;
export const EDGE_BRIGHTNESS_RANGE = {min: 0, max: 1, step: 0.05} as const;
export const EXPOSURE_RANGE = {min: 0.25, max: 4, step: 0.05} as const;
export const ENVIRONMENT_INTENSITY_RANGE = {min: 0, max: 3, step: 0.05} as const;

/** The element-edge settings: what their own "Default" puts back. */
export const EDGE_SETTINGS = ["edgeColor", "edgeBrightness"] as const satisfies readonly (keyof AppearanceSettings)[];

export interface AppearanceState extends AppearanceSettings {
    setDefaultColor: (hex: string) => void;
    setBrightness: (value: number) => void;
    setAmbientShare: (value: number) => void;
    setBackground: (hex: string) => void;
    setShadeResults: (on: boolean) => void;
    setEdgeColor: (hex: string) => void;
    setEdgeBrightness: (value: number) => void;
    setToneMapping: (mode: ToneMappingSetting) => void;
    setExposure: (value: number) => void;
    setBloom: (on: boolean) => void;
    setEnvironment: (on: boolean) => void;
    setEnvironmentIntensity: (value: number) => void;
    /** "realistic" writes `REALISTIC_LOOK` over the settings, remembering what it replaced; "default" puts
     *  that back: the user's own settings, or the defaults when no preset replaced any. */
    applyPreset: (id: AppearancePresetId) => void;
    /** Back to the defaults. */
    reset: () => void;
    /** The element-edge settings back to their defaults, the rest as they are. */
    resetEdges: () => void;
}

const HEX = /^#[0-9a-f]{6}$/i;
const hexOr = (text: unknown, fallback: string): string => (typeof text === "string" && HEX.test(text) ? text.toLowerCase() : fallback);
const clamp = (v: unknown, lo: number, hi: number, fallback: number): number =>
    typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback;

/** Stored settings as valid ones: a value that is not one (an older version's, a hand edit) is the default. */
export function sanitizeAppearance(stored: unknown): AppearanceSettings {
    const s = (stored ?? {}) as Partial<Record<keyof AppearanceSettings, unknown>>;
    return {
        defaultColor: hexOr(s.defaultColor, APPEARANCE_DEFAULTS.defaultColor),
        brightness: clamp(s.brightness, BRIGHTNESS_RANGE.min, BRIGHTNESS_RANGE.max, APPEARANCE_DEFAULTS.brightness),
        ambientShare: clamp(s.ambientShare, 0, 1, APPEARANCE_DEFAULTS.ambientShare),
        background: hexOr(s.background, APPEARANCE_DEFAULTS.background),
        shadeResults: typeof s.shadeResults === "boolean" ? s.shadeResults : APPEARANCE_DEFAULTS.shadeResults,
        edgeColor: hexOr(s.edgeColor, APPEARANCE_DEFAULTS.edgeColor),
        edgeBrightness: clamp(s.edgeBrightness, EDGE_BRIGHTNESS_RANGE.min, EDGE_BRIGHTNESS_RANGE.max, APPEARANCE_DEFAULTS.edgeBrightness),
        toneMapping: TONE_MAPPINGS.includes(s.toneMapping as ToneMappingSetting) ? (s.toneMapping as ToneMappingSetting) : APPEARANCE_DEFAULTS.toneMapping,
        exposure: clamp(s.exposure, EXPOSURE_RANGE.min, EXPOSURE_RANGE.max, APPEARANCE_DEFAULTS.exposure),
        bloom: typeof s.bloom === "boolean" ? s.bloom : APPEARANCE_DEFAULTS.bloom,
        environment: typeof s.environment === "boolean" ? s.environment : APPEARANCE_DEFAULTS.environment,
        environmentIntensity: clamp(
            s.environmentIntensity,
            ENVIRONMENT_INTENSITY_RANGE.min,
            ENVIRONMENT_INTENSITY_RANGE.max,
            APPEARANCE_DEFAULTS.environmentIntensity,
        ),
        presetRestore: sanitizeRestore(s.presetRestore),
    };
}

function sanitizeRestore(stored: unknown): PresetLook | null {
    if (!stored || typeof stored !== "object") return null;
    // The look settings alone, each made valid as the settings themselves are.
    return pickLook(sanitizeAppearance({...(stored as object), presetRestore: null}));
}

/**
 * Stored settings of an older `version` as this version's.
 *
 * Version 2 changed two defaults: brightness 1 -> 0.5, and FEA results shaded. Version 1 stored every
 * setting whenever any one was changed, so a stored value cannot tell "left at the default" from
 * "chosen": a version-1 value that IS the version-1 default is taken as never chosen and given the new
 * default, and any other value is the user's and kept. (Choosing exactly brightness 1.00 or unshaded
 * results under version 1 was choosing what was already there.)
 */
export function migrateAppearance(stored: unknown, version: number): unknown {
    if (version >= 2 || !stored || typeof stored !== "object") return stored;
    const s = {...(stored as Record<string, unknown>)};
    if (s.brightness === V1_DEFAULTS.brightness) s.brightness = APPEARANCE_DEFAULTS.brightness;
    if (s.shadeResults === V1_DEFAULTS.shadeResults) s.shadeResults = APPEARANCE_DEFAULTS.shadeResults;
    return s;
}

const kept = new Map<string, string>();
const pageStorage = {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
};

export const useAppearanceStore = create<AppearanceState>()(
    persist(
        (set) => ({
            ...APPEARANCE_DEFAULTS,
            setDefaultColor: (hex) => set((s) => ({defaultColor: hexOr(hex, s.defaultColor)})),
            setBrightness: (value) => set((s) => ({brightness: clamp(value, BRIGHTNESS_RANGE.min, BRIGHTNESS_RANGE.max, s.brightness)})),
            setAmbientShare: (value) => set((s) => ({ambientShare: clamp(value, 0, 1, s.ambientShare)})),
            setBackground: (hex) => set((s) => ({background: hexOr(hex, s.background)})),
            setShadeResults: (on) => set({shadeResults: !!on}),
            setEdgeColor: (hex) => set((s) => ({edgeColor: hexOr(hex, s.edgeColor)})),
            setEdgeBrightness: (value) =>
                set((s) => ({edgeBrightness: clamp(value, EDGE_BRIGHTNESS_RANGE.min, EDGE_BRIGHTNESS_RANGE.max, s.edgeBrightness)})),
            setToneMapping: (mode) => set((s) => ({toneMapping: TONE_MAPPINGS.includes(mode) ? mode : s.toneMapping})),
            setExposure: (value) => set((s) => ({exposure: clamp(value, EXPOSURE_RANGE.min, EXPOSURE_RANGE.max, s.exposure)})),
            setBloom: (on) => set({bloom: !!on}),
            setEnvironment: (on) => set({environment: !!on}),
            setEnvironmentIntensity: (value) =>
                set((s) => ({
                    environmentIntensity: clamp(value, ENVIRONMENT_INTENSITY_RANGE.min, ENVIRONMENT_INTENSITY_RANGE.max, s.environmentIntensity),
                })),
            applyPreset: (id) =>
                set((s) => {
                    if (id === "realistic") {
                        // Remembered once: applying it again keeps what the user had before the first time.
                        const restore = s.presetRestore ?? (isRealisticLook(s) ? null : pickLook(s));
                        return {...REALISTIC_LOOK, presetRestore: restore};
                    }
                    if (id === "default") {
                        if (s.presetRestore) return {...s.presetRestore, presetRestore: null};
                        // Nothing replaced: the user's own look stays, unless it is the realistic one.
                        return isRealisticLook(s) ? pickLook(APPEARANCE_DEFAULTS) : {};
                    }
                    return {};
                }),
            reset: () => set({...APPEARANCE_DEFAULTS}),
            resetEdges: () => set({edgeColor: APPEARANCE_DEFAULTS.edgeColor, edgeBrightness: APPEARANCE_DEFAULTS.edgeBrightness}),
        }),
        {
            name: "ada-appearance",
            version: 2,
            migrate: (stored, version) => migrateAppearance(stored, version) as AppearanceState,
            // Where there is no browser storage (a worker, a test) the settings live for the page.
            storage: createJSONStorage(() => (typeof localStorage === "undefined" ? pageStorage : localStorage)),
            partialize: (s) => sanitizeAppearance(s),
            merge: (persisted, current) => ({...current, ...sanitizeAppearance(persisted)}),
        },
    ),
);

/** Whether every setting is at its default: what "Reset" would change nothing of. */
export function appearanceIsDefault(s: AppearanceSettings = useAppearanceStore.getState()): boolean {
    return (Object.keys(APPEARANCE_DEFAULTS) as (keyof AppearanceSettings)[]).every((k) => s[k] === APPEARANCE_DEFAULTS[k]);
}

/** Whether the element-edge settings are at their defaults. */
export function edgeLookIsDefault(s: AppearanceSettings = useAppearanceStore.getState()): boolean {
    return EDGE_SETTINGS.every((k) => s[k] === APPEARANCE_DEFAULTS[k]);
}

/** The two lights the settings ask for, as every renderer takes them: the ambient light's irradiance
 *  and the headlight's intensity (three.js' AmbientLight and DirectionalLight intensities, the
 *  adacpp renderer's `setLighting`). Brightness 1 gives PI / 2 and 1.4. */
export function lightIntensities(s: Pick<AppearanceSettings, "brightness" | "ambientShare"> = useAppearanceStore.getState()): {ambient: number; headlight: number} {
    if (s.brightness === 1 && s.ambientShare === APPEARANCE_DEFAULTS.ambientShare) {
        return {ambient: BASE_AMBIENT, headlight: BASE_HEADLIGHT};
    }
    const total = BASE_TOTAL * s.brightness;
    return {ambient: total * s.ambientShare, headlight: total * (1 - s.ambientShare)};
}

/** "#rrggbb" (sRGB) as linear RGB in 0..1. */
export function hexLinear(hex: string): [number, number, number] {
    const n = parseInt(hex.slice(1), 16);
    return [linearChannel(((n >> 16) & 255) / 255), linearChannel(((n >> 8) & 255) / 255), linearChannel((n & 255) / 255)];
}

/** "#rrggbb" as its three channels in 0..1, as written (sRGB). */
export function hexChannels(hex: string): [number, number, number] {
    const n = parseInt(hex.slice(1), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/** The base colour (linear) a model file gives an object that has no colour of its own: black from
 *  exporters that write none, and the light grey ada writes (`ada.visit.gltf.materials`). */
export const FILE_DEFAULT_BASE_COLOR = 204 / 255;

/** Whether a base colour (linear RGB) says "no colour": pure black, or the grey ada writes. */
export function isUncoloured(r: number, g: number, b: number): boolean {
    if (r === 0 && g === 0 && b === 0) return true;
    const near = (c: number) => Math.abs(c - FILE_DEFAULT_BASE_COLOR) < 1e-4;
    return near(r) && near(g) && near(b);
}

/** The default colour as linear RGB. At the default setting it is exactly the grey ada writes. */
export function defaultColorLinear(s: Pick<AppearanceSettings, "defaultColor"> = useAppearanceStore.getState()): [number, number, number] {
    if (s.defaultColor === APPEARANCE_DEFAULTS.defaultColor) return [FILE_DEFAULT_BASE_COLOR, FILE_DEFAULT_BASE_COLOR, FILE_DEFAULT_BASE_COLOR];
    return hexLinear(s.defaultColor);
}

/** The look settings the native (adacpp WebGPU) viewport hands its renderer: setPost and
 *  setEnvironment (adacpp docs/render_effects.md). At the defaults both are off and the renderer draws
 *  as it always has. Bloom needs the HDR target, so it switches it on with no tone mapping of its own. */
/** A moderate bloom: glowing parts and sparks halo, highlights barely. */
export const BLOOM_STRENGTH = 0.4;
export const LOOK_SETTINGS = ["toneMapping", "exposure", "bloom", "environment", "environmentIntensity"] as const satisfies readonly (keyof AppearanceSettings)[];

export function nativeLook(
    s: Pick<AppearanceSettings, (typeof LOOK_SETTINGS)[number]> = useAppearanceStore.getState(),
): {
    post: {hdr: boolean; toneMapping: "none" | "neutral" | "aces"; exposure: number; bloom: boolean; bloomStrength: number};
    environment: {enabled: boolean; intensity: number};
} {
    const hdr = s.toneMapping !== "off" || s.bloom;
    return {
        post: {hdr, toneMapping: s.toneMapping === "off" ? "none" : s.toneMapping, exposure: hdr ? s.exposure : 1, bloom: s.bloom, bloomStrength: BLOOM_STRENGTH},
        environment: {enabled: s.environment, intensity: s.environmentIntensity},
    };
}
