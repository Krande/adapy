// Where a loaded model is placed in the world, as plain numbers: world = model + translation. Every
// viewport places models by this one rule. A model far from the origin is moved near it, because
// 32-bit positions that far out draw with visible precision artefacts. No three.js.

export type Vec3 = [number, number, number];
/** min xyz, max xyz. */
export type Box6 = readonly [number, number, number, number, number, number];

/** The translation that recentres `box`: its centre over the origin, its bottom 5% of its height above
 *  the ground plane (z = 0, or y = 0 in a Y-up world). */
export function recentreTranslation(box: Box6, zIsUp: boolean): Vec3 {
    const t: Vec3 = [-(box[0] + box[3]) / 2, -(box[1] + box[4]) / 2, -(box[2] + box[5]) / 2];
    const up = zIsUp ? 2 : 1;
    const height = box[up + 3] - box[up];
    t[up] = -box[up] + height * 0.05;
    return t;
}

export interface PlacementOptions {
    zIsUp: boolean;
    /** The user locked the translation: a model is drawn where its file puts it. */
    locked: boolean;
    /** The translation the models on screen are placed by; null before the first. */
    current: Vec3 | null;
    /** Place this model by `current` (it joins the models on screen) rather than recentring. */
    reuseCurrent: boolean;
}

/** How a model whose box (in its own frame) is `box` is placed: the translation to apply (null: none),
 *  and whether it becomes the scene's translation for the models that follow. */
export function placeModel(box: Box6, opts: PlacementOptions): {translation: Vec3 | null; isNew: boolean} {
    if (opts.current && opts.reuseCurrent) return {translation: opts.current, isNew: false};
    if (opts.locked) return {translation: null, isNew: false};
    return {translation: recentreTranslation(box, opts.zIsUp), isNew: true};
}
