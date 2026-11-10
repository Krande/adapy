// Apply an element field (AFEL) to the mesh as per-element vertex
// colours. Element fields don't have a per-node value — they live on
// integration points inside each element — so the colour at every
// vertex of an element is the same scalar after our reduction stack
// collapses the (n_ips, n_components) block to a single number.
//
// Pipeline per element:
//   1. Layer filter           — pick the IP rows matching ``layer``
//                                ("top"/"bottom"/"mid"/"all") out of
//                                the bucket's ``ip_layout``. Solid
//                                elements with no layer metadata fall
//                                through to "all".
//   2. IP reduction           — collapse the filtered IPs to one
//                                value per component. Choices follow
//                                the bake's default_view.ip_reduction.
//   3. Component reduction    — "magnitude" (Euclidean norm of the
//                                first 3 components — same as the
//                                nodal path) or pick a single
//                                component out of ``field.components``.
//   4. Colormap sample        — normalise against the field's
//                                rolled-up scalar_range and sample
//                                the active colormap.
//   5. Vertex write           — every vertex inside the element's
//                                AFEM draw range gets that RGB.
//
// Vertices not covered by any element (e.g. orphan nodes on
// line-only meshes, or future cases where AFEM doesn't cover a
// vertex) keep the seed grey so the mesh doesn't show black holes.
//
// Warp is decoupled: callers pass an optional ``warpField`` /
// ``warpStepValues`` exactly like ``applyFieldToMesh``. The warp
// source is always nodal in current solvers (displacement field is
// nodal), so the warp path is identical to the AFBL one.
//
// The numbers are computed by ``paintElemField`` (elemFieldPaint.ts: plain
// typed arrays, shared with the three.js-free viewport); this module writes
// them where three.js draws from.

import * as THREE from "three";

import type {FeaManifestField} from "@/services/viewerApi";
import type {ContourSettings} from "./contourScale";
import {recordValueByRange} from "./visibleValues";
import {isPropertyField} from "./propertyColors";
import {
    ensureElementLocalVertices,
    sourceVertexIndices,
} from "./elementLocalGeometry";
import {paintElemField} from "./elemFieldPaint";
import {clearResultPointMarkers, installResultPointMarkers} from "./resultPointMarkers";
import {clearResultLineSegments, installResultLineSegments} from "./resultLineSegments";
import {segmentRangeIds} from "./lineSegmentIds";
import {ensureVertexNormals, resetBasePositions, writeMorphTarget} from "./gpuRefresh";
import {setSourceMorph} from "./sourceMorph";

export {
    elementScalarReader,
    layerIpIndices,
    reduceIps,
    shownElemFieldRange,
    type ElementScalarReader,
} from "./elemFieldPaint";

