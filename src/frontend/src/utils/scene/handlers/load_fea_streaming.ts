// Streaming-FEA loads: the orchestrator.
//
// Owns: the order of one load -- validate the request and tag its colour
// owner, open the session for a new source and attach its sidecars, resolve
// the warp source, paint the field, link the wireframes, re-apply the view
// preferences, sync the controls and legend, hand the landing to the colour
// owner, and register the step callback. Everything it calls lives in
// fea/streaming/, one module per concern; this file is the sequence and the
// facade. Every export the rest of the viewer imports from here is either
// defined below or re-exported from the package, so no importer had to
// move. Holds no state: the session handle is `feaSession` on the model
// session, resolved once per load.

import {makeViewerApiFetcher} from "@/services/feaFieldBlob";
import {prefetchCase, resolveCase, resolveEnvelope} from "@/services/fea/feaCaseResolver";
import {caseManifestView, isCaseRef} from "@/services/fea/feaStepRef";
import {
    hasLazyCases,
    legendScopeFor,
    mergeCaseSteps,
    nextCaseToPrefetch,
    slotCount,
    slotRef,
} from "../fea/caseSteps";
import {isPropertyField} from "../fea/propertyColors";
import {capabilities} from "@/services/capabilities";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {
    noteFieldSourceLoaded,
    requestingSceneColorOwner,
} from "../fea/modeSceneColor";
import type {FeaSessionHandle} from "@/state/modelSession";
import {feaSession as session, feaSourceScope} from "../fea/streaming/session";
import {openFeaSession} from "../fea/streaming/sessionSetup";
import {linkLineMorphToMesh, resolveWarpSource} from "../fea/streaming/warp";
import {syncResultSession} from "../fea/streaming/legendSync";
import {loadFeaWithDefaults} from "../fea/streaming/loadWithDefaults";
import type {LoadFeaStreamingArgs} from "../fea/streaming/types";
import {paintElemField} from "../fea/streaming/paintElemField";
import {paintNodeField} from "../fea/streaming/paintNodeField";
import {fetchBeamSolidWarpSidecar, tryLoadBeamSolids} from "../fea/streaming/beamSolids";
import {installElementEdges} from "../fea/streaming/elementEdges";
import {refreshUndeformedGhost, setFeaResultColorsVisible, syncFeaOverlayVisibility} from "../fea/streaming/visibility";

export {
    feaSourceScope,
    getActiveFeaMesh,
    getActiveFeaSelectedRangeIds,
    hasBeamSolids,
    setActiveFeaSelectedRangeIds,
} from "../fea/streaming/session";
export {clearActiveFeaStreaming} from "../fea/streaming/teardown";
export {
    feaElementEdgesVisible,
    hasFeaElementEdges,
    refreshUndeformedGhost,
    setBeamSolidsVisible,
    setFeaElementEdgesVisible,
    setFeaResultColorsVisible,
    setFeaUndeformedGhost,
    syncFeaOverlayVisibility,
} from "../fea/streaming/visibility";

// Step changes run one at a time, latest wins. Play, the slider and Stop all ask
// for steps; loads take a network round trip each, so without this an older load
// could finish after a newer one and repaint its frame (Stop during play landed
// back on the in-flight frame, and a second press was needed). Requests arriving
// while a load runs collapse into "the newest one"; the returned promise settles
// once the queue is drained, i.e. when the LAST requested step is on screen.
let pendingStep: {step: number; run: (step: number) => Promise<void>} | null = null;
// The slot the step callback last asked for: tells a walk through the list
// (prefetch the next combination) from a jump (prefetch nothing).
let lastAppliedSlot: number | null = null;
let stepDrain: Promise<void> | null = null;

function requestStep(step: number, run: (step: number) => Promise<void>): Promise<void> {
    pendingStep = {step, run};
    if (!stepDrain) {
        stepDrain = (async () => {
            try {
                while (pendingStep) {
                    const next = pendingStep;
                    pendingStep = null;
                    try {
                        await next.run(next.step);
                    } catch (err) {
                        // A superseded load failing is not the caller's error: carry on
                        // to the newer step. Only the last one's failure surfaces.
                        if (!pendingStep) throw err;
                    }
                }
            } finally {
                stepDrain = null;
            }
        })();
    }
    return stepDrain;
}

/** Load the mesh GLB, fetch the chosen field's blob, and apply the
 * (component, step) selection. Subsequent calls for the same source
 * + field skip the network and just swap the step. */
