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
// elements; the undeformed outline; load combinations computed on request. An element's colour
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
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import {fetchMeshElements} from "@/services/feaMeshElements";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {usePerfStore} from "@/state/perfStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {readGlbMesh, type GlbMesh} from "@/utils/glb/glbMesh";
import {positionsDiagonal, type ResultMeshLayer, type ViewportResults} from "@/viewport/api/resultLayer";
import {activeViewport, notifyModelLoaded} from "@/viewport/api/viewportApi";
import {nodalScalars} from "../beamSolidNodalColors";
import {hasLazyCases, legendScopeFor, mergeCaseSteps, nextCaseToPrefetch, slotCount, slotRef} from "../caseSteps";
import {FEA_BEAM_EDGE_COLOR, FEA_EDGE_COLOR, FEA_UNDEFORMED_COLOR} from "../edgeColors";
import {withoutEdges} from "../edgeSplit";
import {elementLocalVertices, expandTriplesInto, paintElemField, shownElemFieldRange} from "../elemFieldPaint";
import {paintNodalField} from "../nodalFieldPaint";
import {isPropertyField} from "../propertyColors";
import {RangeAccumulator} from "../rangeAccumulator";
import {recordLayerValues} from "../visibleValues";
import {PaintedPointsLayer, ResultGroup} from "./layerGroup";
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
    /** The element edges as index pairs into the nodes, kept for the undeformed outline. */
    edgeIndices: Uint32Array | null;
    /** 1 for each node a drawn face uses, or null when every node is drawn. */
    drawn: Uint8Array | null;
    /** Everything the result is made of on the viewport: what the controls and the playback
     *  driver scale. */
    group: ResultGroup;
    faces: ResultMeshLayer;
    /** The node each vertex of the faces is, once an element field made them element-local;
     *  null while the faces are in node numbering. */
    renderToSource: Uint32Array | null;
    /** Element-local faces are drawn in order: 0, 1, 2, ... */
    localIndices: Uint32Array | null;
    edges: ResultMeshLayer[];
    markers: PaintedPointsLayer;
    lines: PaintedPointsLayer;
    /** The undeformed outline, while it is asked for. */
    ghost: ResultMeshLayer | null;
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

