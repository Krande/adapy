// The picking adapter maps the adacpp renderer's raw GPU pick (instance, segment, triangle, point) back to
// the result shape three.js' GpuMeshPicker returns, so click selection does not care which renderer drew.
import {afterEach, test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {usePerfStore} from "@/state/perfStore";
import {pickMeshAt, setPickingViewport} from "@/viewport/picking";
import type {AdacppViewport} from "@/viewport/adacpp/adacppViewport";
import type {RawPick} from "@/viewport/adacpp/types";

function mesh(): CustomBatchedMesh {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(12 * 3), 3));
    g.setIndex(new THREE.BufferAttribute(Uint32Array.from({length: 12}, (_, i) => i), 1));
    return new CustomBatchedMesh(g, new THREE.MeshStandardMaterial(), new Map([["a", [0, 6]], ["b", [6, 6]]]), "m", true, null);
}

function fakeViewport(raw: RawPick | null, entries: Record<number, {obj: THREE.Object3D; segmentIds: string[] | null}>) {
    return {
        alive: true,
        pickRaw: async () => raw,
        entryForInstance: (i: number) => entries[i] ?? null,
    } as unknown as AdacppViewport;
}

const hit = (instance: number, segment: number): RawPick => ({hit: true, instance, segment, triangle: 3, world: [1, 2, 3]});

afterEach(() => setPickingViewport(null));

test("a WebGPU pick resolves to the batched mesh, its range id and the hit point", async () => {
    usePerfStore.setState({gpuFacePicking: false});
    const m = mesh();
    setPickingViewport(fakeViewport(hit(7, 1), {7: {obj: m, segmentIds: ["a", "b"]}}));
    const r = await pickMeshAt(10, 10);
    assert.ok(r);
    assert.equal(r.mesh, m);
    assert.equal(r.rangeId, "b");
    assert.deepEqual(r.worldPosition.toArray(), [1, 2, 3]);
    assert.equal(r.faceId, undefined);
});

test("a pick on a gap between draw ranges, or on a non-model object, selects nothing", async () => {
    const m = mesh();
    setPickingViewport(fakeViewport(hit(7, 0), {7: {obj: m, segmentIds: ["", "b"]}}));
    assert.equal(await pickMeshAt(0, 0), null);
    setPickingViewport(fakeViewport(hit(3, 0), {3: {obj: new THREE.Mesh(), segmentIds: null}}));
    assert.equal(await pickMeshAt(0, 0), null);
});

test("a miss stays a miss", async () => {
    setPickingViewport(fakeViewport(null, {}));
    assert.equal(await pickMeshAt(0, 0), null);
});
