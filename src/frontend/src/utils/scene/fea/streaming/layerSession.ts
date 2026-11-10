// FEA streaming: a result drawn through result layers.
//
// Owns: one streamed result on a viewport that is given typed arrays to draw
// (viewport/api/resultLayer.ts) instead of a three.js scene to edit: the mesh
// read from the bake's GLB as plain arrays, a face layer and the element-edge
// layers over it, a nodal field painted as colours and displacement
// (nodalFieldPaint.ts), the session registered with the controls and the legend
// (resultStores.ts), and the step callback. The three.js-free viewport shows
// results through this; the three.js viewport keeps its own loader
// (handlers/load_fea_streaming), which paints with the same kernel and
// registers through the same stores.
//
// Drawn here: nodal and element (integration point) fields on the shown mesh, with warp, steps
// and playback; an element field's result-point markers and the coloured lines of its line
// elements; beam elements as their sections; the undeformed outline; load combinations computed
// on request. The elements are objects of the viewport (picked, selected, hidden) and the rows
// of the Scene tree. An element's colour
// is its own, so the first element field makes the faces element-local (elemFieldPaint.ts) and
// every later paint goes through that numbering.
//
// No three.js in this module or in anything it imports.

import {capabilities} from "@/services/capabilities";
import type {FeaCapability} from "@/services/capabilities/types";
import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {prefetchCase, resolveCase, resolveEnvelope} from "@/services/fea/feaCaseResolver";
import {withFieldDetail} from "@/services/fea/feaFieldDetail";
import {caseManifestView, isCaseRef} from "@/services/fea/feaStepRef";
import {FEA_STEP_CACHE} from "@/services/fea/stepCache";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import {fetchMeshElements} from "@/services/feaMeshElements";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {usePerfStore} from "@/state/perfStore";
import {useVisibilityStore} from "@/state/visibilityStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {readGlbMesh, type GlbMesh} from "@/utils/glb/glbMesh";
import {positionsDiagonal, type ResultMeshLayer, type ResultMeshObjects, type ViewportResults} from "@/viewport/api/resultLayer";
import {activeViewport, notifyModelLoaded} from "@/viewport/api/viewportApi";
import {beamSolidNodalColors, nodalScalars} from "../beamSolidNodalColors";
import {beamSolidDisplacement} from "../beamSolidWarp";
import {hasLazyCases, legendScopeFor, mergeCaseSteps, nextCaseToPrefetch, slotCount, slotRef} from "../caseSteps";
import {FEA_BEAM_EDGE_COLOR, FEA_EDGE_COLOR, FEA_UNDEFORMED_COLOR} from "../edgeColors";
import {withoutEdges} from "../edgeSplit";
import {elementLocalVertices, expandTriplesInto, paintElemField, shownElemFieldRange} from "../elemFieldPaint";
import {edgeOwners, maskHiddenEdges, type EdgeOwners} from "../feaEdgeFilter";
import type {LabelSource} from "../labelPlacement";
import {paintNodalField} from "../nodalFieldPaint";
import {isPropertyField} from "../propertyColors";
import {RangeAccumulator} from "../rangeAccumulator";
import {isTimeHistory} from "../timeHistory";
import {recordLayerValues} from "../visibleValues";
import {loadBeamSolidData, type BeamSolidData} from "./layerBeamSolids";
import {PaintedPointsLayer, ResultGroup} from "./layerGroup";
import {preloadFrames, prefetchFieldSteps, upcomingSteps} from "./prefetch";
import {syncResultStores} from "./resultStores";
import {requestStep} from "./stepQueue";
import type {ApplyStepOptions, LoadFeaStreaming, LoadFeaStreamingArgs} from "./types";
import {resolveWarpSource} from "./warpSource";

/** Result-point markers and the coloured lines of line elements, in CSS pixels: as the three.js
 *  viewport draws them. */
const MARKER_SIZE_PX = 7;
const RESULT_LINE_WIDTH_PX = 3;
const UNDEFORMED_OPACITY = 0.45;

interface LayerSession {
    sourceName: string;
    manifest: FeaManifest;
    scope: string;
    /** The viewport part the layers were made on: another viewport on screen is another session. */
    results: ViewportResults;
    /** The mesh as the bake wrote it: its nodes, and the index list in node numbering. */
    mesh: GlbMesh;
    /** Undeformed node positions, xyz per node. */
    basePositions: Float32Array;
    /** ``E<label>`` -> ``[start, count]`` in the index list for each element that has triangles;
     *  null when the bake has no elements sidecar. */
    drawRanges: Map<string, [number, number]> | null;
    /** The elements as objects of the viewport: picked, selected and hidden one at a time. Null
     *  when the bake has no elements sidecar. */
    objects: ResultMeshObjects | null;
    /** The ids of the elements hidden on the viewport now. */
    hidden: Set<string>;
    /** The element edges as index pairs into the nodes, kept for the undeformed outline. */
    edgeIndices: Uint32Array | null;
    /** The edges of the elements that have faces, as the bake gave them, and which elements each
     *  borders (worked out the first time elements are hidden). */
    shellEdgeIndices: Uint32Array | null;
    edgeOwners: EdgeOwners | null;
    /** 1 for each node a face uses, or null when every node is drawn. */
    drawn: Uint8Array | null;
    /** The same over the faces of the elements that are not hidden, while some are. */
    shownNodes: Uint8Array | null;
    /** The displacement the layers hold now, xyz per node: what a layer made later starts from. */
    lastDisplacement: Float32Array | null;
    /** Whether the last paint measured its scale over what is drawn. */
    rangeFromShown: boolean;
    /** Everything the result is made of on the viewport: what the controls and the playback
     *  driver scale. */
    group: ResultGroup;
    faces: ResultMeshLayer;
    /** The node each vertex of the faces is, once an element field made them element-local;
     *  null while the faces are in node numbering. */
    renderToSource: Uint32Array | null;
    /** Element-local faces are drawn in order: 0, 1, 2, ... */
    localIndices: Uint32Array | null;
    /** The edges of the elements with faces, and those of the line elements. */
    shellEdges: ResultMeshLayer | null;
    beamEdges: ResultMeshLayer | null;
    markers: PaintedPointsLayer;
    lines: PaintedPointsLayer;
    /** The undeformed outline, while it is asked for. */
    ghost: ResultMeshLayer | null;
    /** The beam sections the bake wrote (null: none), and what draws them once they are asked
     *  for: a layer, element-local like the faces after the first element field painted on it. */
    solids: BeamSolidData | null;
    solidsLayer: ResultMeshLayer | null;
    solidsLocal: {renderToSource: Uint32Array; indices: Uint32Array; colors: Float32Array; displacement: Float32Array} | null;
    /** Whether the beam sections carry the colours of the field shown. */
    solidsPainted: boolean;
    /** Reused across steps, in node numbering: a step allocates nothing. */
    displacement: Float32Array;
    colors: Float32Array;
    /** The same for element-local faces, made with them. */
    renderDisplacement: Float32Array | null;
    renderColors: Float32Array | null;
    /** The identity numbering, for an element field averaged onto faces in node numbering. */
    identity: Uint32Array | null;
    unsubscribe: () => void;
}

