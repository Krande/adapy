// FEA streaming: deformation.
//
// Owns: which field drives the morph delta (`resolveWarpSource`), the lerped
// nodal warp on the beam-solid mesh (`installBeamSolidWarp`), and sharing the
// mesh's morph attribute + influences with its wireframe children
// (`linkLineMorphToMesh`). Every function takes the mesh and buffers it works
// on; none reads the session.
//
// Inputs: the manifest + a blob fetcher (for the displacement field's step),
// the meshes, their base positions and the parsed AFBV warp mapping.

import * as THREE from "three";

import type {FeaFetcher, FeaRangeFetcher} from "@/services/fea/feaFetcher";
import type {ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {beamSolidEdges, warpBeamSolidEdges} from "../beamSolidEdges";
import {elementLocalMap} from "../elementLocalGeometry";
import {refreshMorphTexture, resetBasePositions, writeMorphTarget} from "../gpuRefresh";

/** Pick the displacement field from the manifest. Frontend reads
 *  ``category`` set by the bake to find it without re-string-matching
 *  solver-specific names. Returns the first match or null. */
export function findDisplacementField(manifest: FeaManifest): FeaManifestField | null {
    for (const f of manifest.fields) {
        if (f.category === "displacement") return f;
    }
    return null;
}

/** Resolve which field (and which step-values) drives the morph
 *  delta for this apply. The colour field is always the user's pick;
 *  warp is decoupled so stress / strain visualisations can still show
 *  the deformed shape. Returns ``null`` when the geometry should stay
 *  static (reaction fields, warp toggle off + no displacement field
 *  available, or the user picked displacement but warpEnabled is off). */
export async function resolveWarpSource(
    rangeFetcher: FeaRangeFetcher,
    fetcher: FeaFetcher,
    cacheKey: string,
    manifest: FeaManifest,
    colorField: FeaManifestField,
    stepIndex: number,
    warpEnabled: boolean,
): Promise<{field: FeaManifestField; stepValues: Float32Array} | null> {
    // Reaction force fields never drive a deformation — applying them
    // as a morph delta would visualise a force vector as a
    // displacement, which is semantically wrong. Lock off regardless
    // of the toggle.
    if (colorField.category === "reaction") return null;

    // For the displacement field itself, the warp toggle still
    // controls whether the user sees the deformed shape — a user
    // inspecting raw DX values may want them on the un-deformed mesh.
    if (colorField.category === "displacement") {
        if (!warpEnabled) return null;
        const stepValues = await fetchFieldStep(rangeFetcher, fetcher, colorField, stepIndex, cacheKey);
        return {field: colorField, stepValues};
    }

    // Stress / strain / other — warp by the manifest's displacement
    // field when the user has the toggle on.
    if (!warpEnabled) return null;
    const dispField = findDisplacementField(manifest);
    if (!dispField) return null;

    // Step alignment: prefer the same index. If the displacement
    // field has fewer steps (rare — sub-step output), clamp to last.
    let warpStep = stepIndex;
    if (warpStep >= dispField.n_steps) {
        warpStep = dispField.n_steps - 1;
        // eslint-disable-next-line no-console
        console.warn(
            `[fea-streaming] colour-field step ${stepIndex} exceeds displacement-field ` +
            `n_steps=${dispField.n_steps}; clamping warp source to step ${warpStep}`,
        );
    }
    const stepValues = await fetchFieldStep(rangeFetcher, fetcher, dispField, warpStep, cacheKey);
    return {field: dispField, stepValues};
}

export {beamSolidDisplacement, BeamRotationFit} from "../beamSolidWarp";
import {beamSolidDisplacement} from "../beamSolidWarp";


interface WarpInputs {
    warp: ParsedBeamSolidsWarp;
    basePositions: Float32Array;
    warpField: FeaManifestField | undefined;
    warpStepValues: Float32Array | undefined;
    nodePositions: Float32Array | undefined;
    renderToSource: Uint32Array | null;
}

/** A morph attribute as written: which one, at which version. */
interface MorphState {
    attr: THREE.BufferAttribute | THREE.InterleavedBufferAttribute | undefined;
    version: number;
}

/** What the last `installBeamSolidWarp` wrote per beam-solid geometry, and the morph
 *  state it left on the solid and its edge overlay: any other writer moves those. */
const installedWarps = new WeakMap<THREE.BufferGeometry, WarpInputs & {morph: MorphState; edgesMorph: MorphState}>();

function morphState(geometry: THREE.BufferGeometry | undefined): MorphState {
    const attr = geometry?.morphAttributes.position?.[0];
    return {attr, version: (attr as THREE.BufferAttribute | undefined)?.version ?? -1};
}

function sameMorph(a: MorphState, b: MorphState): boolean {
    return a.attr === b.attr && a.version === b.version;
}

function sameWarp(a: WarpInputs, b: WarpInputs): boolean {
    return a.warp === b.warp && a.basePositions === b.basePositions && a.warpField === b.warpField
        && a.warpStepValues === b.warpStepValues && a.nodePositions === b.nodePositions
        && a.renderToSource === b.renderToSource;
}

/** Install the beam-solid mesh's morph delta from a nodal displacement
 *  field (see beamSolidDisplacement; `nodePositions`, the main mesh's
 *  undeformed node positions, turns on the rotation term).
 *
 *  Linked to the main mesh's ``morphTargetInfluences`` so the slider
 *  drives both meshes in lockstep. No-op when the active session
 *  has no beam-solid mesh or no AFBV mapping. */
export function installBeamSolidWarp(
    main: THREE.Object3D & {morphTargetInfluences?: number[]; morphTargetDictionary?: {[key: string]: number}},
    beamSolid: THREE.Mesh,
    basePositions: Float32Array,
    warp: ParsedBeamSolidsWarp,
    warpField: FeaManifestField | undefined,
    warpStepValues: Float32Array | undefined,
    nodePositions?: Float32Array,
): void {
    const nVerts = warp.n_verts;
    const geom = beamSolid.geometry;

    // In the geometry's CURRENT vertex numbering, which is not always the one the
    // warp sidecar is written in. Painting an element field expands this geometry to
    // element-local vertices and swaps its position buffer -- 33,812 vertices become
    // 172,260 -- and the expansion is cached, so it survives a switch back to a nodal
    // field. A morph sized for the original count is silently ignored by three.js,
    // which is why beam solids sat undeformed while everything around them moved.
    // `elementLocalMap` returns the cached map, or null when the geometry was never
    // expanded, in which case every write below is a straight copy.
    const renderToSource = elementLocalMap(geom);
    const renderCount = renderToSource ? renderToSource.length : nVerts;

    const posAttr = geom.getAttribute("position");
    if (posAttr && posAttr.count === renderCount) {
        resetBasePositions(geom, basePositions, renderToSource);
    }
    // A repaint that changes only the colour (another field or component, a beam-solid
    // toggle) keeps the warp: recomputing it and rewriting the morph made three.js
    // rebuild the solid's morph texture, a pass over every render vertex.
    const inputs: WarpInputs = {warp, basePositions, warpField, warpStepValues, nodePositions, renderToSource};
    const last = installedWarps.get(geom);
    if (!last || !sameWarp(last, inputs) || !sameMorph(last.morph, morphState(geom))
        || !sameMorph(last.edgesMorph, morphState(beamSolidEdges(beamSolid)?.geometry))) {
        const displacement = beamSolidDisplacement(warp, basePositions, warpField, warpStepValues, nodePositions);
        // In place, morph texture refreshed without dropping the attribute buffers
        // (see gpuRefresh.ts) -- this ran on every step and re-uploaded the solids.
        writeMorphTarget(geom, displacement, renderToSource);
        // Its element-edge overlay, in source numbering like `displacement`.
        warpBeamSolidEdges(beamSolid, displacement);
        installedWarps.set(geom, {
            ...inputs,
            morph: morphState(geom),
            edgesMorph: morphState(beamSolidEdges(beamSolid)?.geometry),
        });
    }

    // Share the main mesh's influences array so a single write to
    // mesh.morphTargetInfluences[0] (manual drag or RAF sweep)
    // moves both meshes. Same trick the line wireframe overlay uses.
    if (main.morphTargetInfluences) {
        beamSolid.morphTargetInfluences = main.morphTargetInfluences;
        beamSolid.morphTargetDictionary = main.morphTargetDictionary ?? undefined;
    } else if (!beamSolid.morphTargetInfluences) {
        beamSolid.morphTargetInfluences = [0];
        beamSolid.morphTargetDictionary = {displacement: 0};
    }

    // The beam-solid element-edge wireframe: same story as the main mesh's, and
    // the one the user sees as black lines hanging in space. Its index is written
    // against the ORIGINAL beam-solid vertices and it holds the position attribute
    // this geometry had before the element-local expansion swapped one in, so the
    // unexpanded `displacement` is what fits it. Installed BEFORE
    // linkLineMorphToMesh runs, which then leaves it alone precisely because it
    // brought its own.

    // Enable morph targets on every material slot so the GPU
    // actually applies the delta. The PBR material from the GLB
    // defaults to morphTargets=false.
    const enableMorph = (mat: THREE.Material) => {
        if ("morphTargets" in mat && (mat as unknown as {morphTargets: unknown}).morphTargets !== true) {
            (mat as unknown as {morphTargets: boolean}).morphTargets = true;
            mat.needsUpdate = true;
        }
    };
    if (Array.isArray(beamSolid.material)) beamSolid.material.forEach(enableMorph);
    else if (beamSolid.material) enableMorph(beamSolid.material as THREE.Material);

}

/** Wire the LineSegments wireframe child to share morph attributes
 * + influences with the parent mesh, so changing
 * mesh.morphTargetInfluences[0] morphs both. */
export function linkLineMorphToMesh(mesh: THREE.Mesh): void {
    for (const child of mesh.children) {
        if (!(child instanceof THREE.LineSegments)) continue;
        const lineGeom = child.geometry as THREE.BufferGeometry;
        // A child that brought its own morph keeps it. The element-edge overlay
        // SHARES the parent's position buffer, so the parent's per-vertex deltas
        // are exactly what it needs. The result-line renderer does not: it has
        // two vertices per beam in its own buffer and its own deltas to match.
        // Forcing the parent's array onto it hands it deltas of the wrong length
        // read against the wrong vertices, which is why the coloured beams drifted
        // away from the black outline instead of moving with it.
        if (
            lineGeom.morphAttributes.position
            && lineGeom.morphAttributes.position !== mesh.geometry.morphAttributes.position
        ) continue;
        // morphAttributes is per-geometry; sharing the same array of
        // BufferAttributes makes both geometries reference the same
        // morph delta data on the GPU.
        if (mesh.geometry.morphAttributes.position) {
            lineGeom.morphAttributes.position = mesh.geometry.morphAttributes.position;
            lineGeom.morphTargetsRelative = mesh.geometry.morphTargetsRelative;
        }
        // morphTargetInfluences is per-Object3D; sharing the same
        // array reference means writes through mesh.morphTargetInfluences
        // are visible to the line too.
        if (mesh.morphTargetInfluences) {
            child.morphTargetInfluences = mesh.morphTargetInfluences;
            child.morphTargetDictionary = mesh.morphTargetDictionary ?? undefined;
        }
        const mat = child.material as THREE.LineBasicMaterial;
        if (mat && "morphTargets" in mat) {
            (mat as any).morphTargets = true;
            mat.needsUpdate = true;
        }
        // Rebuild lineGeom's morph texture (the parent's morph data changed) and
        // its VAO, keeping every attribute buffer -- the position one is SHARED
        // with the parent, and a full dispose here deleted it for both. See
        // gpuRefresh.ts.
        refreshMorphTexture(lineGeom);
    }
}
