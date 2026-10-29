// Picking through the renderer adapter: "what is under this pixel" asked of whichever renderer draws.
// three.js answers with GpuMeshPicker / GpuPointPicker (WebGL id targets); the adacpp WebGPU renderer
// answers with its own id-buffer pick (adacpp Renderer::pick), mapped back to the same result shape
// here. Async because WebGPU reads back asynchronously; the three.js pickers resolve immediately.
//
// Point clouds still go to three.js' point picker in both modes: WebGPU draws points one pixel wide, so
// a pixel-exact pick would rarely land on one until the renderer has a point-size impostor pass.
import * as THREE from "three";
import type {GpuMeshPickResult} from "@/utils/mesh_select/GpuMeshPicker";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {usePerfStore} from "@/state/perfStore";
import type {AdacppViewport} from "./adacpp/adacppViewport";

let adacpp: AdacppViewport | null = null;

/** The adacpp viewport answering picks while it draws; null hands picking back to three.js. */
export function setPickingViewport(viewport: AdacppViewport | null): void {
    adacpp = viewport;
}

function activeAdacpp(): AdacppViewport | null {
    return adacpp && adacpp.alive ? adacpp : null;
}

type FaceRange = {start: number; length: number; rangeId: string; faceId: number; seq: number};
const faceCache = new WeakMap<CustomBatchedMesh, Promise<FaceRange[]>>();

/** A mesh's face ranges (gpuFacePicking), sorted by start; [] when the model has none. */
function faceRanges(mesh: CustomBatchedMesh): Promise<FaceRange[]> {
    let p = faceCache.get(mesh);
    if (!p) {
        p = (async () => {
            const {queryHasFaceRanges, queryAllFaceRanges} = await import("@/utils/mesh_select/queryMeshDrawRange");
            if (!(await queryHasFaceRanges(mesh.unique_key))) return [];
            let faces = (await queryAllFaceRanges(mesh.unique_key, mesh.name)) as FaceRange[];
            if (faces.length === 0 && mesh.userData?.node_id != null) {
                faces = (await queryAllFaceRanges(mesh.unique_key, `node${mesh.userData.node_id}`)) as FaceRange[];
            }
            return faces.filter((f) => f.length > 0).sort((a, b) => a.start - b.start);
        })().catch(() => []);
        faceCache.set(mesh, p);
    }
    return p;
}

function faceAt(faces: FaceRange[], indexPos: number): FaceRange | null {
    let lo = 0, hi = faces.length - 1;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        const f = faces[mid];
        if (indexPos < f.start) hi = mid - 1;
        else if (indexPos >= f.start + f.length) lo = mid + 1;
        else return f;
    }
    return null;
}

/** The model mesh + draw range (and face, with gpuFacePicking) under a client position. */
export async function pickMeshAt(clientX: number, clientY: number): Promise<GpuMeshPickResult> {
    const vp = activeAdacpp();
    if (!vp) {
        // Loaded on first use: the three.js pickers pull in their WebGL picker workers.
        const {gpuMeshPicker} = await import("@/utils/mesh_select/GpuMeshPicker");
        return gpuMeshPicker.pickAt(clientX, clientY);
    }
    const raw = await vp.pickRaw(clientX, clientY);
    if (!raw) return null;
    const entry = vp.entryForInstance(raw.instance);
    if (!entry || !(entry.obj instanceof CustomBatchedMesh)) return null;
    const rangeId = entry.segmentIds?.[raw.segment];
    if (!rangeId) return null; // a gap between draw ranges: nothing selectable there
    const result: NonNullable<GpuMeshPickResult> = {
        mesh: entry.obj,
        rangeId,
        worldPosition: new THREE.Vector3(...raw.world),
    };
    if (usePerfStore.getState().gpuFacePicking && raw.triangle !== 0xffffffff) {
        const face = faceAt(await faceRanges(entry.obj), raw.triangle * 3);
        if (face) {
            result.faceId = face.faceId;
            result.seq = face.seq;
            result.faceStart = face.start;
            result.faceLen = face.length;
        }
    }
    return result;
}

/** The point (of a points object) under a client position. three.js' point picker in both modes. */
export async function pickPointAt(clientX: number, clientY: number) {
    const {gpuPointPicker} = await import("@/utils/mesh_select/GpuPointPicker");
    return gpuPointPicker.pickAt(clientX, clientY);
}