export async function load_fea_streaming(args: LoadFeaStreamingArgs): Promise<void> {
    // The mesh, field and sidecar blobs are read by key from the bake's
    // _derived/ namespace, which only a transport that bakes and serves FEA
    // manifests can do -- what "REST mode" used to stand in for.
    if (!capabilities.fea.supports("fetchManifest")) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const {sourceName, manifest, fieldName, stepIndex, reduction, onStage, signal} = args;
    // Who this load paints for, taken NOW rather than when it lands: an owning
    // mode entered while the fetch is in flight must not be credited with a
    // field the user picked before it, and a mode's own repaint stays its own
    // if the user leaves before it lands.
    const colorOwner = requestingSceneColorOwner(sourceName);
    const displacementScale = args.displacementScale ?? 1;
    const {sliderFactor} = args;
    const colormap =
        args.colormap ?? useFeaAnimationStore.getState().colormap;
    const stage = (label: string, progress: number) => {
        if (onStage) onStage(label, progress);
    };
    const throwIfAborted = () => {
        if (signal?.aborted) {
            throw new DOMException("load_fea_streaming aborted", "AbortError");
        }
    };

    if (!manifest || !Array.isArray(manifest.fields)) {
        throw new Error(
            "load_fea_streaming: manifest is missing or has no fields array",
        );
    }
    // fieldName == null is the field-less mesh path (design-model FEM): no field to resolve.
    const field =
        fieldName == null ? null : manifest.fields.find((f) => f.name_canonical === fieldName) ?? null;
    // Slots: the field's stored steps, then (bake_version 4) the load
    // combinations the bake left to compute on request. See fea/caseSteps.ts.
    const nSlots = field ? slotCount(manifest, field) : 0;
    if (fieldName != null) {
        if (!field) {
            throw new Error(`field ${fieldName} not found in manifest`);
        }
        if (stepIndex < 0 || stepIndex >= nSlots) {
            throw new Error(
                `step index ${stepIndex} out of range (0..${nSlots - 1})`,
            );
        }
    }

    // The scope the open model came from when this repaints it, the browsing
    // scope for a new source. See feaSourceScope.
    const scope = feaSourceScope(sourceName);
    // One fetcher + cache key for every storage-layer call below. The
    // bake-job storage convention (`_derived/<src>.fea/<filename>`)
    // is encoded in `makeViewerApiFetcher`; downstream helpers stay
    // storage-agnostic so paradoc-embed can plug in its own fetcher
    // that hits paradoc-serve's REST endpoint instead.
    const {fetcher, rangeFetcher, cacheKey} = makeViewerApiFetcher(scope, sourceName);

    // (Re-)load the mesh into the scene if we don't already have it
    // for this source. Switching field-within-source keeps the same
    // mesh; switching source forces a reload.
    let active: FeaSessionHandle | null = session.active;
    if (!active || active.sourceName !== sourceName) {
        active = await openFeaSession({fetcher, sourceName, manifest, stage, throwIfAborted});
        active.scope = scope;
        const mesh = active.mesh;

        // Material flags (vertexColors + morphTargets) are flipped on
        // inside applyFieldToMesh so they cover both the array-typed
        // material that prepareLoadedModel installs on
        // CustomBatchedMesh and the plain-material fallback.

        // Beam-solid mesh — optional, only present in manifests baked
        // from SIF sources with section info. Attached as a child of
        // the main mesh so it inherits the FEA root parent and gets
        // disposed alongside the main mesh on scene swap. Visibility
        // is driven by ``beamSolidsVisible`` in feaAnimationStore —
        // default false so the existing line-only render stays the
        // default and a fresh bake doesn't surprise users with the
        // new solid mesh.
        const beamSolidsVisible = useFeaAnimationStore.getState().beamSolidsVisible;
        const beamSolid = await tryLoadBeamSolids(
            fetcher, sourceName, manifest, beamSolidsVisible, active.basePositions,
        );
        if (beamSolid) {
            mesh.add(beamSolid.mesh);
            active.beamSolidMesh = beamSolid.mesh;
            active.beamSolidBasePositions = beamSolid.basePositions;

            // No element-edge wireframe over the beam solids.
            //
            // There used to be one, drawn from the AFEG sidecar: the perimeter of
            // each extruded section plus the seams between adjacent beam
            // elements. It is the wrong thing to call a mesh line. A beam element
            // IS a line — two nodes and the span between them — and its mesh line
            // should be that line whether or not the section is drawn around it.
            // Outlining the extrusion instead put a rectangle round every section
            // end and read as mesh that the model does not have.
            //
            // Nothing replaces it, because nothing needs to: the main mesh's edge
            // sidecar already carries one edge per line element (see
            // get_mesh_topology — "Line elements contribute edges but no
            // triangles"), so beams keep exactly the mesh line they have with the
            // sections switched off. The bake still writes beam_solids_edges_url;
            // it is simply no longer consumed.

            // The compact artefact expands into its own warp triple — same
            // (node0, node1, t) AFBV carried, computed from the frames and
            // the node positions instead of shipped per vertex. Only a
            // manifest that still points at the GLB has a sidecar to fetch.
            const warp = beamSolid.warp
                ?? await fetchBeamSolidWarpSidecar(fetcher, manifest, beamSolid.basePositions);
            if (warp) active.beamSolidWarp = warp;
        }

        // Element-edge wireframe overlays, from the bake's edge sidecar. The
        // index is kept on the session so the undeformed reference wireframe can
        // be rebuilt without re-fetching.
        const edgeIndices = await installElementEdges(mesh, fetcher, manifest);
        if (edgeIndices) active.edgeIndices = edgeIndices;
    }

    stage("loading field data", 0.55);
    throwIfAborted();

    // What is actually painted. A stored slot reads the base blobs at its own
    // index. A combination slot is materialised first (feaCaseResolver: cached,
    // or computed on request), and then the WHOLE manifest is read through the
    // case's view -- blobs under its case prefix, step 0, its own ranges -- so
    // the colour field and the warp source are the same case. Everything below
    // is unaware of combinations.
    let paintManifest = manifest;
    let paintField = field;
    let paintStep = stepIndex;
    let isCase = false;
    if (field) {
        const ref = slotRef(manifest, field, stepIndex);
        if (isCaseRef(ref)) {
            stage("materialising load combination", 0.6);
            const resolution = await resolveCase(manifest, {scope, sourceKey: sourceName}, ref.case, {
                field: field.name_canonical,
                signal,
            });
            throwIfAborted();
            paintManifest = caseManifestView(manifest, resolution.overlay, resolution.relPrefix);
            paintField = paintManifest.fields.find((f) => f.name_canonical === field.name_canonical) ?? null;
            if (!paintField || paintField === field) {
                throw new Error(`load combination ${ref.case} has no ${field.name_canonical} field`);
            }
            paintStep = 0;
            isCase = true;
        } else {
            paintStep = ref.stored;
        }
    }
    // The range the scale is measured over: the painted field's own (the
    // stored steps, or this case), or every combination's when the user asked
    // for it and the server offers one.
    const lazy = hasLazyCases(manifest);
    let envelopeAvailable = false;
    if (paintField && lazy && useFeaAnimationStore.getState().envelopeMode && !isPropertyField(paintField)) {
        const envelope = await resolveEnvelope(manifest, {scope, sourceKey: sourceName}, paintField.name_canonical);
        if (envelope) {
            paintField = {...paintField, scalar_range: {...paintField.scalar_range, ...envelope.scalar_range}};
            envelopeAvailable = true;
        }
    }
    const legendScope = legendScopeFor({
        hasCombinations: lazy,
        isCase,
        envelopeMode: useFeaAnimationStore.getState().envelopeMode,
        envelopeAvailable,
    });

    // Resolve the warp source. The picked field drives colour
    // regardless; warp depends on category:
    //   * displacement → warp by self (legacy behaviour).
    //   * reaction → never warp (force vectors aren't a deformation).
    //   * stress / strain / other → warp by the manifest's displacement
    //     field when ``warpEnabled`` is on, else stay undeformed.
    // Step index is shared across fields — almost all analyses use a
    // parallel step structure, so step 3 of the stress field aligns
    // with step 3 of the displacement field. If the displacement field
    // has fewer steps (unusual; happens when a user runs a sub-step
    // displacement output), we clamp to its last step and warn.
    // Field-less FEM meshes (no results) skip all result coloring / warp / step handling —
    // they only need geometry + beam-solids (loaded above). Everything below is field work.
    if (field && paintField) {
    const reductionStr = reduction ?? "magnitude"; // field present -> reduction is meaningful
    const warpEnabled = useFeaAnimationStore.getState().warpEnabled;
    // Read once, applied to every surface that carries the field. Splitting the
    // scale between the shells, the beam solids and the beam lines is how a model
    // comes to show three different answers to the same question.
    const contour = useFeaAnimationStore.getState().contour;
    const warpInfo = await resolveWarpSource(
        rangeFetcher,
        fetcher,
        cacheKey,
        paintManifest,
        paintField,
        paintStep,
        warpEnabled,
    );

    if (paintField.per_type && paintField.per_type.length > 0) {
        await paintElemField({
            active, field: paintField, stepIndex: paintStep, reduction: reductionStr, displacementScale, colormap,
            contour, warpInfo, rangeFetcher, fetcher, cacheKey,
        });
    } else {
        await paintNodeField({
            active, field: paintField, stepIndex: paintStep, reduction: reductionStr, displacementScale, colormap,
            contour, warpInfo, rangeFetcher, fetcher, cacheKey,
        });
    }
    } // end if (field)

    stage("rendering", 0.9);
    throwIfAborted();

    // Link the edge overlay's morph state to the mesh's so the
    // wireframe tracks deformation. Idempotent: re-running just
    // re-links, which is fine — the references are stable across
    // step changes.
    linkLineMorphToMesh(active.mesh);
    // Same link for the beam-solid mesh's element-edge wireframe so
    // the seams between adjacent beam elements stay attached to the
    // deformed solid mesh under any morph scale.
    if (active.beamSolidMesh) {
        linkLineMorphToMesh(active.beamSolidMesh);
    }

    // Re-apply the undeformed-wireframe preference. It survives loads and step
    // changes, and the ghost has to be rebuilt after one: the base positions it
    // copies belong to the source that was just loaded.
    refreshUndeformedGhost();

    // Re-apply the view preferences a load resets: which beam rendering shows, and
    // whether element edges are drawn. Both outlive the mesh they were set on.
    {
        const s = useFeaAnimationStore.getState();
        setFeaResultColorsVisible(s.resultColorsVisible);
        syncFeaOverlayVisibility();
    }

    // The animation store and the legend, from what was just painted: the
    // session it drives, the field's range, the derived warp scale.
    syncResultSession({
        mesh: active.mesh, sourceName, manifest, field, fieldName, reduction, colormap, stepIndex, sliderFactor,
        rangeField: paintField, nSlots: field ? nSlots : undefined, legendScope,
    });

    // The colours and legend above assume nobody else owns the scene colouring.
    // A mode that does (capacity, inspect) may be on top of the owner stack: one
    // entered before this model loaded when the page opened straight into it,
    // or one that asked for this load itself. The tag taken at request time
    // says which; a load the mode did not ask for is set aside under it.
    noteFieldSourceLoaded(sourceName, colorOwner);

    // applyStep closure captures the *current* (sourceName, manifest,
    // fieldName, reduction). SimulationControls calls this when the
    // user drags the step slider — the callback re-runs
    // load_fea_streaming with the updated stepIndex. Re-registering
    // on every apply keeps the closure fresh even when the user
    // changes field / reduction via the SimulationControls dropdowns.
    // Colormap intentionally reads from the store at call time
    // (load_fea_streaming pulls it from useFeaAnimationStore when the
    // arg is omitted) so a colormap change between apply and the next
    // step drag still picks up the latest selection without needing
    // to re-register the callback here.
    if (field) {
        useFeaAnimationStore.getState().setApplyStep((newStepIndex: number) =>
            requestStep(newStepIndex, async (step) => {
                // The influence is read at call time like the colormap, and for the
                // same reason: without it a step change repainted at the default of
                // 1 and dropped the slider and the warp scale the user had set.
                const {factor, scaleFactor} = useFeaAnimationStore.getState();
                // Walking the list in order (play, export, stepping one by one):
                // start materialising the next combination while this one loads,
                // so the next frame does not wait on a cold case. A jump to an
                // arbitrary step prefetches nothing -- it would only start jobs
                // nobody asked for.
                const sequential = lastAppliedSlot !== null && step === lastAppliedSlot + 1;
                lastAppliedSlot = step;
                if (sequential) {
                    const next = nextCaseToPrefetch(mergeCaseSteps(manifest, field), step);
                    if (next !== null) prefetchCase(manifest, {scope, sourceKey: sourceName}, next);
                }
                await load_fea_streaming({
                    sourceName,
                    manifest,
                    fieldName,
                    stepIndex: step,
                    reduction,
                    displacementScale: factor * scaleFactor,
                });
            }),
        );
    }

    // Auto-show the SimulationControls panel on first apply so the
    // user doesn't need to find a hidden toggle for a deformation
    // session they just kicked off. Idempotent — re-applying with a
    // panel already open is a no-op. Field-less FEM meshes have nothing
    // to drive there, so leave the panel as-is.
    const generalAnimStore = useAnimationStore.getState();
    if (field && !generalAnimStore.isControlsVisible) {
        generalAnimStore.setIsControlsVisible(true);
    }

    stage("ready", 1.0);
}

/** Toggle entry point: fetch the manifest, pick sensible defaults
 * (first field, default reduction, step 0, factor 1), and run
 * ``load_fea_streaming``. The user then refines via SimulationControls
 * — they no longer have to step through a modal picker.
 *
 * Returns silently on a manifest with no fields; the storage row
 * stays unchecked-but-toggled which the user can interpret as
 * "nothing renderable in this file". */
export async function load_fea_with_defaults(sourceName: string): Promise<void> {
    return loadFeaWithDefaults(load_fea_streaming, sourceName);
}

