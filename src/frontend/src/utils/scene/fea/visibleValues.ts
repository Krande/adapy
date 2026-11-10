import type * as THREE from "three";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";

// Which of a field's values are actually on screen.
//
// A categorical legend that names every material in the deck is describing the
// deck, not the picture. Section a model down to two frames, or isolate one
// group, and a twelve-entry legend beside a two-colour view is worse than no
// legend: the reader has to work out which entries are lies.
//
// The map is recorded where the colours are computed — one entry per drawn
// element, keyed by the same draw-range id the mesh hides by — so filtering it is
// a set lookup rather than a second pass over the field data.

/** Draw-range id → the field value that element was painted with. Read-only: the
 *  consumers below only iterate it. */
export type ValueByRange = Pick<ReadonlyMap<string, number>, "size" | "values" | typeof Symbol.iterator>;

/**
 * A ``ValueByRange`` over parallel arrays: the element ids (shared, built once per
 * mesh and element bucket) and this paint's values. The element kernel records one
 * per frame, and a ``Map`` with a string key per element -- a quarter of a million
 * of them on a shell model -- was most of what a frame of a stress animation cost.
 * NaN entries stand for elements that carry no value and are skipped.
 */
export class PaintedValues implements ValueByRange {
    private readonly parts: {keys: readonly string[]; values: Float64Array}[] = [];
    private count = 0;

    add(keys: readonly string[], values: Float64Array): void {
        this.parts.push({keys, values});
        for (let i = 0; i < values.length; i++) if (!Number.isNaN(values[i])) this.count++;
    }

    get size(): number {
        return this.count;
    }

    *[Symbol.iterator](): MapIterator<[string, number]> {
        for (const {keys, values} of this.parts) {
            for (let i = 0; i < values.length; i++) {
                if (!Number.isNaN(values[i])) yield [keys[i], values[i]];
            }
        }
    }

    *values(): MapIterator<number> {
        for (const [, value] of this) yield value;
    }
}

const KEY = "__feaValueByRange";

/** Record what each element was painted with. Called by the colouring kernels. */
export function recordValueByRange(mesh: THREE.Object3D, values: ValueByRange): void {
    mesh.userData[KEY] = values;
}

/** Forget it — the field changed, or the mesh is being replaced. */
export function clearValueByRange(mesh: THREE.Object3D): void {
    delete mesh.userData[KEY];
}

interface HideableMesh {
    getHiddenRanges?: () => ReadonlySet<string>;
}

/**
 * The distinct field values among elements that are currently drawn.
 *
 * Null when nothing has been recorded — the caller then shows everything, which
 * is the right answer for "we do not know" and matches the old behaviour.
 *
 * Hidden here means hidden by draw range: an isolated group, a manual hide.
 * Section-plane clipping is a shader effect on geometry that is still drawn, so
 * it is deliberately NOT counted — an element half-cut by a plane is still on
 * screen, and its material still belongs in the legend.
 */
export function visibleFieldValues(mesh: THREE.Object3D | null): Set<number> | null {
    if (!mesh) return null;
    const values = mesh.userData[KEY] as ValueByRange | undefined;
    if (!values || values.size === 0) return null;
    const hidden = (mesh as unknown as HideableMesh).getHiddenRanges?.() ?? new Set<string>();
    const out = new Set<number>();
    for (const [rangeId, value] of values) {
        if (hidden.has(rangeId)) continue;
        out.add(value);
    }
    return out;
}

/**
 * Every distinct value painted on `mesh`, hidden elements included, ascending;
 * null when nothing has been recorded. What a ranked colour scale was ranked
 * over, so a legend can colour its entries exactly as the painter did.
 */
export function paintedFieldValues(mesh: THREE.Object3D | null): number[] | null {
    if (!mesh) return null;
    const values = mesh.userData[KEY] as ValueByRange | undefined;
    if (!values || values.size === 0) return null;
    const seen = new Set<number>();
    for (const value of values.values()) if (Number.isFinite(value)) seen.add(value);
    return Array.from(seen).sort((a, b) => a - b);
}

/** What a result drawn without a three.js mesh was painted with (a result drawn through result
 *  layers), and how to ask whether one of its elements is hidden. */
let layerValues: {values: ValueByRange; isHidden: (rangeId: string) => boolean} | null = null;

/** Record what each element of a result drawn through result layers was painted with; null
 *  forgets it. */
export function recordLayerValues(values: ValueByRange | null, isHidden: (rangeId: string) => boolean = () => false): void {
    layerValues = values ? {values, isHidden} : null;
}

/** The same, for whichever mesh the active result session is painting. */
export function paintedFieldValuesForSession(): number[] | null {
    const mesh = useFeaAnimationStore.getState().mesh;
    if (mesh || !layerValues || layerValues.values.size === 0) return paintedFieldValues(mesh);
    const seen = new Set<number>();
    for (const value of layerValues.values.values()) if (Number.isFinite(value)) seen.add(value);
    return Array.from(seen).sort((a, b) => a - b);
}

/** The same, for whichever mesh the active result session is painting. */
export function visibleFieldValuesForSession(): Set<number> | null {
    const mesh = useFeaAnimationStore.getState().mesh;
    if (mesh || !layerValues || layerValues.values.size === 0) return visibleFieldValues(mesh);
    const out = new Set<number>();
    for (const [rangeId, value] of layerValues.values) if (!layerValues.isHidden(rangeId)) out.add(value);
    return out;
}