export interface ApplyElemFieldArgs {
    mesh: THREE.Mesh;
    basePositions: Float32Array;
    /** The element field driving the colour. Must have ``per_type``
     *  populated; the AFBL path runs through ``applyFieldToMesh``. */
    colorField: FeaManifestField;
    /** One ``(n_elements * n_ips * n_components)`` Float32 view per
     *  per_type bucket, in the same order as ``colorField.per_type``.
     *  Caller is responsible for fetching/parsing the AFEL blobs and
     *  picking the right step index out of ``parsed.steps``. */
    perTypeStepValues: Float32Array[];
    /** Layer filter — "top" | "bottom" | "mid" | "all". Buckets with
     *  empty ``ip_layout`` fall through to "all" regardless of this
     *  value (no metadata to filter on). */
    layer: string;
    /** IP reduction — "max_abs" | "mean" | "max" | "min". */
    ipReduction: string;
    /** Component reduction — "magnitude" (vector norm of the first 3
     *  components) or one of ``colorField.components``. */
    reduction: string;
    /** Optional warp source (typically the manifest's displacement
     *  field) — same semantics as ``applyFieldToMesh``. */
    warpField?: FeaManifestField;
    warpStepValues?: Float32Array;
    displacementScale?: number;
    colormap?: string;
    /**
     * How the scale is drawn: discrete bands, and either end of the range pinned.
     *
     * Threaded in rather than read from the store so this kernel stays a pure
     * function of its arguments — the same reason ``colormap`` is a parameter.
     */
    contour?: ContourSettings | null;
    /** The range the scale follows where ``contour`` pins no end -- what is shown
     *  (``shownElemFieldRange``). Omitted: the field's ``scalar_range``. Ignored for a
     *  model property, which keeps its own categorical range. */
    autoRange?: readonly [number, number] | null;
    /**
     * Draw the coloured two-vertex fallback for line elements.
     *
     * Only for a deck that CANNOT show beam solids. Where solids exist the beam
     * carries its result on its own surface, and drawing a coloured line as well
     * puts two renderings of the same beam in the same place: the black
     * element-edge overlay fights the coloured line, and neither is legible.
     */
    lineFallback?: boolean;
    /** Smooth-shade by averaging each vertex's element scalars across
     *  the elements that touch it. ``false`` (default) paints the same
     *  colour onto every vertex of an element (piecewise-constant). */
    nodalAverage?: boolean;
    /** Install the (zero, without ``warpField``) morph delta. ``false`` for a mesh
     *  whose caller installs its own warp right after (the beam solids). */
    writeMorph?: boolean;
}

/** Same shape as ``applyFieldToMesh`` but takes the AFEL render path
 *  instead. Caller resolves the warp source separately (always nodal
 *  in current solvers) and passes it in. */
