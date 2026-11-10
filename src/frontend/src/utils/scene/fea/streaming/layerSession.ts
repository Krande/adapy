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
// Drawn here: nodal fields on the shown mesh, with warp, steps and playback.
// Not drawn here yet, and refused with the reason rather than shown wrong:
// element (integration point) fields and load combinations computed on request.
// Beam sections, result points, the undeformed outline and per-element
// selection are the three.js loader's.
//
// No three.js in this module or in anything it imports.

import {capabilities} from "@/services/capabilities";
import type {FeaCapability} from "@/services/capabilities/types";
import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {isCaseRef} from "@/services/fea/feaStepRef";
import {fetchFieldStep} from "@/services/feaFieldBlob";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import type {FeaManifest} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {usePerfStore} from "@/state/perfStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {readGlbMesh} from "@/utils/glb/glbMesh";
import {positionsDiagonal, type ResultMeshLayer, type ViewportResults} from "@/viewport/api/resultLayer";
import {activeViewport, notifyModelLoaded} from "@/viewport/api/viewportApi";
import {nodalScalars} from "../beamSolidNodalColors";
import {legendScopeFor, slotCount, slotRef} from "../caseSteps";
import {FEA_BEAM_EDGE_COLOR, FEA_EDGE_COLOR} from "../edgeColors";
import {withoutEdges} from "../edgeSplit";
import {paintNodalField} from "../nodalFieldPaint";
import {isPropertyField} from "../propertyColors";
import {RangeAccumulator} from "../rangeAccumulator";
import {syncResultStores} from "./resultStores";
import {requestStep} from "./stepQueue";
import type {ApplyStepOptions, LoadFeaStreaming, LoadFeaStreamingArgs} from "./types";
import {resolveWarpSource} from "./warpSource";

interface LayerSession {
    sourceName: string;
    manifest: FeaManifest;
    scope: string;
    /** The viewport part the layers were made on: another viewport on screen is another session. */
    results: ViewportResults;
    /** Undeformed node positions, xyz per node. */
    basePositions: Float32Array;
    /** 1 for each node a drawn face uses, or null when every node is drawn. */
    drawn: Uint8Array | null;
    faces: ResultMeshLayer;
    edges: ResultMeshLayer[];
    /** The faces and the edges as one: what the controls and the playback driver scale. */
    all: ResultMeshLayer;
    /** Reused across steps: a step allocates nothing. */
    displacement: Float32Array;
    colors: Float32Array;
    unsubscribe: () => void;
}

let session: LayerSession | null = null;
// The (source, field, component) the last playback frame registered with the controls and the
// legend. While it matches, a playback frame only repaints: nothing they show can have changed
// between two frames of one play, and registering again woke every subscriber on each frame.
let playbackSyncedFor: string | null = null;