let session: LayerSession | null = null;
// The (source, field, component) the last playback frame registered with the controls and the
// legend. While it matches, a playback frame only repaints: nothing they show can have changed
// between two frames of one play, and registering again woke every subscriber on each frame.
let playbackSyncedFor: string | null = null;
// The slot the step callback last asked for: tells a walk through the list (start on the next
// combination) from a jump (start nothing).
let lastAppliedSlot: number | null = null;
// The running "load every frame" job, and what it is loading. A load of anything else stops it.
let preload: {key: string; abort: AbortController} | null = null;

/** An sRGB hex colour (0xrrggbb) as linear RGB in 0..1, which is what a layer takes. */
function linear(hex: number): [number, number, number] {
    const channel = (c: number) => {
        const v = c / 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return [channel((hex >> 16) & 255), channel((hex >> 8) & 255), channel(hex & 255)];
}

const labelSources = new WeakMap<LayerSession, LabelSource>();

/** The result shown through layers as what number labels are placed on (labelPlacement.ts): its
 *  elements, their nodes, and where each node is at the deformation drawn now. Null when no
 *  result is shown this way or it has no elements to label. */
export function layerLabelSource(): LabelSource | null {
    const s = session;
    if (!s || !s.drawRanges || !s.mesh.indices || s.mesh.topology !== "triangles") return null;
    let source = labelSources.get(s);
    if (!source) {
        const base = s.basePositions, m = s.mesh.matrix;
        source = {
            ranges: s.drawRanges,
            index: s.mesh.indices,
            position: (node, out, at) => {
                const i = node * 3;
                if (i + 2 >= base.length) return false;
                const moved = s.lastDisplacement, k = s.group.scale;
                const x = base[i] + (moved ? k * moved[i] : 0);
                const y = base[i + 1] + (moved ? k * moved[i + 1] : 0);
                const z = base[i + 2] + (moved ? k * moved[i + 2] : 0);
                out[at] = m[0] * x + m[4] * y + m[8] * z + m[12];
                out[at + 1] = m[1] * x + m[5] * y + m[9] * z + m[13];
                out[at + 2] = m[2] * x + m[6] * y + m[10] * z + m[14];
                return true;
            },
            isHidden: (key) => s.hidden.has(key),
        };
        labelSources.set(s, source);
    }
    return source;
}

let selectedElements: {token: unknown; ids: ReadonlySet<string> | null} | null = null;

/** The elements of the result shown through layers that are selected on the viewport, by their
 *  keys; null when none is. Read again only when the selection has changed. */
export function layerSelectedElements(): ReadonlySet<string> | null {
    const s = session;
    const viewport = activeViewport();
    if (!s?.objects || !viewport) return null;
    const token = viewport.selectionToken();
    const read = selectedElements;
    if (read && read.token === token) return read.ids;
    const ids = new Set<string>();
    if (viewport.selectionCount() > 0) {
        for (const ref of viewport.selection()) if (ref.model === s.objects.model) ids.add(ref.id);
    }
    const now = {token, ids: ids.size ? ids : null};
    selectedElements = now;
    return now.ids;
}

/** The scope a result shown through layers was loaded from, when `sourceName` is that result. */
export function layerSessionScope(sourceName: string | null | undefined): string | null {
    return session && sourceName && session.sourceName === sourceName ? session.scope : null;
}

/** Is a result shown through layers now. */
export function layerSessionActive(): boolean {
    return session !== null;
}

/** End the session: its layers leave the viewport. Called by the FEA teardown, so it must not
 *  assume one is open. */
export function closeLayerSession(): void {
    const s = session;
    if (!s) return;
    session = null;
    playbackSyncedFor = null;
    lastAppliedSlot = null;
    preload?.abort.abort();
    preload = null;
    recordLayerValues(null);
    s.unsubscribe();
    s.group.remove();
}

/** The bake's element edges as index pairs into the nodes: the shell grid, and the beam members
 *  when the bake says which those are. Best-effort, like the three.js loader's: a missing sidecar
 *  leaves the faces bare. */
async function elementEdges(fetcher: FeaFetcher, manifest: FeaManifest): Promise<{shell: Uint32Array | null; beam: Uint32Array | null}> {
    if (!manifest.mesh.edges_url || usePerfStore.getState().hideElementEdges) return {shell: null, beam: null};
    try {
        const all = await fetchMeshEdges(fetcher, manifest.mesh.edges_url);
        let beam: Uint32Array | null = null;
        if (manifest.mesh.line_edges_url) {
            try {
                const fetched = await fetchMeshEdges(fetcher, manifest.mesh.line_edges_url);
                if (fetched.length > 0) beam = fetched;
            } catch (err) {
                console.warn("[fea-streaming] failed to load line edges:", err);
            }
        }
        const shell = beam ? withoutEdges(all, beam) : all;
        return {shell: shell.length > 0 ? shell : null, beam};
    } catch (err) {
        console.warn("[fea-streaming] failed to load mesh edges:", err);
        return {shell: null, beam: null};
    }
}

/** The key a result's elements are known by as objects of the viewport, and in the Scene tree. */
export function resultModelKey(sourceName: string): string {
    return `fea-result:${sourceName}`;
}

/** The elements of the mesh, from the bake's elements sidecar: each one's range of the index
 *  list (what an element field is painted by), the same as objects of the viewport, and the
 *  hierarchy the Scene tree lists. Best-effort: without it nodal fields still draw, as one mesh. */
async function elementTable(fetcher: FeaFetcher, manifest: FeaManifest, sourceName: string): Promise<{
    drawRanges: Map<string, [number, number]>;
    objects: ResultMeshObjects;
    extras: Record<string, unknown>;
} | null> {
    if (!manifest.mesh.elements_url) return null;
    try {
        const entries = await fetchMeshElements(fetcher, manifest.mesh.elements_url);
        const drawRanges = new Map<string, [number, number]>();
        const ids: string[] = new Array(entries.length);
        // As the three.js viewport's tree of a result: every element under one root.
        const hierarchy: Record<string, [string, string]> = {"fea-root": ["FEA elements", "*"]};
        const ranges: Record<string, [number, number]> = {};
        const drawing: number[] = [];
        for (let i = 0; i < entries.length; i++) {
            const e = entries[i];
            const id = `E${e.label}`;
            ids[i] = id;
            hierarchy[id] = [id, "fea-root"];
            if (e.triCount <= 0) continue;
            // The sidecar counts triangles; the index list has three corners for each.
            const range: [number, number] = [e.triStart * 3, e.triCount * 3];
            drawRanges.set(id, range);
            ranges[id] = range;
            drawing.push(i);
        }
        drawing.sort((x, y) => entries[x].triStart - entries[y].triStart);
        const segments = new Uint32Array(drawing.length * 2);
        drawing.forEach((entry, s) => {
            segments[s * 2] = entries[entry].triStart * 3;
            segments[s * 2 + 1] = entries[entry].triCount * 3;
        });
        return {
            drawRanges,
            objects: {model: resultModelKey(sourceName), sourceName, ids, segments, objectOf: Int32Array.from(drawing)},
            extras: {id_hierarchy: hierarchy, draw_ranges_node0: ranges},
        };
    } catch (err) {
        console.warn("[fea-streaming] failed to load mesh elements:", err);
        return null;
    }
}

/** The faces of the mesh as a layer, in node numbering or element-local. */
function makeFaces(
    s: Pick<LayerSession, "results" | "mesh" | "objects">,
    local: {positions: Float32Array; indices: Uint32Array} | null,
    replaceModel: boolean,
): ResultMeshLayer {
    const {mesh} = s;
    return s.results.createResultMesh(
        {
            positions: local ? local.positions : mesh.positions,
            indices: local ? local.indices : mesh.indices,
            topology: mesh.topology,
            matrix: mesh.matrix,
            // An element's range of the index list is the same in either numbering.
            objects: mesh.topology === "triangles" && mesh.indices ? s.objects ?? undefined : undefined,
        },
        {replaceModel},
    );
}

/** Make the faces element-local, so each element can carry its own colour: once per session,
 *  by the first element field painted. The faces drawn until now are replaced. */
function ensureLocalFaces(s: LayerSession): Uint32Array {
    if (s.renderToSource) return s.renderToSource;
    const {renderToSource, indices} = elementLocalVertices(s.mesh.indices!);
    const positions = new Float32Array(renderToSource.length * 3);
    expandTriplesInto(s.basePositions, renderToSource, positions);
    // The new faces first: the elements stay objects of the viewport throughout, selected and
    // hidden as they were.
    const before = s.faces;
    s.faces = s.group.add(makeFaces(s, {positions, indices}, false));
    s.group.drop(before);
    s.group.faces = s.faces;
    s.renderToSource = renderToSource;
    s.localIndices = indices;
    s.renderDisplacement = new Float32Array(positions.length);
    s.renderColors = new Float32Array(positions.length);
    s.faces.setColorsVisible(useFeaAnimationStore.getState().resultColorsVisible);
    return renderToSource;
}

/** A step's displacement (xyz per node) onto every layer that moves with the nodes. */
function applyDisplacement(s: LayerSession, source: Float32Array): void {
    s.lastDisplacement = source;
    s.shellEdges?.setDisplacement(source);
    s.beamEdges?.setDisplacement(source);
    if (s.renderToSource) {
        expandTriplesInto(source, s.renderToSource, s.renderDisplacement!);
        s.faces.setDisplacement(s.renderDisplacement!);
    } else {
        s.faces.setDisplacement(source);
    }
}

/** A step's colours (rgb per node) onto the faces. */
function applyNodalColors(s: LayerSession, source: Float32Array): void {
    if (s.renderToSource) {
        expandTriplesInto(source, s.renderToSource, s.renderColors!);
        s.faces.setColors(s.renderColors!);
    } else {
        s.faces.setColors(source);
    }
}

/** The view switches a result has, applied to the layers as they are now. */
function applyView(s: LayerSession): void {
    const view = useFeaAnimationStore.getState();
    s.faces.setColorsVisible(view.resultColorsVisible);
    // A beam is drawn one way at a time: its section when asked for, else its coloured line,
    // which stands in for its grey edge and yields with the colours.
    const solids = solidsOn(s);
    s.group.setShown(s.solidsLayer, solids);
    s.solidsLayer?.setColorsVisible(view.resultColorsVisible && s.solidsPainted);
    const colouredLines = s.lines.drawn && view.resultColorsVisible && !solids;
    s.lines.setShown(colouredLines);
    s.markers.setShown(view.resultColorsVisible && view.resultPointMarkersVisible);
    // A mesh of line elements only has no faces: all its edges are the members themselves.
    const members = s.mesh.topology !== "triangles";
    s.group.setShown(s.shellEdges, view.elementEdgesVisible && !(members && colouredLines));
    s.group.setShown(s.beamEdges, view.elementEdgesVisible && !colouredLines);
    // The undeformed outline: the element edges where they were, never displaced.
    if (view.showUndeformed && !s.ghost && s.edgeIndices) {
        s.ghost = s.group.add(s.results.createResultMesh({
            positions: s.basePositions, indices: s.edgeIndices, topology: "lines", matrix: s.mesh.matrix,
            color: linear(FEA_UNDEFORMED_COLOR), opacity: UNDEFORMED_OPACITY,
        }));
    } else if (!view.showUndeformed && s.ghost) {
        s.group.drop(s.ghost);
        s.ghost = null;
    }
}

/** Element edges as a layer of lines over the nodes. */
function edgeLines(s: Pick<LayerSession, "results" | "mesh">, indices: Uint32Array, color: number): ResultMeshLayer {
    return s.results.createResultMesh({positions: s.mesh.positions, indices, topology: "lines", matrix: s.mesh.matrix, color: linear(color)});
}

/** Whether the beam sections are what draws the beams now. */
function solidsOn(s: Pick<LayerSession, "solids">): boolean {
    return !!s.solids && useFeaAnimationStore.getState().beamSolidsVisible;
}

/** 1 for each node that something drawn in colour uses: the faces that are not hidden and, while
 *  the beam sections are on, the end nodes of the beams that are not; null when every node is. */
function drawnNodes(s: LayerSession): Uint8Array | null {
    const {objects, hidden} = s;
    const indices = s.mesh.indices;
    const solids = solidsOn(s) ? s.solids! : null;
    if (!solids?.warp && (!hidden.size || !objects || !indices || s.mesh.topology !== "triangles")) return s.drawn;
    if (!s.shownNodes) {
        const nPoints = s.basePositions.length / 3;
        const shown = new Uint8Array(nPoints);
        if (!hidden.size || !objects || !indices || s.mesh.topology !== "triangles") {
            if (s.drawn) shown.set(s.drawn);
            else shown.fill(1);
        } else {
            for (let seg = 0; seg < objects.objectOf.length; seg++) {
                if (hidden.has(objects.ids[objects.objectOf[seg]])) continue;
                const start = objects.segments[seg * 2], end = start + objects.segments[seg * 2 + 1];
                for (let i = start; i < end; i++) shown[indices[i]] = 1;
            }
        }
        if (solids?.warp) {
            const {node0, node1} = solids.warp;
            for (let beam = 0; beam < solids.labels.length; beam++) {
                if (hidden.size && hidden.has(`E${solids.labels[beam]}`)) continue;
                const start = solids.segments[beam * 2], end = start + solids.segments[beam * 2 + 1];
                for (let i = start; i < end; i++) {
                    const v = solids.indices[i];
                    if (node0[v] < nPoints) shown[node0[v]] = 1;
                    if (node1[v] < nPoints) shown[node1[v]] = 1;
                }
            }
        }
        s.shownNodes = shown;
    }
    return s.shownNodes;
}

/** Is element ``label`` drawn in colour now: by a face that is not hidden, by its beam section
 *  while those are on, or else by its coloured line. */
function elementShown(s: LayerSession): (label: number) => boolean {
    const {hidden, drawRanges} = s;
    const solids = solidsOn(s) ? s.solids!.drawRanges : null;
    if (!hidden.size && !solids) return () => true;
    return (label) => {
        const id = `E${label}`;
        if (hidden.has(id)) return false;
        // With the sections on, a beam without one is drawn by nothing coloured.
        return !solids || !!drawRanges?.has(id) || solids.has(id);
    };
}

/** The layer that draws the beam sections, made the first time they are asked for. Each beam is
 *  the same object of the viewport as its element, so it is picked, selected and hidden as one. */
function ensureSolidsLayer(s: LayerSession): ResultMeshLayer {
    if (s.solidsLayer) return s.solidsLayer;
    s.solidsLayer = s.group.add(makeSolids(s, null));
    return s.solidsLayer;
}

function makeSolids(s: LayerSession, local: {positions: Float32Array; indices: Uint32Array} | null): ResultMeshLayer {
    const solids = s.solids!;
    let objects: ResultMeshObjects | undefined;
    if (s.objects) {
        const at = new Map<string, number>();
        s.objects.ids.forEach((id, i) => at.set(id, i));
        objects = {
            ...s.objects,
            segments: solids.segments,
            objectOf: Int32Array.from(solids.labels, (label) => at.get(`E${label}`) ?? -1),
        };
    }
    return s.results.createResultMesh({
        positions: local ? local.positions : solids.positions,
        indices: local ? local.indices : solids.indices,
        topology: "triangles",
        matrix: s.mesh.matrix,
        objects,
    });
}

/** Make the beam sections element-local, as the faces are for an element field. */
function ensureLocalSolids(s: LayerSession): NonNullable<LayerSession["solidsLocal"]> {
    if (s.solidsLocal) return s.solidsLocal;
    const {renderToSource, indices} = elementLocalVertices(s.solids!.indices);
    const positions = new Float32Array(renderToSource.length * 3);
    expandTriplesInto(s.solids!.positions, renderToSource, positions);
    const before = s.solidsLayer;
    s.solidsLayer = s.group.add(makeSolids(s, {positions, indices}));
    s.group.drop(before);
    s.solidsLocal = {renderToSource, indices, colors: new Float32Array(positions.length), displacement: new Float32Array(positions.length)};
    return s.solidsLocal;
}

/** Move the beam sections with the nodes: each vertex by its beam's two end nodes and their
 *  rotations (beamSolidWarp.ts). Without the bake's node mapping they stay where they are. */
function warpSolids(s: LayerSession, warp: {field: FeaManifestField; stepValues: Float32Array} | null): void {
    const solids = s.solids!;
    const layer = s.solidsLayer;
    if (!layer || !solids.warp) return;
    const displacement = beamSolidDisplacement(solids.warp, solids.positions, warp?.field, warp?.stepValues, s.basePositions);
    if (s.solidsLocal) {
        expandTriplesInto(displacement, s.solidsLocal.renderToSource, s.solidsLocal.displacement);
        layer.setDisplacement(s.solidsLocal.displacement);
    } else {
        layer.setDisplacement(displacement);
    }
}

/** The beam sections were switched on or off: they and the coloured lines swap places, and the
 *  field is painted again, onto the sections and on a scale measured over what is drawn now. */
function toggleSolids(s: LayerSession): void {
    if (!s.solids) return;
    s.shownNodes = null;
    if (solidsOn(s)) ensureSolidsLayer(s);
    applyView(s);
    const {sessionActive, applyStep, stepIndex} = useFeaAnimationStore.getState();
    if (sessionActive && applyStep) {
        applyStep(stepIndex).catch((err) => console.warn("[fea] repaint after switching the beam sections failed", err));
    }
}

const hiddenRefresh = new WeakMap<LayerSession, ReturnType<typeof setTimeout>>();

/** Elements were hidden or shown: once per burst of changes (an isolation hides and shows many
 *  in one go), bring the session in line with what is drawn. */
function scheduleHiddenRefresh(s: LayerSession): void {
    if (hiddenRefresh.has(s)) return;
    hiddenRefresh.set(s, setTimeout(() => {
        hiddenRefresh.delete(s);
        if (session === s) refreshHidden(s);
    }, 0));
}

/** Read which elements are hidden on the viewport. When that changed: an edge is drawn while an
 *  element it borders is, and a scale measured over what is drawn is measured again. */
function refreshHidden(s: LayerSession): void {
    const visibility = activeViewport()?.objectVisibility();
    if (!visibility || !s.objects) return;
    const {model, ids} = s.objects;
    const hidden = new Set<string>();
    for (const id of ids) if (visibility(model, id) === "hidden") hidden.add(id);
    let same = hidden.size === s.hidden.size;
    if (same) for (const id of hidden) if (!s.hidden.has(id)) { same = false; break; }
    if (same) return;
    s.hidden = hidden;
    s.shownNodes = null;

    if (s.shellEdges && s.shellEdgeIndices && s.drawRanges && s.mesh.indices) {
        let indices = s.shellEdgeIndices;
        if (hidden.size) {
            s.edgeOwners ??= edgeOwners(s.drawRanges, s.mesh.indices, s.basePositions.length / 3, s.shellEdgeIndices);
            indices = new Uint32Array(s.shellEdgeIndices.length);
            // An edge all of whose elements are hidden becomes a pair of one node: nothing is drawn.
            maskHiddenEdges(s.shellEdgeIndices, s.edgeOwners, (id) => hidden.has(id), indices);
        }
        const before = s.shellEdges;
        s.shellEdges = s.group.add(edgeLines(s, indices, FEA_EDGE_COLOR));
        s.group.drop(before);
        if (s.lastDisplacement) s.shellEdges.setDisplacement(s.lastDisplacement);
        applyView(s);
    }

    const {sessionActive, applyStep, stepIndex} = useFeaAnimationStore.getState();
    if (s.rangeFromShown && sessionActive && applyStep) {
        applyStep(stepIndex).catch((err) => console.warn("[fea] repaint after a visibility change failed", err));
    }
}

async function openLayerSession(args: {
    results: ViewportResults;
    fetcher: FeaFetcher;
    sourceName: string;
    manifest: FeaManifest;
    stage: (label: string, progress: number) => void;
    throwIfAborted: () => void;
    compute?: Parameters<typeof loadBeamSolidData>[3];
}): Promise<LayerSession> {
    const {results, fetcher, sourceName, manifest, stage, throwIfAborted, compute} = args;
    stage("loading mesh", 0.05);
    throwIfAborted();
    const mesh = readGlbMesh(new Uint8Array(await fetcher(manifest.mesh.url)));
    const [edges, elements, solids] = await Promise.all([
        elementEdges(fetcher, manifest),
        elementTable(fetcher, manifest, sourceName),
        loadBeamSolidData(fetcher, manifest, mesh.positions, compute),
    ]);
    throwIfAborted();
    stage("loading mesh", 0.35);

    closeLayerSession();
    const group = new ResultGroup();
    const objects = elements?.objects ?? null;
    const faces = group.add(makeFaces({results, mesh, objects}, null, true));
    group.faces = faces;
    const lines = (indices: Uint32Array | null, color: number) =>
        indices ? group.add(edgeLines({results, mesh}, indices, color)) : null;
    const shellEdges = lines(edges.shell, FEA_EDGE_COLOR);
    const beamEdges = lines(edges.beam, FEA_BEAM_EDGE_COLOR);
    // The panels list the viewport's model: the Scene tree gets the result's elements, which are
    // the viewport's objects by the same ids.
    notifyModelLoaded({model: resultModelKey(sourceName), sourceName, sceneExtras: "", extras: elements?.extras});

    const nPoints = mesh.positions.length / 3;
    let drawn: Uint8Array | null = null;
    if (mesh.topology === "triangles" && mesh.indices) {
        drawn = new Uint8Array(nPoints);
        for (let i = 0; i < mesh.indices.length; i++) drawn[mesh.indices[i]] = 1;
    }

    const points = (topology: "points" | "lines", sizePx: number) =>
        // White under the painted colours, which a layer draws times its own colour.
        new PaintedPointsLayer(group, (positions) => results.createResultMesh({positions, indices: null, topology, matrix: mesh.matrix, sizePx, color: [1, 1, 1]}));
    const opened: LayerSession = {
        sourceName,
        manifest,
        scope: scopeUrlPart(useScopeStore.getState().current),
        results,
        mesh,
        basePositions: mesh.positions,
        drawRanges: elements?.drawRanges ?? null,
        objects,
        hidden: new Set(),
        edgeIndices: edges.shell && edges.beam ? concat(edges.shell, edges.beam) : (edges.shell ?? edges.beam),
        shellEdgeIndices: edges.shell,
        edgeOwners: null,
        drawn,
        shownNodes: null,
        lastDisplacement: null,
        rangeFromShown: false,
        group,
        faces,
        renderToSource: null,
        localIndices: null,
        shellEdges,
        beamEdges,
        markers: points("points", MARKER_SIZE_PX),
        lines: points("lines", RESULT_LINE_WIDTH_PX),
        ghost: null,
        solids,
        solidsLayer: null,
        solidsLocal: null,
        solidsPainted: false,
        displacement: new Float32Array(nPoints * 3),
        colors: new Float32Array(nPoints * 3),
        renderDisplacement: null,
        renderColors: null,
        identity: null,
        unsubscribe: () => {},
    };
    group.onDisplacement = (xyz) => applyDisplacement(opened, xyz);
    if (solidsOn(opened)) ensureSolidsLayer(opened);
    // The view switches apply to the layers as they change.
    applyView(opened);
    const offView = useFeaAnimationStore.subscribe((now, before) => {
        if (
            now.resultColorsVisible !== before.resultColorsVisible || now.elementEdgesVisible !== before.elementEdgesVisible ||
            now.resultPointMarkersVisible !== before.resultPointMarkersVisible || now.showUndeformed !== before.showUndeformed
        ) applyView(opened);
        if (now.beamSolidsVisible !== before.beamSolidsVisible) toggleSolids(opened);
    });
    // Elements hidden or shown on the viewport: the edges and the scale follow what is drawn.
    const offVisibility = objects ? useVisibilityStore.subscribe(() => scheduleHiddenRefresh(opened)) : () => {};
    opened.unsubscribe = () => {
        offView();
        offVisibility();
    };
    session = opened;
    return opened;
}

function concat(a: Uint32Array, b: Uint32Array): Uint32Array {
    const out = new Uint32Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
}

/** Paint one step of an element field: its values per element-type bucket are fetched, each
 *  element is coloured by its own (or, averaged, each node by its elements'), and the result
 *  points and line elements the field has are drawn beside the faces. Returns the range measured
 *  over what is shown, when asked to measure it. */
async function paintElementField(args: {
    active: LayerSession;
    field: FeaManifestField;
    step: number;
    reduction: string;
    colormap: string;
    warp: {field: FeaManifestField; stepValues: Float32Array} | null;
    rangeFetcher: Parameters<typeof fetchElemFieldStep>[0];
    fetcher: FeaFetcher;
    cacheKey: string;
    rangeFromShown: boolean;
    stillCurrent: () => boolean;
}): Promise<[number, number] | null> {
    const {active, step, reduction, colormap, warp, rangeFetcher, fetcher, cacheKey, rangeFromShown} = args;
    // The buckets' element labels and node indices: inline on an older bake, else the field's
    // detail sidecar, fetched once and cached.
    const field = await withFieldDetail(args.field, fetcher, cacheKey);
    const buckets = field.per_type ?? [];
    const perTypeStepValues = await Promise.all(
        buckets.map((bucket) =>
            fetchElemFieldStep(rangeFetcher, fetcher, bucket, step, cacheKey).catch((err) => {
                throw new Error(
                    `element field ${field.name_canonical} bucket ${bucket.elem_type} step ${step}: ${err instanceof Error ? err.message : String(err)}`,
                );
            }),
        ),
    );
    if (!args.stillCurrent()) return null;
    const view = useFeaAnimationStore.getState();
    const {layer, ipReduction, contour} = view;
    const faces = active.mesh.topology === "triangles" && !!active.mesh.indices;
    if (faces && !active.drawRanges) {
        throw new Error(`${field.name_canonical} is an element field, and this result has no element ranges to paint it by`);
    }
    const autoRange = rangeFromShown
        ? shownElemFieldRange({field, perTypeStepValues, layer, ipReduction, reduction, isShown: elementShown(active)})
        : null;
    // A property is never averaged; an averaged field is painted in whatever numbering the faces
    // have, an element's own colour needs element-local faces.
    const averaged = view.nodalAverage && !isPropertyField(field);
    let renderToSource: Uint32Array;
    if (!faces || averaged) {
        renderToSource = active.renderToSource ?? (active.identity ??= Uint32Array.from({length: active.basePositions.length / 3}, (_, i) => i));
    } else {
        renderToSource = ensureLocalFaces(active);
    }
    const local = renderToSource === active.renderToSource;
    const outColors = local ? active.renderColors! : active.colors;
    const painted = paintElemField(
        {
            basePositions: active.basePositions,
            renderToSource,
            // Element-local faces are drawn in order; a mesh of lines has no element triangles.
            indices: local ? active.localIndices! : faces ? active.mesh.indices! : [],
            drawRanges: faces ? active.drawRanges! : NO_RANGES,
            colorField: field,
            perTypeStepValues,
            layer,
            ipReduction,
            reduction,
            warpField: warp?.field,
            warpStepValues: warp?.stepValues,
            colormap,
            contour,
            autoRange,
            nodalAverage: view.nodalAverage,
        },
        outColors,
    );
    applyDisplacement(active, painted.sourceDisplacement);
    if (faces) active.faces.setColors(outColors);
    // The beam sections carry the same field: each beam's value on its own solid. The elements
    // with faces have no range there, and the beams none on the faces.
    if (solidsOn(active)) {
        const solids = active.solids!;
        ensureSolidsLayer(active);
        const local = ensureLocalSolids(active);
        paintElemField(
            {
                basePositions: solids.positions, renderToSource: local.renderToSource, indices: local.indices,
                drawRanges: solids.drawRanges, colorField: field, perTypeStepValues, layer, ipReduction, reduction,
                colormap, contour, autoRange, lineFallback: false, resultPoints: false,
            },
            local.colors,
        );
        active.solidsLayer!.setColors(local.colors);
        active.solidsPainted = true;
        warpSolids(active, warp);
    }
    active.markers.show(painted.markers);
    active.lines.show(painted.lines);
    recordLayerValues(painted.values, (id) => active.hidden.has(id));
    return autoRange;
}

const NO_RANGES = new Map<string, [number, number]>();
/** One streaming-FEA load, drawn through the result layers of the viewport on screen. The same
 *  contract as the three.js loader's ``load_fea_streaming``: a call for the source already shown
 *  only repaints.
 *
 *  ``reload`` is what the step callback registered here calls for another step: the loader that
 *  chose this one, so a step change goes the way the first load did. ``fea`` is the transport's FEA
 *  capability (the selected one by default; tests pass their own). */
export async function loadFeaOnLayers(
    args: LoadFeaStreamingArgs,
    opts: {reload?: LoadFeaStreaming; fea?: FeaCapability; compute?: Parameters<typeof loadBeamSolidData>[3]} = {},
): Promise<void> {
    const fea = opts.fea ?? capabilities.fea;
    const reload = opts.reload ?? ((next: LoadFeaStreamingArgs) => loadFeaOnLayers(next, opts));
    if (!fea.supports("fetchManifest") || !fea.supports("fieldFetchers")) {
        throw new Error("This transport cannot serve streamed FEA results");
    }
    const results = activeViewport()?.results;
    if (!results) throw new Error("No 3D view to show the result in");
    const {sourceName, manifest, fieldName, stepIndex, reduction, onStage, signal, playback = false} = args;
    const playbackKey = `${sourceName}\u0000${fieldName ?? ""}\u0000${reduction ?? ""}`;
    if (!playback) playbackSyncedFor = null;
    const displacementScale = args.displacementScale ?? 1;
    const colormap = args.colormap ?? useFeaAnimationStore.getState().colormap;
    const stage = (label: string, progress: number) => onStage?.(label, progress);
    const throwIfAborted = () => {
        if (signal?.aborted) throw new DOMException("load_fea_streaming aborted", "AbortError");
    };
    if (!manifest || !Array.isArray(manifest.fields)) {
        throw new Error("load_fea_streaming: manifest is missing or has no fields array");
    }
    const field = fieldName == null ? null : manifest.fields.find((f) => f.name_canonical === fieldName) ?? null;
    // Slots: the field's stored steps, then the load combinations the bake left to compute on
    // request (caseSteps.ts).
    const nSlots = field ? slotCount(manifest, field) : 0;
    if (fieldName != null) {
        if (!field) throw new Error(`field ${fieldName} not found in manifest`);
        if (stepIndex < 0 || stepIndex >= nSlots) throw new Error(`step index ${stepIndex} out of range (0..${nSlots - 1})`);
    }

    const scope = session?.sourceName === sourceName ? session.scope : scopeUrlPart(useScopeStore.getState().current);
    const {fetcher, rangeFetcher, cacheKey} = fea.fieldFetchers(scope, sourceName);

    let active = session;
    if (!active || active.sourceName !== sourceName || active.results !== results) {
        active = await openLayerSession({results, fetcher, sourceName, manifest, stage, throwIfAborted, compute: opts.compute});
    } else if (active.manifest !== manifest) {
        // A later manifest of a running bake: the mesh is the same file.
        active.manifest = manifest;
    }
    const opened = active;
    // A newer load opened another source while this one was fetching: its picture stands.
    const stillCurrent = () => session === opened;

    stage("loading field data", 0.55);
    throwIfAborted();

    // What is painted. A stored slot reads the field's own blobs at its index. A combination is
    // made first (cached, or computed on request), and then the whole manifest is read through
    // the combination's view, so the colour field and the warp source are the same case.
    let paintManifest = manifest;
    let paintField = field;
    let paintStep = stepIndex;
    let isCase = false;
    if (field) {
        const ref = slotRef(manifest, field, stepIndex);
        if (isCaseRef(ref)) {
            stage("materialising load combination", 0.6);
            const resolution = await resolveCase(manifest, {scope, sourceKey: sourceName}, ref.case, {field: field.name_canonical, signal});
            throwIfAborted();
            paintManifest = caseManifestView(manifest, resolution.overlay, resolution.relPrefix);
            paintField = paintManifest.fields.find((f) => f.name_canonical === field.name_canonical) ?? null;
            if (!paintField || paintField === field) throw new Error(`load combination ${ref.case} has no ${field.name_canonical} field`);
            paintStep = 0;
            isCase = true;
        } else {
            paintStep = ref.stored;
        }
    }
    // The range the scale is measured over: the painted field's own, or every combination's when
    // the user asked for that and the server offers one.
    const lazy = hasLazyCases(manifest);
    let envelopeAvailable = false;
    if (paintField && lazy && useFeaAnimationStore.getState().envelopeMode && !isPropertyField(paintField)) {
        const envelope = await resolveEnvelope(manifest, {scope, sourceKey: sourceName}, paintField.name_canonical);
        if (envelope) {
            paintField = {...paintField, scalar_range: {...paintField.scalar_range, ...envelope.scalar_range}};
            envelopeAvailable = true;
        }
    }

    let shownRange: [number, number] | null = null;
    if (field && paintField) {
        const reductionStr = reduction ?? "magnitude";
        const view = useFeaAnimationStore.getState();
        const contour = view.contour;
        // Otherwise the scale describes the picture: this step's values on what is drawn.
        // Playback holds it over every frame instead (the field's own range), so colours mean the
        // same from frame to frame; a scale pinned at both ends and a property's own range are
        // not measured.
        const rangeFromShown =
            !playback && !envelopeAvailable && !isPropertyField(paintField) && !(contour.min !== null && contour.max !== null);
        const warpSource = resolveWarpSource(rangeFetcher, fetcher, cacheKey, paintManifest, paintField, paintStep, view.warpEnabled);
        if (paintField.per_type && paintField.per_type.length > 0) {
            const warp = await warpSource;
            throwIfAborted();
            if (!stillCurrent()) return;
            shownRange = await paintElementField({
                active, field: paintField, step: paintStep, reduction: reductionStr, colormap, warp,
                rangeFetcher, fetcher, cacheKey, rangeFromShown, stillCurrent,
            });
            if (!stillCurrent()) return;
        } else {
            const nPoints = active.basePositions.length / 3;
            const [colorStepValues, warp] = await Promise.all([
                fetchFieldStep(rangeFetcher, fetcher, paintField, paintStep, cacheKey),
                warpSource,
            ]);
            throwIfAborted();
            if (!stillCurrent()) return;
            if (rangeFromShown) {
                const scalars = nodalScalars(paintField, colorStepValues, reductionStr, nPoints);
                const range = new RangeAccumulator();
                const drawn = drawnNodes(active);
                for (let v = 0; v < nPoints; v++) if (!drawn || drawn[v]) range.add(scalars[v]);
                shownRange = range.result();
            }
            paintNodalField(
                {
                    nPoints, colorField: paintField, colorStepValues, reduction: reductionStr,
                    warpField: warp?.field, warpStepValues: warp?.stepValues, colormap, contour, autoRange: shownRange,
                },
                active.displacement,
                active.colors,
            );
            applyDisplacement(active, active.displacement);
            applyNodalColors(active, active.colors);
            if (solidsOn(active)) {
                // A beam section is not made of nodes, but each of its vertices sits between its
                // beam's two: the field is carried onto it the way the displacement is.
                const solids = active.solids!;
                const layer = ensureSolidsLayer(active);
                const colors = solids.warp
                    ? beamSolidNodalColors(paintField, colorStepValues, reductionStr, solids.warp, colormap, nPoints, contour, shownRange)
                    : null;
                if (colors) {
                    if (active.solidsLocal) {
                        expandTriplesInto(colors, active.solidsLocal.renderToSource, active.solidsLocal.colors);
                        layer.setColors(active.solidsLocal.colors);
                    } else {
                        layer.setColors(colors);
                    }
                }
                active.solidsPainted = colors !== null;
                warpSolids(active, warp);
            }
            // A nodal field has no result points and paints no line element.
            active.markers.clear();
            active.lines.clear();
            recordLayerValues(null);
        }
        active.rangeFromShown = rangeFromShown;
        active.group.setInfluence(displacementScale);
        // Which lines stand in for which edges follows from what was just painted.
        applyView(active);
    }

    stage("rendering", 0.9);
    throwIfAborted();
    if (playback && playbackSyncedFor === playbackKey) {
        stage("ready", 1.0);
        return;
    }

    const basePositions = active.basePositions;
    syncResultStores({
        layer: active.group,
        mesh: null,
        modelSize: () => positionsDiagonal(basePositions),
        sourceName,
        manifest,
        field,
        fieldName,
        reduction,
        colormap,
        stepIndex,
        sliderFactor: args.sliderFactor,
        rangeField: paintField,
        nSlots: field ? nSlots : undefined,
        legendScope: legendScopeFor({
            hasCombinations: lazy,
            isCase,
            envelopeMode: useFeaAnimationStore.getState().envelopeMode,
            envelopeAvailable,
            fromShown: shownRange !== null,
        }),
        autoRange: shownRange,
    });

    if (field) {
        const store = useFeaAnimationStore.getState();
        // Playing reads the next frames while this one paints, and a time history can be loaded
        // whole ahead of playing it: both fill the step cache the paint reads from.
        const readAhead = (step: number) =>
            prefetchFieldSteps({
                manifest, field, steps: upcomingSteps(step, nSlots),
                warpEnabled: useFeaAnimationStore.getState().warpEnabled, fetcher, rangeFetcher, cacheKey,
            });
        const preloadKey = `${scope}\u0000${sourceName}\u0000${field.name_canonical}`;
        if (preload && preload.key !== preloadKey) {
            // Another field or source: stop loading the old one's frames.
            preload.abort.abort();
            preload = null;
            store.setFramePreload(null);
        }
        store.setStartFramePreload(
            isTimeHistory(field, manifest) && nSlots > 1
                ? () => {
                    if (preload?.key === preloadKey) return;
                    const abort = new AbortController();
                    preload = {key: preloadKey, abort};
                    const now = useFeaAnimationStore.getState();
                    void preloadFrames({
                        manifest, field, warpEnabled: now.warpEnabled, fetcher, rangeFetcher, cacheKey, nFrames: nSlots,
                        startStep: now.stepIndex, budgetBytes: FEA_STEP_CACHE.budgetBytes, signal: abort.signal,
                        onProgress: (p) => {
                            if (!abort.signal.aborted) useFeaAnimationStore.getState().setFramePreload(p);
                        },
                    }).finally(() => {
                        if (preload?.abort === abort) preload = null;
                    });
                }
                : null,
        );
        store.setApplyStep((newStepIndex: number, stepOpts?: ApplyStepOptions) =>
            requestStep(newStepIndex, async (step) => {
                // Read at call time: the slider and the warp scale the user has set now.
                const {factor, scaleFactor} = useFeaAnimationStore.getState();
                // Walking the list in order (play, stepping one by one): start on the next
                // combination while this one loads. A jump starts nothing nobody asked for.
                const sequential = lastAppliedSlot !== null && step === lastAppliedSlot + 1;
                lastAppliedSlot = step;
                if (sequential) {
                    const next = nextCaseToPrefetch(mergeCaseSteps(manifest, field), step);
                    if (next !== null) prefetchCase(manifest, {scope, sourceKey: sourceName}, next);
                }
                if (stepOpts?.playback) readAhead(step);
                await reload({
                    sourceName,
                    manifest,
                    fieldName,
                    stepIndex: step,
                    reduction,
                    displacementScale: factor * scaleFactor,
                    playback: stepOpts?.playback,
                });
            }),
        );
    }
    if (playback) playbackSyncedFor = playbackKey;
    stage("ready", 1.0);
}
