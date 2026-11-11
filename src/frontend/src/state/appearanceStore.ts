import {create} from "zustand";
import {createJSONStorage, persist} from "zustand/middleware";

import {linearChannel} from "@/utils/scene/sceneColor";

// How the model looks, whichever renderer draws it: three.js, the adacpp WebGPU renderer that mirrors
// the three.js scene, or the three.js-free viewport. One store, read by all three, so a setting
// means the same picture on each. Persisted like the performance options.
//
// The defaults are the look the viewer had before the settings existed: an ambient light of
// irradiance PI / 2 and a headlight of intensity 1.4, a #393939 background, and light grey
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
    /** Light a painted FEA result like the model. Off, a result colour is drawn exactly as the
     *  legend shows it, whichever way its face is turned. */
    shadeResults: boolean;
}

export const APPEARANCE_DEFAULTS: Readonly<AppearanceSettings> = {
    defaultColor: "#e7e7e7",
    brightness: 1,
    ambientShare: BASE_AMBIENT / BASE_TOTAL,
    background: "#393939",
    shadeResults: false,
};

export const BRIGHTNESS_RANGE = {min: 0.25, max: 2, step: 0.05} as const;

export interface AppearanceState extends AppearanceSettings {
    setDefaultColor: (hex: string) => void;
    setBrightness: (value: number) => void;
    setAmbientShare: (value: number) => void;
    setBackground: (hex: string) => void;
    setShadeResults: (on: boolean) => void;
    /** Back to the defaults. */
    reset: () => void;
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
    };
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
            reset: () => set({...APPEARANCE_DEFAULTS}),
        }),
        {
            name: "ada-appearance",
            version: 1,
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

/** The two lights the settings ask for, as every renderer takes them: the ambient light's irradiance
 *  and the headlight's intensity (three.js' AmbientLight and DirectionalLight intensities, the
 *  adacpp renderer's `setLighting`). The defaults give PI / 2 and 1.4. */
export function lightIntensities(s: Pick<AppearanceSettings, "brightness" | "ambientShare"> = useAppearanceStore.getState()): {ambient: number; headlight: number} {
    if (s.brightness === APPEARANCE_DEFAULTS.brightness && s.ambientShare === APPEARANCE_DEFAULTS.ambientShare) {
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
