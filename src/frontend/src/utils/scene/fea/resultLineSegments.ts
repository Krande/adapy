import * as THREE from "three";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";

import {selectedSegments} from "./lineSegmentIds";

// Beam (line) elements, drawn with their result colour.
//
// The reference postprocessor colours beams whether or not they are drawn as solids, and so do we now.
// Previously this renderer only ran when the bake carried NO beam solids at all,
// so a deck that HAD solids showed black beams the moment the solids were
// switched off — the values were there, the colour was not.
//
// Fat lines, not THREE.Line. LineBasicMaterial's linewidth is silently ignored on
// every WebGL platform we ship to, and a one-pixel line is too thin to read a
// colour off, which is the whole point of colouring it. LineSegments2 draws each
// segment as camera-facing quads, so linewidth is real pixels.
//
// The cost of that: instanced geometry has no morph targets, so deformation is
// applied on the CPU here rather than by the GPU as it is for the meshes. That is
// affordable because it is per BEAM, not per mesh vertex — a few thousand numbers
// on a slider drag — and it is recomputed only when the influence changes.

const RESULT_LINE_SEGMENTS = "__fea_result_line_segments__";
const RESULT_LINE_HIGHLIGHT = "__fea_result_line_highlight__";

/** The selection colour, matched to selectedMaterial. */
const HIGHLIGHT_COLOR = 0x0000ff;

/** Beam lines, in pixels. Deliberately above the element-edge wireframe's
 *  hairline so a coloured beam reads as a member, not as another mesh line. */
const BEAM_LINEWIDTH = 3;

/** The selection, thicker again so it shows against the beam under it. */
const HIGHLIGHT_LINEWIDTH = 5;

function disposeChild(parent: THREE.Object3D, name: string): void {
    const existing = parent.getObjectByName(name) as LineSegments2 | undefined;
    if (!existing) return;
    existing.removeFromParent();
    existing.geometry.dispose();
    const material = existing.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
}

export function clearResultLineSegments(mesh: THREE.Mesh): void {
    disposeChild(mesh, RESULT_LINE_SEGMENTS);
    disposeChild(mesh, RESULT_LINE_HIGHLIGHT);
    delete mesh.userData.__feaLineSelection;
}

/**
 * Drive a fat line's positions from base + influence * displacement.
 *
 * Writes into the interleaved buffer the geometry already owns rather than
 * calling setPositions again, which allocates a fresh one every call. The
 * returned hook no-ops until the influence actually moves.
 */
function morphDriver(
    line: LineSegments2,
    positions: Float32Array,
    displacement: Float32Array,
    source: THREE.Object3D,
): () => void {
    // A COPY of the undeformed positions, and it has to be.
    //
    // LineSegmentsGeometry.setPositions keeps a Float32Array it is handed rather
    // than copying it, so the array the caller built IS the geometry's instance
    // buffer. Reading base from it and then writing the deformed result back into
    // it meant every frame started from the previous frame's answer: the beams
    // walked further away on each pass of the oscillator while the shells, driven
    // by a GPU morph off an untouched base, stayed where they were.
    const base = positions.slice();
    const working = new Float32Array(base.length);
    let applied = Number.NaN;
    return () => {
        const influence = (source as THREE.Mesh).morphTargetInfluences?.[0] ?? 0;
        if (influence === applied) return;
        applied = influence;
        for (let i = 0; i < base.length; i++) {
            working[i] = base[i] + influence * displacement[i];
        }
        const attr = line.geometry.getAttribute("instanceStart") as THREE.InterleavedBufferAttribute;
        if (!attr) return;
        (attr.data.array as Float32Array).set(working);
        attr.data.needsUpdate = true;
        // Bounds follow the deformed line. LineSegments2.raycast rejects on the
        // bounding sphere before it looks at a single segment, and a sphere left
        // around the undeformed beams makes them unclickable exactly when the
        // model is warped far enough that you want to click one.
        line.geometry.computeBoundingBox();
        line.geometry.computeBoundingSphere();
    };
}

function buildLine(
    positions: Float32Array,
    colors: Float32Array | null,
    color: number | undefined,
    linewidth: number,
): LineSegments2 {
    const geometry = new LineSegmentsGeometry();
    geometry.setPositions(positions);
    if (colors) geometry.setColors(colors);
    const material = new LineMaterial({
        color: color ?? 0xffffff,
        linewidth,
        vertexColors: !!colors,
        // Depth ON: a line that draws through the hull looked, on the deck this
        // was built against, exactly like a stray line across the model.
        depthTest: true,
    });
    const line = new LineSegments2(geometry, material);
    line.frustumCulled = false;
    // Layer 1: drawn, never picked. Element picking continues through the parent
    // mesh's draw ranges rather than intercepting raycasts here.
    line.layers.set(1);
    return line;
}

/** Install a result-coloured rendering of the line elements.
 *
 * Positions, colours and displacement carry two duplicated vertices per element,
 * so discontinuous element values never bleed across beam ends. */