/** An sRGB hex colour (0xrrggbb) as linear RGB in 0..1, which is what a layer takes. */
function linear(hex: number): [number, number, number] {
    const channel = (c: number) => {
        const v = c / 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return [channel((hex >> 16) & 255), channel((hex >> 8) & 255), channel(hex & 255)];
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

/** Each element's range of the index list, from the bake's elements sidecar: what an element
 *  field is painted by. Best-effort: without it nodal fields still draw. */
async function elementRanges(fetcher: FeaFetcher, manifest: FeaManifest): Promise<Map<string, [number, number]> | null> {
    if (!manifest.mesh.elements_url) return null;
    try {
        const entries = await fetchMeshElements(fetcher, manifest.mesh.elements_url);
        const ranges = new Map<string, [number, number]>();
        // The sidecar counts triangles; the index list has three corners for each.
        for (const e of entries) if (e.triCount > 0) ranges.set(`E${e.label}`, [e.triStart * 3, e.triCount * 3]);
        return ranges;
    } catch (err) {
        console.warn("[fea-streaming] failed to load mesh elements:", err);
        return null;
    }
}

/** The faces of the mesh as a layer, in node numbering or element-local. */
function makeFaces(s: Pick<LayerSession, "results" | "mesh">, local: {positions: Float32Array; indices: Uint32Array} | null, replaceModel: boolean): ResultMeshLayer {
    const {mesh} = s;
    return s.results.createResultMesh(
        {
            positions: local ? local.positions : mesh.positions,
            indices: local ? local.indices : mesh.indices,
            topology: mesh.topology,
            matrix: mesh.matrix,
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
    s.group.drop(s.faces);
    s.faces = s.group.add(makeFaces(s, {positions, indices}, false));
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
    for (const l of s.edges) l.setDisplacement(source);
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
    // A line element's coloured line stands in for its grey edge, and yields with the colours.
    const colouredLines = s.lines.drawn && view.resultColorsVisible;
    s.lines.setShown(colouredLines);
    s.markers.setShown(view.resultColorsVisible && view.resultPointMarkersVisible);
    const [shellEdges, beamEdges] = s.edges;
    if (s.mesh.topology === "triangles") {
        s.group.setShown(shellEdges ?? null, view.elementEdgesVisible);
        s.group.setShown(beamEdges ?? null, view.elementEdgesVisible && !colouredLines);
    } else {
        // A mesh of line elements only: its edges are the members themselves.
        for (const l of s.edges) s.group.setShown(l, view.elementEdgesVisible && !colouredLines);
    }
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

async function openLayerSession(args: {
    results: ViewportResults;
    fetcher: FeaFetcher;
    sourceName: string;
    manifest: FeaManifest;
    stage: (label: string, progress: number) => void;
    throwIfAborted: () => void;
}): Promise<LayerSession> {
    const {results, fetcher, sourceName, manifest, stage, throwIfAborted} = args;
    stage("loading mesh", 0.05);
    throwIfAborted();
    const mesh = readGlbMesh(new Uint8Array(await fetcher(manifest.mesh.url)));
    const [edges, drawRanges] = await Promise.all([elementEdges(fetcher, manifest), elementRanges(fetcher, manifest)]);
    throwIfAborted();
    stage("loading mesh", 0.35);

    closeLayerSession();
    const group = new ResultGroup();
    const faces = group.add(makeFaces({results, mesh}, null, true));
    group.faces = faces;
    const edgeLayers: ResultMeshLayer[] = [];
    const lines = (indices: Uint32Array | null, color: number) => {
        if (!indices) return;
        edgeLayers.push(group.add(results.createResultMesh({positions: mesh.positions, indices, topology: "lines", matrix: mesh.matrix, color: linear(color)})));
    };
    lines(edges.shell, FEA_EDGE_COLOR);
    lines(edges.beam, FEA_BEAM_EDGE_COLOR);
    // The panels list the viewport's model: this one has no objects of its own.
    notifyModelLoaded({model: `fea-result:${sourceName}`, sourceName, sceneExtras: ""});

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
        drawRanges,
        edgeIndices: edges.shell && edges.beam ? concat(edges.shell, edges.beam) : (edges.shell ?? edges.beam),
        drawn,
        group,
        faces,
        renderToSource: null,
        localIndices: null,
        edges: edgeLayers,
        markers: points("points", MARKER_SIZE_PX),
        lines: points("lines", RESULT_LINE_WIDTH_PX),
        ghost: null,
        displacement: new Float32Array(nPoints * 3),
        colors: new Float32Array(nPoints * 3),
        renderDisplacement: null,
        renderColors: null,
        identity: null,
        unsubscribe: () => {},
    };
    group.onDisplacement = (xyz) => applyDisplacement(opened, xyz);
    // The view switches apply to the layers as they change.
    applyView(opened);
    opened.unsubscribe = useFeaAnimationStore.subscribe((now, before) => {
        if (
            now.resultColorsVisible !== before.resultColorsVisible || now.elementEdgesVisible !== before.elementEdgesVisible ||
            now.resultPointMarkersVisible !== before.resultPointMarkersVisible || now.showUndeformed !== before.showUndeformed
        ) applyView(opened);
    });
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
        ? shownElemFieldRange({field, perTypeStepValues, layer, ipReduction, reduction, isShown: () => true})
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
    active.markers.show(painted.markers);
    active.lines.show(painted.lines);
    recordLayerValues(painted.values);
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
    opts: {reload?: LoadFeaStreaming; fea?: FeaCapability} = {},
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
        active = await openLayerSession({results, fetcher, sourceName, manifest, stage, throwIfAborted});
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
                const drawn = active.drawn;
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
            // A nodal field has no result points and paints no line element.
            active.markers.clear();
            active.lines.clear();
            recordLayerValues(null);
        }
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
        // Frames are fetched as they are played here; there is nothing to load ahead.
        store.setStartFramePreload(null);
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
