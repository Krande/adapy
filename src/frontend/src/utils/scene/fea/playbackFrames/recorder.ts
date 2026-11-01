// The recorded frames of one play, renderer-agnostic.
//
// The first pass through a time history paints each frame the ordinary way; the
// recorder then keeps what that paint produced so the next passes can show the
// frame without repainting:
//   * the displacement per SOURCE vertex -- handed to the renderer adapter
//     (backend.ts), which knows how to put it on screen cheaply;
//   * the result-mesh colours, as bytes;
//   * the beam solids' colours and morph, and the beam result lines -- small, so
//     they are simply re-installed.
// Colours and beams go through the three.js scene graph, which every renderer
// draws from (three.js directly, adacpp WebGPU through its mirror).
//
// Fixed capacity, chosen from the step-cache budget when the recording starts; the
// step cache is shrunk by what it reserves, so the two together stay within one
// budget. Frames past the capacity keep painting the ordinary way.

import * as THREE from "three";

import {FEA_STEP_CACHE} from "@/services/fea/stepCache";
import {writeColorAttribute, writeMorphTarget} from "../gpuRefresh";
import {clearResultLineSegments, hasResultLineSegments, installResultLineSegments} from "../resultLineSegments";
import type {FrameDisplacementBackend} from "./backend";

/** Share of the step-cache budget the recording may take. */
const BUDGET_SHARE = 0.5;

/** The element-edge wireframe in source numbering, the one child that moves with
 *  the displacement. */
export const EDGE_CHILD = "fea-element-edges";

interface LineData {
    positions: Float32Array;
    colors: Float32Array;
    displacement: Float32Array;
    segmentIds: readonly string[];
}

interface FrameRecord {
    slot: number;
    /** Result-mesh colours per render vertex, 0..255 per channel. */
    colors: Uint8Array;
    beamColors: Uint8Array | null;
    beamMorph: Float32Array | null;
    lines: LineData | null;
}

function toBytes(src: ArrayLike<number>, out: Uint8Array): void {
    for (let i = 0; i < out.length; i++) {
        const v = src[i];
        out[i] = v <= 0 ? 0 : v >= 1 ? 255 : Math.round(v * 255);
    }
}

function fromBytes(src: Uint8Array, out: Float32Array): void {
    const inv = 1 / 255;
    for (let i = 0; i < out.length; i++) out[i] = src[i] * inv;
}

/** The source-numbered edge wireframe of `mesh`, when it has one. */
export function sourceEdges(mesh: THREE.Mesh, nSource: number): THREE.LineSegments | null {
    const edges = mesh.getObjectByName(EDGE_CHILD);
    if (!(edges instanceof THREE.LineSegments)) return null;
    const pos = (edges.geometry as THREE.BufferGeometry).getAttribute("position");
    return pos && pos.count === nSource ? edges : null;
}

export class FrameRecorder {
    readonly capacity: number;
    private readonly records = new Map<number, FrameRecord>();
    private readonly baseBudget: number;
    private disposed = false;

    constructor(
        readonly key: string,
        readonly backend: FrameDisplacementBackend,
        readonly nSource: number,
        readonly nRender: number,
        readonly beamVerts: number,
        nFrames: number,
        maxFrames: number,
        budgetBytes: number = FEA_STEP_CACHE.budgetBytes,
    ) {
        const perFrame = backend.displacementBytes(nSource) + nRender * 3 + beamVerts * 15;
        this.capacity = Math.max(
            0,
            Math.min(nFrames, maxFrames, Math.floor((budgetBytes * BUDGET_SHARE) / Math.max(1, perFrame))),
        );
        if (this.capacity > 0) backend.allocate(nSource, this.capacity);
        // What the recording reserves comes out of the step cache's budget.
        this.baseBudget = FEA_STEP_CACHE.budgetBytes;
        FEA_STEP_CACHE.setBudget(Math.max(0, this.baseBudget - this.capacity * perFrame));
    }

    get size(): number {
        return this.records.size;
    }

    has(step: number): boolean {
        return this.records.has(step);
    }

    /**
     * Keep the frame just painted the ordinary way: `sourceDisplacement` is the warp
     * per source vertex (xyz); the rest is read off the meshes as painted. False when
     * the recording is full or the meshes are not the ones it was made for.
     */
    record(step: number, mesh: THREE.Mesh, sourceDisplacement: Float32Array, beam: THREE.Mesh | null): boolean {
        if (this.disposed || this.records.has(step) || this.records.size >= this.capacity) return false;
        if (sourceDisplacement.length !== this.nSource * 3) return false;
        const color = mesh.geometry.getAttribute("color");
        if (!color || color.itemSize !== 3 || color.count !== this.nRender) return false;

        const slot = this.records.size;
        this.backend.store(slot, sourceDisplacement);

        const colors = new Uint8Array(this.nRender * 3);
        toBytes(color.array as ArrayLike<number>, colors);
        let beamColors: Uint8Array | null = null;
        let beamMorph: Float32Array | null = null;
        if (beam) {
            const bc = beam.geometry.getAttribute("color");
            if (bc && bc.itemSize === 3) {
                beamColors = new Uint8Array(bc.count * 3);
                toBytes(bc.array as ArrayLike<number>, beamColors);
            }
            const bm = beam.geometry.morphAttributes.position?.[0];
            if (bm) beamMorph = Float32Array.from(bm.array as ArrayLike<number>);
        }
        // Only lines this paint drew: the full set outlives a nodal repaint that
        // cleared them (it is what an isolation is rebuilt from).
        const lines = hasResultLineSegments(mesh)
            ? (mesh.userData.__feaLineFull as LineData | undefined) ?? null
            : null;
        this.records.set(step, {slot, colors, beamColors, beamMorph, lines});
        return true;
    }

    /** Show recorded frame `step`. `influence` is the morph influence the ordinary
     *  paint would install (slider position times warp scale). False when it is not
     *  recorded or the meshes changed under the recording. */
    show(step: number, mesh: THREE.Mesh, beam: THREE.Mesh | null, influence: number): boolean {
        const record = this.records.get(step);
        if (this.disposed || !record) return false;
        const color = mesh.geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
        if (!color || color.count !== this.nRender || !(color.array instanceof Float32Array)) return false;

        fromBytes(record.colors, color.array);
        color.needsUpdate = true;
        if (mesh.morphTargetInfluences) mesh.morphTargetInfluences[0] = influence;

        if (beam && record.beamColors) {
            const floats = new Float32Array(record.beamColors.length);
            fromBytes(record.beamColors, floats);
            writeColorAttribute(beam.geometry, floats, null);
        }
        if (beam && record.beamMorph) writeMorphTarget(beam.geometry, record.beamMorph, null);

        if (record.lines) {
            const {positions, colors, displacement, segmentIds} = record.lines;
            installResultLineSegments(mesh, positions, colors, displacement, segmentIds);
        } else {
            clearResultLineSegments(mesh);
        }

        this.backend.present(record.slot, mesh, sourceEdges(mesh, this.nSource));
        return true;
    }

    /** Hand the meshes back to their ordinarily painted state. */
    release(): void {
        this.backend.release();
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.backend.release();
        this.backend.dispose();
        this.records.clear();
        FEA_STEP_CACHE.setBudget(this.baseBudget);
    }
}