/** An sRGB hex colour (0xrrggbb) as linear RGB in 0..1, which is what a layer takes. */
function linear(hex: number): [number, number, number] {
    const channel = (c: number) => {
        const v = c / 255;
        return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return [channel((hex >> 16) & 255), channel((hex >> 8) & 255), channel(hex & 255)];
}

/** Layers that move as one: a displacement and its scale go to all of them, colours to the first. */
function together(layers: readonly ResultMeshLayer[]): ResultMeshLayer {
    const [first] = layers;
    if (layers.length === 1) return first;
    return {
        vertexCount: first.vertexCount,
        setColors: (rgb) => first.setColors(rgb),
        setDisplacement: (xyz) => {
            for (const l of layers) l.setDisplacement(xyz);
        },
        setInfluence: (influence) => {
            for (const l of layers) l.setInfluence(influence);
        },
        setColorsVisible: (visible) => first.setColorsVisible(visible),
        setVisible: (visible) => {
            for (const l of layers) l.setVisible(visible);
        },
        remove: () => {
            for (const l of layers) l.remove();
        },
    };
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
    s.unsubscribe();
    s.all.remove();
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
    const edges = await elementEdges(fetcher, manifest);
    throwIfAborted();
    stage("loading mesh", 0.35);

    closeLayerSession();
    const faces = results.createResultMesh(
        {positions: mesh.positions, indices: mesh.indices, topology: mesh.topology, matrix: mesh.matrix},
        {replaceModel: true},
    );
    const edgeLayers: ResultMeshLayer[] = [];
    const lines = (indices: Uint32Array | null, color: number) => {
        if (!indices) return;
        edgeLayers.push(results.createResultMesh({positions: mesh.positions, indices, topology: "lines", matrix: mesh.matrix, color: linear(color)}));
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

    // The view switches a result has: they apply to the layers as they change.
    const applyView = () => {
        const s = useFeaAnimationStore.getState();
        faces.setColorsVisible(s.resultColorsVisible);
        for (const l of edgeLayers) l.setVisible(s.elementEdgesVisible);
    };
    applyView();
    const unsubscribe = useFeaAnimationStore.subscribe((s, p) => {
        if (s.resultColorsVisible !== p.resultColorsVisible || s.elementEdgesVisible !== p.elementEdgesVisible) applyView();
    });

    session = {
        sourceName,
        manifest,
        scope: scopeUrlPart(useScopeStore.getState().current),
        results,
        basePositions: mesh.positions,
        drawn,
        faces,
        edges: edgeLayers,
        all: together([faces, ...edgeLayers]),
        displacement: new Float32Array(nPoints * 3),
        colors: new Float32Array(nPoints * 3),
        unsubscribe,
    };
    return session;
}

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
    const nSlots = field ? slotCount(manifest, field) : 0;
    if (fieldName != null) {
        if (!field) throw new Error(`field ${fieldName} not found in manifest`);
        if (stepIndex < 0 || stepIndex >= nSlots) throw new Error(`step index ${stepIndex} out of range (0..${nSlots - 1})`);
        if (field.per_type && field.per_type.length > 0) {
            throw new Error(`${fieldName} is an element field: this viewport draws nodal fields only for now`);
        }
    }
    const ref = field ? slotRef(manifest, field, stepIndex) : null;
    if (ref && isCaseRef(ref)) {
        throw new Error("Load combinations computed on request are not drawn on this viewport yet");
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

    stage("loading field data", 0.55);
    throwIfAborted();

    let shownRange: [number, number] | null = null;
    if (field && ref) {
        const reductionStr = reduction ?? "magnitude";
        const view = useFeaAnimationStore.getState();
        const contour = view.contour;
        const nPoints = active.basePositions.length / 3;
        const [colorStepValues, warp] = await Promise.all([
            fetchFieldStep(rangeFetcher, fetcher, field, ref.stored, cacheKey),
            resolveWarpSource(rangeFetcher, fetcher, cacheKey, manifest, field, ref.stored, view.warpEnabled),
        ]);
        throwIfAborted();
        // A newer load opened another source while this one was fetching: its picture stands.
        if (session !== active) return;
        // The scale describes the picture: this step's values on the nodes drawn. Playback holds
        // it over every frame instead (the field's own range), so colours mean the same from frame
        // to frame; a scale pinned at both ends and a property's own range are not measured.
        if (!playback && !isPropertyField(field) && !(contour.min !== null && contour.max !== null)) {
            const scalars = nodalScalars(field, colorStepValues, reductionStr, nPoints);
            const range = new RangeAccumulator();
            const drawn = active.drawn;
            for (let v = 0; v < nPoints; v++) if (!drawn || drawn[v]) range.add(scalars[v]);
            shownRange = range.result();
        }
        paintNodalField(
            {
                nPoints, colorField: field, colorStepValues, reduction: reductionStr,
                warpField: warp?.field, warpStepValues: warp?.stepValues, colormap, contour, autoRange: shownRange,
            },
            active.displacement,
            active.colors,
        );
        active.all.setDisplacement(active.displacement);
        active.faces.setColors(active.colors);
        active.all.setInfluence(displacementScale);
    }

    stage("rendering", 0.9);
    throwIfAborted();
    if (playback && playbackSyncedFor === playbackKey) {
        stage("ready", 1.0);
        return;
    }

    const basePositions = active.basePositions;
    syncResultStores({
        layer: active.all,
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
        nSlots: field ? nSlots : undefined,
        legendScope: legendScopeFor({
            hasCombinations: false, isCase: false, envelopeMode: false, envelopeAvailable: false, fromShown: shownRange !== null,
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
