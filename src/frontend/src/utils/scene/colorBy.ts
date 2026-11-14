// Colour by property: paint the scene by an attribute, with a legend and a reset.
//
// No second colouring pipeline. A source either goes through the one that
// exists or through the batched mesh's own range colours:
//
//   fea:<field>  a model property of a loaded FEA result (section, material,
//                thickness - the manifest's "property" fields). Selecting it is
//                selecting a result field, so the FEA painter colours the
//                elements and the colour legend draws the categories.
//   model        every loaded model in its own colour (`CustomBatchedMesh.setRangeColors`),
//                with a legend of the models.
//   <registered> a source a plugin adds with `registerColorBySource`.
//
// Reset puts the previous result field back (or switches result colours off if
// there was none), and clears range colours.

import * as THREE from "three";

import {useColorStore} from "@/state/colorLegendStore";
import {useColorByStore, type ColorByLegendData} from "@/state/colorByStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {loadedSourceGroups} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {categoryColor, isPropertyField} from "@/utils/scene/fea/propertyColors";
import {noteOwnerPainted} from "@/utils/scene/fea/modeSceneColor";
import {selectFeaResultComponent} from "@/utils/scene/fea/resultSelection";

export const COLOR_BY_MODEL = "model";
export const COLOR_BY_FEA_PREFIX = "fea:";

/** A thing the scene can be coloured by. */
export interface ColorBySource {
    id: string;
    label: string;
    /** Offered only while this holds (default: always). */
    available?(): boolean;
    /** Paint; return a legend if core's colour legend does not draw this one. */
    apply(): Promise<ColorByLegendData | void> | ColorByLegendData | void;
    /** Undo `apply` (default: nothing to undo). */
    reset?(): Promise<void> | void;
}

const registered = new Map<string, ColorBySource & {owner: string}>();
const listeners = new Set<() => void>();
let version = 0;
const changed = () => {
    version++;
    for (const l of listeners) l();
};

/** Add a source (a later one with the same id replaces it). */
export function registerColorBySource(source: ColorBySource, owner: string): void {
    registered.set(source.id, {...source, owner});
    changed();
}

export function unregisterColorBySource(id: string): void {
    if (registered.delete(id)) changed();
}

export function subscribeColorBySources(cb: () => void): () => void {
    listeners.add(cb);
    return () => listeners.delete(cb);
}

export function colorBySourcesVersion(): number {
    return version;
}

function cssRgb(rank: number): string {
    const c = new Float32Array(3);
    categoryColor(rank, c);
    return `rgb(${Math.round(c[0] * 255)}, ${Math.round(c[1] * 255)}, ${Math.round(c[2] * 255)})`;
}

/** The FEA model-property fields of the loaded result. */
export function feaPropertyFields(): {id: string; label: string}[] {
    const {manifest, sessionActive} = useFeaAnimationStore.getState();
    if (!sessionActive || !manifest) return [];
    return manifest.fields
        .filter((f) => isPropertyField(f))
        .map((f) => ({id: COLOR_BY_FEA_PREFIX + f.name_canonical, label: f.group_path?.join(" / ") ?? f.name_canonical}));
}

/** Everything the scene can be coloured by now, in the order a picker lists it. */
export function colorBySources(): {id: string; label: string}[] {
    const out: {id: string; label: string}[] = [...feaPropertyFields()];
    if (loadedSourceGroups.size > 1) out.push({id: COLOR_BY_MODEL, label: "Model"});
    for (const s of registered.values()) {
        if (!s.available || s.available()) out.push({id: s.id, label: s.label});
    }
    return out;
}

// Meshes painted by the `model` source, so a reset reaches exactly them.
const painted = new Set<CustomBatchedMesh>();

function clearModelPaint(): void {
    for (const m of painted) m.disableVertexColorsAndResetMaterial();
    painted.clear();
    requestRender();
}

function paintModels(): ColorByLegendData {
    const fea = useFeaAnimationStore.getState().mesh;
    const entries: ColorByLegendData["entries"] = [];
    let rank = 0;
    for (const [name, group] of [...loadedSourceGroups.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
        const css = cssRgb(rank);
        const c = new Float32Array(3);
        categoryColor(rank, c);
        const color = new THREE.Color(c[0], c[1], c[2]);
        rank++;
        entries.push({label: name, color: css});
        group.traverse((o) => {
            if (!(o instanceof CustomBatchedMesh) || o === (fea as unknown)) return;
            o.setRangeColors(new Map([...o.drawRanges.keys()].map((id) => [id, color])));
            painted.add(o);
        });
    }
    requestRender();
    return {title: "Colour by model", entries};
}

/** Colour the scene by source `id`. */
export async function applyColorBy(id: string): Promise<void> {
    const store = useColorByStore.getState();
    const fromFea = store.activeId?.startsWith(COLOR_BY_FEA_PREFIX) ?? false;
    const toFea = id.startsWith(COLOR_BY_FEA_PREFIX);
    // A property to a property keeps the result field remembered from before the first.
    if (!(fromFea && toFea)) await resetColorBy();
    if (toFea) {
        const fea = useFeaAnimationStore.getState();
        const name = id.slice(COLOR_BY_FEA_PREFIX.length);
        const field = fea.manifest?.fields.find((f) => f.name_canonical === name);
        if (!field) throw new Error(`Unknown property ${name}`);
        if (!fromFea) {
            const cur = fea.manifest?.fields.find((f) => f.name_canonical === fea.fieldName);
            useColorByStore.getState().set({
                previousField: fea.fieldName && cur && !isPropertyField(cur) ? {fieldName: fea.fieldName, reduction: fea.reduction} : null,
            });
        }
        // The mode on top paints this (a property, in Inspect): say so before the load, so
        // the loader credits the repaint to it rather than setting it aside as a result.
        noteOwnerPainted();
        await selectFeaResultComponent(name);
        useFeaAnimationStore.getState().setResultColorsVisible(true);
        useColorStore.getState().setShowLegend(true);
        useColorByStore.getState().set({activeId: id, legend: null});
        return;
    }
    if (id === COLOR_BY_MODEL) {
        const legend = paintModels();
        useColorByStore.getState().set({activeId: id, legend});
        return;
    }
    const src = registered.get(id);
    if (!src) throw new Error(`Unknown colour source ${id}`);
    const legend = (await src.apply()) || null;
    useColorByStore.getState().set({activeId: id, legend});
    requestRender();
}

/** Undo the colouring: the previous result field (or no result colours), no range colours, no legend. */
export async function resetColorBy(): Promise<void> {
    const {activeId, previousField} = useColorByStore.getState();
    if (!activeId) return;
    useColorByStore.getState().set({activeId: null, legend: null, previousField: null});
    if (activeId.startsWith(COLOR_BY_FEA_PREFIX)) {
        if (previousField) await selectFeaResultComponent(previousField.fieldName, previousField.reduction);
        else useFeaAnimationStore.getState().setResultColorsVisible(false);
    } else if (activeId === COLOR_BY_MODEL) {
        clearModelPaint();
    } else {
        await registered.get(activeId)?.reset?.();
    }
    requestRender();
}

/** The source after the active one in the picker's order (the first when none is active). */
export function nextColorBySource(): string | null {
    const all = colorBySources();
    if (!all.length) return null;
    const i = all.findIndex((s) => s.id === useColorByStore.getState().activeId);
    return all[(i + 1) % all.length].id;
}

/** Clear -- unit tests only. */
export function resetColorBySources(): void {
    registered.clear();
    painted.clear();
    changed();
}
