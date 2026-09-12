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
import {expandSourceTriples, sourceVertexIndices} from "../elementLocalGeometry";
import {translationOffsets, warpValue} from "../warpComponents";

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

/** Install the beam-solid mesh's morph delta from a nodal
 *  displacement field. Per vertex:
 *
 *    delta_v = lerp(disp[node0], disp[node1], t) × (only first 3 components)
 *
 *  Linked to the main mesh's ``morphTargetInfluences`` so the slider
 *  drives both meshes in lockstep. No-op when the active session
 *  has no beam-solid mesh or no AFBV mapping. */
export function installBeamSolidWarp(
    main: THREE.Mesh,
    beamSolid: THREE.Mesh,
    basePositions: Float32Array,
    warp: ParsedBeamSolidsWarp,
    warpField: FeaManifestField | undefined,
    warpStepValues: Float32Array | undefined,
): void {
    const nVerts = warp.n_verts;
    const displacement = new Float32Array(nVerts * 3);

    if (warpField && warpStepValues) {
        const nc = warpField.components.length;
        // WHICH slots hold the translation. A Sesam displacement field is
        // ["ALL","X","Y","Z","RX","RY","RZ"] -- reading slots 0..2 warps every
        // vertex by (ALL, X, Y), and since `ALL` is a non-negative aggregate the
        // beams visibly fly off. See translationOffsets.
        const axes = translationOffsets(warpField);
        const n0 = warp.node0;
        const n1 = warp.node1;
        const ts = warp.t;
        for (let v = 0; v < nVerts; v++) {
            const t = ts[v];
            const a = n0[v] * nc;
            const b = n1[v] * nc;
            const out = v * 3;
            const ax = warpValue(warpStepValues, a, axes[0]);
            const ay = warpValue(warpStepValues, a, axes[1]);
            const az = warpValue(warpStepValues, a, axes[2]);
            const bx = warpValue(warpStepValues, b, axes[0]);
            const by = warpValue(warpStepValues, b, axes[1]);
            const bz = warpValue(warpStepValues, b, axes[2]);
            const omt = 1 - t;
            displacement[out + 0] = omt * ax + t * bx;
            displacement[out + 1] = omt * ay + t * by;
            displacement[out + 2] = omt * az + t * bz;
        }
    }
    // Else: leave displacement at zero — no warp source means no
    // deformation, which is what the user gets when they pick a
    // reaction field or turn warp off.

    const geom = beamSolid.geometry;

    // In the geometry's CURRENT vertex numbering, which is not always the one the
    // warp sidecar is written in. Painting an element field expands this geometry to
    // element-local vertices and swaps its position buffer -- 33,812 vertices become
    // 172,260 -- and the expansion is cached, so it survives a switch back to a nodal
    // field. A morph sized for the original count is silently ignored by three.js,
    // which is why beam solids sat undeformed while everything around them moved.
    // `sourceVertexIndices` returns the cached map, or the identity when the geometry
    // was never expanded, so this is a no-op on the untouched case.
    const renderToSource = sourceVertexIndices(geom, nVerts);
    const renderPositions = expandSourceTriples(basePositions, renderToSource);
    const renderDisplacement = expandSourceTriples(displacement, renderToSource);

    const posAttr = geom.getAttribute("position");
    if (posAttr && posAttr.count === renderToSource.length) {
        (posAttr.array as Float32Array).set(renderPositions);
        posAttr.needsUpdate = true;
    }
    geom.morphAttributes.position = [new THREE.BufferAttribute(renderDisplacement, 3)];
    geom.morphTargetsRelative = true;

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

    // Same dispose dance as applyField: drop the cached morph texture
    // so three.js rebuilds it from the fresh BufferAttribute on the
    // next render.
    geom.dispatchEvent({type: "dispose"});
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
        // Mirror the morph-texture rebuild that applyFieldToMesh does
        // for the parent mesh. lineGeom shares the parent's position
        // BufferAttribute, so when applyField dispatched 'dispose' on
        // mesh.geometry, three.js's WebGLAttributes deleted the GPU
        // buffer for that shared position. lineGeom's VAO still
        // references the (now-orphaned) buffer ID, which is why the
        // wireframe vanishes after a step change. Dispatching dispose
        // here rebuilds lineGeom's VAO + morph texture against the
        // freshly-uploaded position buffer on the next render. No-op
        // on the first call (no renderer state yet).
        lineGeom.dispatchEvent({type: "dispose"});
    }
}