export function applyElemFieldToMesh(args: ApplyElemFieldArgs): void {
    const {
        mesh,
        basePositions,
        colorField,
        displacementScale = 1,
        nodalAverage: nodalAverageArg = false,
        writeMorph = true,
    } = args;

    clearResultPointMarkers(mesh);
    clearResultLineSegments(mesh);
    if (!colorField.per_type) {
        throw new Error(
            `applyElemFieldToMesh: field ${colorField.name_canonical} has no per_type buckets`,
        );
    }
    // A model property is never averaged across element boundaries (see the kernel).
    const nodalAverage = nodalAverageArg && !isPropertyField(colorField);
    const geometry = mesh.geometry;
    const n_points = basePositions.length / 3;
    const renderToSource = nodalAverage
        ? sourceVertexIndices(geometry, n_points)
        : ensureElementLocalVertices(geometry);
    const n_render_points = renderToSource.length;

    // Painted straight into the installed colour buffer when it fits: on an
    // element-local shell model that is 12 MB a frame not allocated and not copied.
    const installedColor = geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
    const paintInPlace = !!installedColor
        && installedColor.itemSize === 3
        && installedColor.array instanceof Float32Array
        && installedColor.array.length === n_render_points * 3;
    const out_colors = paintInPlace
        ? (installedColor!.array as Float32Array)
        : new Float32Array(n_render_points * 3);

    // Pull the AFEM draw ranges off the CustomBatchedMesh so we can
    // map element labels back to vertex spans. ``drawRanges`` keys
    // are ``E${label}`` strings (see load_fea_streaming.installAfemUserData);
    // values are ``[startVertexIdx, countVertexIdx]`` already
    // multiplied by 3 to land in the index buffer's vertex-index units.
    const drawRanges = (mesh as unknown as {
        drawRanges?: Map<string, [number, number]>;
    }).drawRanges;
    if (!drawRanges) {
        throw new Error(
            "applyElemFieldToMesh: mesh has no drawRanges Map; " +
            "AFEM sidecar wiring is required for element-field rendering",
        );
    }
    const indexAttr = geometry.getIndex();
    if (!indexAttr) {
        throw new Error("applyElemFieldToMesh: mesh geometry has no index buffer");
    }

    const painted = paintElemField(
        {
            basePositions,
            renderToSource,
            indices: indexAttr.array as Uint16Array | Uint32Array,
            drawRanges,
            colorField,
            perTypeStepValues: args.perTypeStepValues,
            layer: args.layer,
            ipReduction: args.ipReduction,
            reduction: args.reduction,
            warpField: args.warpField,
            warpStepValues: args.warpStepValues,
            colormap: args.colormap,
            contour: args.contour,
            autoRange: args.autoRange,
            nodalAverage: nodalAverageArg,
            lineFallback: args.lineFallback,
            resultPoints: !mesh.userData.feaBeamSolids,
        },
        out_colors,
    );
    const {sourceDisplacement} = painted;

    // ── Position + morph attribute (warp). Same shape as
    //    applyFieldToMesh: reset to base, install the displacement as
    //    a morph delta, mark the geometry dirty so three.js rebuilds
    //    the morph texture on the next render.
    resetBasePositions(geometry, basePositions, renderToSource);

    // The element-edge wireframe keeps SOURCE numbering: its index buffer was built
    // from the bake's edge sidecar against the original vertices, and it still holds
    // the original position attribute. The parent mesh does not -- an element field
    // expands the geometry to element-local vertices and swaps the buffer -- so the
    // parent's morph is the wrong length for it, and handing it over left the outline
    // sitting undeformed while the faces moved. Nodal fields never expand, which is
    // why this only ever showed up on an element field.
    setSourceMorph(mesh, "fea-element-edges", sourceDisplacement);
    setSourceMorph(mesh, "fea-beam-element-edges", sourceDisplacement);

    installResultPointMarkers(
        mesh,
        painted.markers.positions,
        painted.markers.colors,
        painted.markers.displacement,
    );
    recordValueByRange(mesh, painted.values);
    installResultLineSegments(
        mesh,
        painted.lines.positions,
        painted.lines.colors,
        painted.lines.displacement,
        segmentRangeIds(painted.lineVertexLabels),
    );
    // Re-apply whatever was selected before this repaint: applying a field
    // rebuilds the lines from scratch, so a selection made beforehand would
    // otherwise vanish on the next step or component change.
    const restore = mesh.userData.__feaLineSelection as
        | ((ids: readonly string[]) => void)
        | undefined;
    if (restore) {
        const owner = mesh as unknown as {selectedRanges?: Set<string>};
        const current = Array.from(owner.selectedRanges ?? []);
        if (current.length) restore(current);
    }
    // In place, with the morph texture refreshed rather than the whole geometry
    // disposed. See gpuRefresh.ts.
    if (writeMorph) writeMorphTarget(geometry, sourceDisplacement, renderToSource);

    // Colour attribute install / replace.
    const existingColor = geometry.getAttribute("color");
    if (paintInPlace) {
        installedColor!.needsUpdate = true;
    } else if (existingColor && existingColor.itemSize === 3 && existingColor.array.length === out_colors.length) {
        (existingColor.array as Float32Array).set(out_colors);
        existingColor.needsUpdate = true;
    } else {
        geometry.setAttribute("color", new THREE.BufferAttribute(out_colors, 3));
    }

    if (!mesh.morphTargetInfluences) {
        mesh.morphTargetInfluences = [displacementScale];
    } else {
        mesh.morphTargetInfluences[0] = displacementScale;
    }
    if (!mesh.morphTargetDictionary) {
        mesh.morphTargetDictionary = {displacement: 0};
    }

    const enableShaderFlags = (mat: THREE.Material) => {
        let dirty = false;
        if ("vertexColors" in mat && (mat as unknown as {vertexColors: unknown}).vertexColors !== true) {
            (mat as unknown as {vertexColors: boolean}).vertexColors = true;
            dirty = true;
        }
        if ("morphTargets" in mat && (mat as unknown as {morphTargets: unknown}).morphTargets !== true) {
            (mat as unknown as {morphTargets: boolean}).morphTargets = true;
            dirty = true;
        }
        if (dirty) mat.needsUpdate = true;
    };
    if (Array.isArray(mesh.material)) {
        mesh.material.forEach(enableShaderFlags);
    } else if (mesh.material) {
        enableShaderFlags(mesh.material as THREE.Material);
    }

    ensureVertexNormals(geometry);
}
