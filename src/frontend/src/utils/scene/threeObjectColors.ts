// Colours the user gives objects, on the three.js viewport (viewport/api/objectColors.ts): a colour
// per draw range of a batched mesh, drawn through the mesh's one colour attribute
// (`CustomBatchedMesh.setUserColors`). No material and no draw call per object, and the adacpp
// WebGPU renderer that mirrors the scene reads the same attribute.

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {requestRender} from "@/state/perfStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {parseHexColor, type ObjectColorState} from "@/viewport/api/objectColors";
import type {ObjectRef} from "@/viewport/api/viewportApi";
import {batchedMeshOf, isBatched} from "./meshLookup";

/** Changes whenever a colour the user gave an object does; the last state handed to the history. */
let version = 0;
let cached: {version: number; state: ThreeColorState} | null = null;

class ThreeColorState implements ObjectColorState {
    constructor(
        /** Each mesh that has user colours, with a copy of them. */
        readonly byMesh: ReadonlyMap<CustomBatchedMesh, Uint32Array>,
        readonly count: number,
    ) {}
}

/** The faces of an FEA result (its mesh, its beam sections): their colours are the result's. */
function drawsResult(mesh: CustomBatchedMesh): boolean {
    const fea = useFeaAnimationStore.getState().mesh as unknown;
    return mesh === fea || mesh.parent === fea || mesh.userData?.feaBeamSolids === true;
}

function eachMesh(visit: (mesh: CustomBatchedMesh) => void): void {
    getViewerRuntime().scene.current?.traverse((o) => {
        if (isBatched(o)) visit(o);
    });
}

/** `ViewportObjectColors.setObjectColors` over the three.js scene, without the undo step. */
export function setThreeObjectColors(refs: Iterable<ObjectRef> | null, color: string | null): number {
    const packed = color === null ? null : parseHexColor(color);
    if (color !== null && packed === null) return 0;
    let changed = 0;
    if (refs === null) {
        eachMesh((mesh) => {
            if (!drawsResult(mesh)) changed += mesh.setUserColors(null, packed);
        });
    } else {
        const tree = useTreeViewStore.getState();
        const cache = new Map<string, CustomBatchedMesh>();
        // Consecutive objects are nearly always drawn by the same mesh: its ids are gathered into
        // one reused list and written in one pass when the mesh changes.
        const run: string[] = [];
        let model: string | null = null;
        let node: string | null | undefined;
        let mesh: CustomBatchedMesh | null = null;
        const flush = () => {
            if (mesh && run.length && !drawsResult(mesh)) changed += mesh.setUserColors(run, packed);
            run.length = 0;
        };
        for (const ref of refs) {
            const name = ref.node !== undefined ? ref.node : tree.findNodeByRangeId(ref.model, ref.id)?.node_name;
            if (ref.model !== model || name !== node) {
                flush();
                model = ref.model;
                node = name;
                mesh = name ? batchedMeshOf(ref.model, name, cache) : null;
            }
            if (mesh) run.push(ref.id);
        }
        flush();
    }
    if (changed) {
        version++;
        requestRender();
    }
    return changed;
}

export function threeObjectColorCount(): number {
    let n = 0;
    eachMesh((mesh) => void (n += mesh.userColorCount));
    return n;
}

export function captureThreeObjectColors(): ObjectColorState {
    if (cached?.version === version) return cached.state;
    const byMesh = new Map<CustomBatchedMesh, Uint32Array>();
    let count = 0;
    eachMesh((mesh) => {
        const colors = mesh.userColorSnapshot();
        if (!colors) return;
        byMesh.set(mesh, colors);
        count += mesh.userColorCount;
    });
    const state = new ThreeColorState(byMesh, count);
    cached = {version, state};
    return state;
}

/** Go back to a captured state: every mesh on screen gets the colours it had in it, or none. A
 *  mesh that is no longer in the scene is left out. */
export function restoreThreeObjectColors(state: ObjectColorState): void {
    if (!(state instanceof ThreeColorState)) return;
    eachMesh((mesh) => {
        if (!drawsResult(mesh)) mesh.restoreUserColors(state.byMesh.get(mesh) ?? null);
    });
    version++;
    // The state handed in is the state now: asked again, the history sees no change.
    cached = {version, state};
    requestRender();
}
