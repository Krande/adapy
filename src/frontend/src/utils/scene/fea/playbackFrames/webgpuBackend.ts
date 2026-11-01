// Recorded-frame displacement for the adacpp WebGPU renderer.
//
// That renderer mirrors the three.js scene graph (viewport/adacpp/sceneSync.ts) and
// uploads a geometry's morph buffer whenever the morph attribute's identity or
// version changes -- a straight buffer write, none of three.js's morph-texture
// rebuild. So the cheap way to show a recorded frame there is the plain one: write
// its displacement into the mesh's morph (and the element-edge wireframe's) in
// place and let the mirror send it.
//
// What this saves over an ordinary paint is everything before that write: the
// fetch and the colouring kernel.

import type * as THREE from "three";

import {elementLocalMap} from "../elementLocalGeometry";
import {writeMorphTarget} from "../gpuRefresh";
import type {FrameDisplacementBackend} from "./backend";

export class WebGpuFrameBackend implements FrameDisplacementBackend {
    readonly kind = "adacpp-webgpu" as const;
    private slots: Float32Array[] = [];
    private nSource = 0;

    displacementBytes(nSource: number): number {
        return nSource * 12;
    }

    allocate(nSource: number, capacity: number): void {
        this.nSource = nSource;
        this.slots = new Array<Float32Array>(capacity);
    }

    store(slot: number, sourceDisplacement: Float32Array): void {
        this.slots[slot] = Float32Array.from(sourceDisplacement.subarray(0, this.nSource * 3));
    }

    present(slot: number, mesh: THREE.Mesh, edges: readonly THREE.LineSegments[]): void {
        const disp = this.slots[slot];
        if (!disp) return;
        writeMorphTarget(mesh.geometry, disp, elementLocalMap(mesh.geometry));
        for (const e of edges) writeMorphTarget(e.geometry as THREE.BufferGeometry, disp, null);
    }

    release(): void {
        // Nothing is overridden: the ordinary paint that follows writes the morph.
    }

    dispose(): void {
        this.slots = [];
    }
}
