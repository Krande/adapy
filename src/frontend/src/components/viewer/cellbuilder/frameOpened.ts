import * as THREE from "three";
import {activeViewport} from "@/viewport/api/viewportApi";

/** Frame the camera on a newly opened model's cells, once per model: the builder's
 *  group is left out of "fit all" (it is an editing overlay), so a model opened into an
 *  empty scene would otherwise be looked at from wherever the camera was - often inside
 *  it. Returns the model now framed (null once none is open). A model whose cells are
 *  not drawn yet waits. */
export function frameOpenedModel(cells: THREE.Object3D, modelId: string | null, framed: string | null): string | null {
    if (!modelId) return null; // closed: the next open frames again, the same model too
    if (modelId === framed) return framed;
    const box = new THREE.Box3().setFromObject(cells);
    if (box.isEmpty()) return framed;
    const viewport = activeViewport();
    if (!viewport || !viewport.fitBox(box.min.toArray(), box.max.toArray())) return framed;
    return modelId;
}
