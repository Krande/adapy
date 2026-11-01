// The renderer adapter of the recorded playback frames.
//
// A recorded frame has two halves. Colours, the beam solids and the beam result
// lines are written into the three.js scene graph, which every renderer draws from
// (three.js directly, the adacpp WebGPU renderer through its scene mirror), so the
// recorder handles them itself. The displacement is where renderers differ: putting
// it on screen cheaply is renderer-specific, so it goes through this interface.
//
//   * three.js (threeBackend.ts): one half-float array-texture layer per frame and
//     a vertex-shader patch that reads it. Showing a frame sets a uniform; nothing is
//     rebuilt or uploaded. (three.js would otherwise rebuild its morph texture from
//     scratch for every frame.)
//   * adacpp WebGPU (webgpuBackend.ts): the renderer uploads a changed morph buffer
//     directly and cheaply, so the frame's displacement is written into the mesh's
//     morph in place and the scene mirror sends it.
//
// Callers (the recorder, the loader, the playback driver) only ever talk to this
// interface; `frameBackendFor` picks the implementation for the active renderer.

import type * as THREE from "three";

import type {RenderBackend} from "@/state/perfStore";

export interface FrameDisplacementBackend {
    readonly kind: RenderBackend;

    /** Bytes one frame's displacement costs this backend for `nSource` source vertices. */
    displacementBytes(nSource: number): number;

    /** Reserve room for `capacity` frames of `nSource` source vertices. Called once. */
    allocate(nSource: number, capacity: number): void;

    /** Keep `sourceDisplacement` (xyz per source vertex) as slot `slot`. */
    store(slot: number, sourceDisplacement: Float32Array): void;

    /**
     * Put slot `slot` on `mesh` and on its source-numbered wireframes (`edges`: the
     * element edges and the beam-element lines -- every child the ordinary paint
     * gives a source-numbered morph). The mesh's `morphTargetInfluences[0]` is the
     * scale, as for the ordinary paint.
     */
    present(slot: number, mesh: THREE.Mesh, edges: readonly THREE.LineSegments[]): void;

    /** Stop presenting: the meshes' own (ordinarily painted) morph applies again. */
    release(): void;

    /** Free everything `allocate` and `store` took. */
    dispose(): void;
}
