// The loaded CAD models' per-face regions (face_ranges), found by source face id, and focusing one:
// highlight it and frame the camera on it. Shared by the info box's face search and the plugin verbs
// (`findFaces` / `focusFace`), so a UI shell gets the same lookup without reaching into the scene.

import * as THREE from "three";

import {useObjectInfoStore} from "@/state/objectInfoStore";
import {useOptionsStore} from "@/state/optionsStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {frameBox} from "@/components/viewer/sceneHelpers/cameraFit";
import {CustomBatchedMesh} from "./CustomBatchedMesh";
import {clearFaceHighlight, setFaceHighlight} from "./faceHighlight";
import {queryAllFaceRanges} from "./queryMeshDrawRange";

/** One face region: the source face id, its occurrence (`seq`) and the model it is in (`model`, opaque).
 *  `start` / `length` are the face's triangles in that model's index buffer. */
export interface FaceEntry {
    faceId: number;
    seq: number;
    start: number;
    length: number;
    model: string;
}

function batchedMeshes(): CustomBatchedMesh[] {
    const scene = getViewerRuntime().scene.current;
    const out: CustomBatchedMesh[] = [];
    scene?.traverse((o) => {
        if (o instanceof CustomBatchedMesh) out.push(o);
    });
    return out;
}

function meshByKey(key: string): CustomBatchedMesh | null {
    return batchedMeshes().find((m) => m.unique_key === key) ?? null;
}

/** Every face region of every loaded model, by face id then occurrence. queryAllFaceRanges returns
 *  ABSOLUTE index ranges keyed by the mesh name the metadata cache used: mesh.name, else
 *  node<node_id>, the same fallback handleClickMesh uses. */
export async function findFaces(): Promise<FaceEntry[]> {
    const entries: FaceEntry[] = [];
    for (const mesh of batchedMeshes()) {
        const names = [mesh.name];
        const nodeId = (mesh.userData as {node_id?: unknown})?.node_id;
        if (nodeId != null) names.push(`node${nodeId}`);
        for (const nm of names) {
            const got = await queryAllFaceRanges(mesh.unique_key, nm);
            if (got && got.length) {
                for (const f of got)
                    entries.push({faceId: f.faceId, seq: f.seq, start: f.start, length: f.length, model: mesh.unique_key});
                break;
            }
        }
    }
    return entries.sort((a, b) => a.faceId - b.faceId || a.seq - b.seq);
}

/** World-space box of a face's triangles. */
export function faceWorldBox(mesh: CustomBatchedMesh, start: number, length: number): THREE.Box3 {
    const box = new THREE.Box3();
    const geom = mesh.geometry as THREE.BufferGeometry;
    const index = geom.getIndex();
    const pos = geom.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!index || !pos) return box;
    const end = Math.min(start + length, index.count);
    const v = new THREE.Vector3();
    for (let i = start; i < end; i++) {
        const vi = index.getX(i);
        box.expandByPoint(v.set(pos.getX(vi), pos.getY(vi), pos.getZ(vi)));
    }
    mesh.updateWorldMatrix(true, false);
    return box.applyMatrix4(mesh.matrixWorld);
}

type View = Pick<ReturnType<typeof getViewerRuntime>, "camera" | "controls">;

/** Highlight `face` (switching on face-level picking so the highlight shows), name it in the info box,
 *  and frame the camera on it unless `frame` is false. `null` clears the highlight. False when the
 *  face's model is no longer loaded. `view`: the viewer to frame (a component passes its own; the
 *  default is the runtime's). */
export function focusFace(face: FaceEntry | null, opts?: {frame?: boolean; view?: View}): boolean {
    if (face === null) {
        clearFaceHighlight();
        useObjectInfoStore.getState().setClickedFace(null);
        requestRender();
        return true;
    }
    const mesh = meshByKey(face.model);
    if (!mesh) return false;
    useOptionsStore.getState().setFaceLevelPicking(true);
    setFaceHighlight(mesh, face.start, face.length);
    useObjectInfoStore.getState().setClickedFace({faceId: face.faceId, seq: face.seq});
    if (opts?.frame !== false) {
        const {camera, controls} = opts?.view ?? getViewerRuntime();
        const box = faceWorldBox(mesh, face.start, face.length);
        if (camera.current && controls.current && !box.isEmpty()) frameBox(box, camera.current, controls.current);
    }
    requestRender();
    return true;
}