export function installResultLineSegments(
    mesh: THREE.Mesh,
    positions: Float32Array,
    colors: Float32Array,
    displacement: Float32Array,
    segmentIds: readonly string[] = [],
): void {
    clearResultLineSegments(mesh);
    if (positions.length === 0) return;

    const segments = buildLine(positions, colors, undefined, BEAM_LINEWIDTH);
    segments.name = RESULT_LINE_SEGMENTS;
    segments.renderOrder = 4;

    const drive = morphDriver(segments, positions, displacement, mesh);
    segments.onBeforeRender = (renderer) => {
        // LineMaterial needs the drawing-buffer size to turn linewidth into
        // pixels; without it the lines render at a nonsense width after a resize.
        renderer.getSize((segments.material as LineMaterial).resolution);
        drive();
    };
    mesh.add(segments);

    // Which element each drawn segment belongs to, carried on the object the
    // raycast returns so a hit can be named without a second lookup table.
    segments.userData.__feaSegmentIds = segmentIds;

    // A generic hook rather than an import from the selection machinery: the mesh
    // this hangs off is a CustomBatchedMesh, which has no business knowing about
    // FEA line rendering, and this keeps the dependency pointing one way.
    if (segmentIds.length) {
        mesh.userData.__feaLineSelection = (selected: readonly string[]) =>
            highlightResultLineSegments(mesh, positions, displacement, segmentIds, selected);
    }
}

/**
 * Draw the selected elements' line segments in the selection colour.
 *
 * A separate object rather than a recolour of the base one: the base geometry's
 * colour attribute is the result field, and overwriting part of it would mean
 * restoring it exactly on deselect.
 */
export function highlightResultLineSegments(
    mesh: THREE.Mesh,
    positions: Float32Array,
    displacement: Float32Array,
    segmentIds: readonly string[],
    selected: readonly string[],
): void {
    disposeChild(mesh, RESULT_LINE_HIGHLIGHT);

    const picked = selectedSegments(segmentIds, selected);
    if (picked.length === 0) return;

    const pos = new Float32Array(picked.length * 6);
    const disp = new Float32Array(picked.length * 6);
    for (let i = 0; i < picked.length; i++) {
        // Two vertices per segment, three floats each.
        const from = picked[i] * 6;
        pos.set(positions.subarray(from, from + 6), i * 6);
        disp.set(displacement.subarray(from, from + 6), i * 6);
    }

    const highlight = buildLine(pos, null, HIGHLIGHT_COLOR, HIGHLIGHT_LINEWIDTH);
    highlight.name = RESULT_LINE_HIGHLIGHT;
    // Above the result lines (4) and the element edges (3).
    highlight.renderOrder = 9;
    const drive = morphDriver(highlight, pos, disp, mesh);
    highlight.onBeforeRender = (renderer) => {
        renderer.getSize((highlight.material as LineMaterial).resolution);
        drive();
    };
    mesh.add(highlight);
}

/**
 * The beam under the cursor, or null.
 *
 * Line elements are the one part of an FE model with nothing to click. Picking
 * goes through the main mesh's draw ranges, a beam contributes no triangles to
 * it, and with the section solids switched off there is simply no surface under
 * the pointer — the beam is drawn, and clicking it does nothing. That is what
 * this closes: the drawn line is what gets picked, so what you can see you can
 * select.
 *
 * Screen-space, via LineSegments2's own raycast, so the clickable width is the
 * width you see plus the caller's threshold rather than a mathematical line
 * nobody can hit.
 */
export function pickResultLineSegment(
    mesh: THREE.Object3D,
    raycaster: THREE.Raycaster,
): {rangeId: string; point: THREE.Vector3; distance: number} | null {
    const line = mesh.getObjectByName(RESULT_LINE_SEGMENTS) as LineSegments2 | undefined;
    if (!line || !line.visible) return null;
    const ids = line.userData.__feaSegmentIds as readonly string[] | undefined;
    if (!ids || ids.length === 0) return null;

    const hits: THREE.Intersection[] = [];
    // The line sits on layer 1 so it stays out of the scene-wide raycast; this
    // one asks it directly, so the layer mask must not be consulted.
    line.raycast(raycaster, hits);
    if (hits.length === 0) return null;
    hits.sort((a, b) => a.distance - b.distance);
    const hit = hits[0];
    const segment = hit.faceIndex ?? -1;
    if (segment < 0 || segment >= ids.length) return null;
    return {rangeId: ids[segment], point: hit.point.clone(), distance: hit.distance};
}

/** Show or hide the coloured beam lines — the counterpart to the beam-solid
 *  toggle, since the two are alternative renderings of the same elements. */
export function setResultLineSegmentsVisible(mesh: THREE.Object3D, visible: boolean): void {
    for (const name of [RESULT_LINE_SEGMENTS, RESULT_LINE_HIGHLIGHT]) {
        const child = mesh.getObjectByName(name);
        if (child) child.visible = visible;
    }
}
