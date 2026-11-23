// Visual effects applied to the viewport from outside a model's file: point lights, particle emitters and
// emissive ramps, as plain data in the shape of a GLB's "ada_effects" (`ada.visit.gltf.effects.EffectSpec`,
// adacpp docs/render_effects.md). What `ctx.effects` (plugins/pluginEffects.ts) hands the viewport on screen.
//
// A model can carry the same description in its own file, read when it is loaded; this is for effects
// that are made, changed or taken off while the model stays loaded.
//
// No three.js in this module.

/** One effect, as plain data: the keys of the "ada_effects" schema (camelCase). */
export type EffectData = Readonly<Record<string, unknown>>;

/** A description of effects. `post` and `environment` are not taken: the look is the viewer's setting. */
export interface EffectsDescription {
    version?: number;
    /** Point lights: `position` (in the scene's coordinates), colour, intensity, flicker, start / end. */
    lights?: readonly EffectData[];
    /** Particle emitters: `origin` (in the scene's coordinates), direction, rate, start / end, ... */
    emitters?: readonly EffectData[];
    /** Emissive ramps: `targets` are object ids of the model the effects are applied with, each taking
     *  everything below it too. */
    emissive?: readonly (EffectData & {targets?: readonly unknown[]})[];
}

/** The clock the effects are drawn at: seconds, and a way to hear that it moved. */
export interface EffectsClock {
    time(): number;
    subscribe(listener: () => void): () => void;
}

export interface ApplyEffectsOptions {
    /** The source name of the model the ramps' targets are objects of. The effects are drawn only while
     *  that model is on screen, and are put on it again whenever it is loaded again. */
    model?: string;
    /** The clock to draw them at (the playback clock); without one, the viewport's own effects clock. */
    clock?: EffectsClock;
}

/** Effects a viewport holds, until removed. */
export interface ViewportEffects {
    /** Show `spec` in place of what was shown. */
    update(spec: EffectsDescription): void;
    remove(): void;
    /** The ramps' targets that named no object of the model, as last applied. */
    unresolved(): readonly string[];
}
