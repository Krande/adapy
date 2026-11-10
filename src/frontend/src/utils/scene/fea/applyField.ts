// Apply a single (field, component, step) selection to a baked
// FEA mesh: installs the displacement as a morph target on the
// geometry so the deformation factor lives in
// ``mesh.morphTargetInfluences[0]`` instead of being baked into
// the position attribute. Vertex colours are still CPU-computed
// per step + reduction since they don't depend on the factor.
//
// The morph-target route is the right primitive here because:
//   * the deformation animation just sweeps the influence uniform —
//     zero CPU per frame regardless of vertex count;
//   * CustomBatchedMesh's selection overlay already syncs with
//     morph deformation via its onBeforeRender hook, so highlighted
//     elements track the deformed shape automatically;
//   * raycasting on morphed geometry is supported by THREE out of
//     the box (CustomBatchedMesh's custom raycast respects it too).

import type * as THREE from "three";

import type {FeaManifestField} from "@/services/viewerApi";
import type {ContourSettings} from "./contourScale";
import {ensureVertexNormals} from "./gpuRefresh";
import {paintNodalField} from "./nodalFieldPaint";
import {clearResultPointMarkers} from "./resultPointMarkers";
import {clearResultLineSegments} from "./resultLineSegments";
import {threeResultLayer} from "./threeResultLayer";

export interface ApplyFieldArgs {
    /** The mesh whose geometry we deform. We need the mesh (not just
     * the geometry) so we can update ``morphTargetInfluences``. */
    mesh: THREE.Mesh;
    /** Original (un-deformed) vertex positions, length 3*n_points.
     * Cached on the mesh so step scrubs don't accumulate displacement
     * onto already-displaced positions. The geometry's position
     * attribute is reset to this snapshot on every apply, so the
     * morph attribute can hold the un-scaled displacement. */
    basePositions: Float32Array;
    /** Field driving the per-vertex colour. */
    colorField: FeaManifestField;
    /** Step values for ``colorField``, length ``n_points *
     * colorField.components.length``. */
    colorStepValues: Float32Array;
    /** "magnitude" or one of ``colorField.components``. */
    reduction: string;
    /** Field driving the morph delta. Omit to leave the geometry
     * undeformed (zero-magnitude morph delta installed so the influence
     * channel still exists for downstream consumers like the line
     * wireframe — they expect morphTargetInfluences[0] to be a number).
     *
     * Decoupling colour and warp lets the picker show stress / strain
     * fields on the *deformed* shape: the user selects the field they
     * want to inspect, the simulation panel chooses the displacement
     * field as the warp source. Reaction fields explicitly pass no
     * warp source so the geometry stays static. */
    warpField?: FeaManifestField;
    /** Step values for ``warpField``, length ``n_points *
     * warpField.components.length``. Only the first 3 components are
     * read (DX, DY, DZ); rotational DOFs and other slots beyond 3 are
     * ignored for warp purposes. */
    warpStepValues?: Float32Array;
    /** Initial deformation factor. Animation drivers update
     * ``mesh.morphTargetInfluences[0]`` directly afterwards; this is
     * just the value the slider was at when the user pressed apply. */
    displacementScale?: number;
    /** Colormap ID — one of the keys in
     * ``utils/scene/fea/colormaps.COLORMAPS``. Falls back to viridis
     * when missing/unknown so a typo in state doesn't render the mesh
     * black. */
    colormap?: string;
    /**
     * How the scale is drawn: discrete bands, and either end of the range pinned.
     *
     * Threaded in rather than read from the store so this kernel stays a pure
     * function of its arguments — the same reason ``colormap`` is a parameter.
     */
    contour?: ContourSettings | null;
    /** The range the scale follows where ``contour`` pins no end -- what is shown
     *  (``shownNodalRange``). Omitted: the field's ``scalar_range``. */
    autoRange?: readonly [number, number] | null;
}

/** Update the geometry's morph target + colour attribute for the
 * chosen step, and seed the mesh's morphTargetInfluences[0] with
 * the requested factor. Idempotent: always derives from
 * basePositions and the step's raw values, so repeated calls just
 * replace the morph delta and colours.
 *
 * The numbers come from ``paintNodalField`` (plain typed arrays, shared
 * with the three.js-free viewport); the mesh's result layer draws them. */
export function applyFieldToMesh(args: ApplyFieldArgs): void {
    const {
        mesh,
        basePositions,
        colorField,
        colorStepValues,
        reduction,
        warpField,
        warpStepValues,
        displacementScale = 1,
        colormap,
        contour,
        autoRange,
    } = args;
    clearResultPointMarkers(mesh);
    clearResultLineSegments(mesh);

    const geometry = mesh.geometry;
    const n_points = basePositions.length / 3;

    // Displacement vector per vertex (un-scaled) and its colour, in source
    // numbering. Per-geometry scratch, reused across repaints: a 255k-node deck
    // played at 12 fps allocated ~15 MB a frame here, and the garbage collector
    // paid for it in the middle of the animation. Nothing keeps a reference to
    // these past this call -- the layer copies out of them.
    const {sourceDisplacement, sourceColors} = scratchFor(geometry, n_points);
    paintNodalField(
        {nPoints: n_points, colorField, colorStepValues, reduction, warpField, warpStepValues, colormap, contour, autoRange},
        sourceDisplacement,
        sourceColors,
    );

    // The layer writes them where three.js draws from: the position attribute
    // reset to the un-deformed base (a no-op when it already holds it), the
    // displacement as the morph delta (additive: position = base + influence *
    // morph, also on the element-edge wireframes), the colours as the colour
    // attribute, and the shader flags that make the material use both.
    const layer = threeResultLayer(mesh, basePositions);
    layer.setDisplacement(sourceDisplacement);
    layer.setColors(sourceColors);
    // Seed the influence. Animation drivers (RAF) update it directly afterwards.
    layer.setInfluence(displacementScale);

    // Normals depend on the deformed shape; recompute against the
    // base positions only — the morph delta blends per-vertex on the
    // GPU and we don't have a cheap way to reflect that in the CPU
    // normals array. For viz that's acceptable: lighting on the
    // morphed shape uses base normals, which is consistent with how
    // GLTF morph clips behave by default.
    // Base positions only change with the position attribute, so neither do
    // these: computed once per geometry, not once per step.
    ensureVertexNormals(geometry);
}

const scratch = new WeakMap<THREE.BufferGeometry, {sourceDisplacement: Float32Array; sourceColors: Float32Array}>();

function scratchFor(
    geometry: THREE.BufferGeometry,
    nPoints: number,
): {sourceDisplacement: Float32Array; sourceColors: Float32Array} {
    let s = scratch.get(geometry);
    if (!s || s.sourceDisplacement.length !== nPoints * 3) {
        s = {sourceDisplacement: new Float32Array(nPoints * 3), sourceColors: new Float32Array(nPoints * 3)};
        scratch.set(geometry, s);
    }
    return s;
}
