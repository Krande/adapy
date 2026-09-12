import * as THREE from "three";

import {fetchFieldStep, makeViewerApiFetcher} from "@/services/feaFieldBlob";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {capabilities} from "@/services/capabilities";
import {runResultSidecarLoaders} from "@/plugins/sidecarLoaders";
import type {SidecarFetcher} from "@/plugins/registry";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useConversionStore} from "@/state/conversionStore";
import {resolveContourRange} from "../fea/contourScale";
import {selectedResultRange} from "../fea/resultUnits";
import {autoWarpScale} from "../fea/warpScale";
import {
    noteFieldSourceLoaded,
    requestingSceneColorOwner,
} from "../fea/modeSceneColor";
import type {FeaSessionHandle} from "@/state/modelSession";
import {feaSession as session} from "../fea/streaming/session";
import {openFeaSession} from "../fea/streaming/sessionSetup";
import {findDisplacementField, linkLineMorphToMesh, resolveWarpSource} from "../fea/streaming/warp";
import {paintElemField} from "../fea/streaming/paintElemField";
import {paintNodeField} from "../fea/streaming/paintNodeField";
import {fetchBeamSolidWarpSidecar, tryLoadBeamSolids} from "../fea/streaming/beamSolids";
import {installElementEdges} from "../fea/streaming/elementEdges";
import {refreshUndeformedGhost, setFeaResultColorsVisible, syncFeaOverlayVisibility} from "../fea/streaming/visibility";

export {
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

/** Load the mesh GLB, fetch the chosen field's blob, and apply the
 * (component, step) selection. Subsequent calls for the same source
 * + field skip the network and just swap the step. */
export async function load_fea_streaming(args: {
    sourceName: string;
    manifest: FeaManifest;
    /** null = field-less mesh (design-model FEM): load mesh + beam-solids only, no result
     *  coloring / warp / step animation. */
    fieldName: string | null;
    stepIndex: number;
    reduction: string | null;
    displacementScale?: number;
    /**
     * The sweep slider's own position, when the caller is starting a session and
     * wants the slider moved to it.
     *
     * Distinct from ``displacementScale`` on purpose. That one is the MORPH
     * INFLUENCE — the slider position multiplied by the warp-scale knob — and
     * writing it back into the slider was how selecting a component moved the
     * indicator without moving the model: at a warp scale of 0.2 a slider on 0.55
     * sends an influence of 0.11, the slider then read 0.11, and the shape did not
     * change because the influence had not. Compounding, too: the next selection
     * would have sent 0.022.
     *
     * Omit it and the slider is left where the user put it, which is what every
     * re-apply wants — component, step, layer, colormap. Only a fresh load passes
     * it.
     */
    sliderFactor?: number;
    /** Colormap ID — one of the keys in ``COLORMAPS``. Optional so
     * existing call-sites that don't care still work; we fall back to
     * the active store value (and from there to viridis if unset). */
    colormap?: string;
    /** Optional stage reporter so the toast can show mesh-load /
     *  render progress, not just the manifest poll. ``progress`` is
     *  a fraction in [0, 1] over the load_fea_streaming portion of
     *  the flow; the caller is responsible for remapping that into
     *  the wider queue+convert+load progress bar. */
    onStage?: (stage: string, progress: number) => void;
    /** Optional abort signal — checked between async stages so the
     *  user clicking Kill in the toast bails out without waiting for
     *  the in-flight fetch (which doesn't itself accept a signal). */
    signal?: AbortSignal;
}): Promise<void> {
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
    if (fieldName != null) {
        if (!field) {
            throw new Error(`field ${fieldName} not found in manifest`);
        }
        if (stepIndex < 0 || stepIndex >= field.n_steps) {
            throw new Error(
                `step index ${stepIndex} out of range (0..${field.n_steps - 1})`,
            );
        }
    }

    const scope = scopeUrlPart(useScopeStore.getState().current);
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
            fetcher, sourceName, manifest, beamSolidsVisible,
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

            const warp = await fetchBeamSolidWarpSidecar(fetcher, manifest, beamSolid.basePositions);
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
    if (field) {
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
        manifest,
        field,
        stepIndex,
        warpEnabled,
    );

    if (field.per_type && field.per_type.length > 0) {
        await paintElemField({
            active, field, stepIndex, reduction: reductionStr, displacementScale, colormap, contour,
            warpInfo, rangeFetcher, fetcher, cacheKey,
        });
    } else {
        await paintNodeField({
            active, field, stepIndex, reduction: reductionStr, displacementScale, colormap, contour,
            warpInfo, rangeFetcher, fetcher, cacheKey,
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

    // Register the session with the animation store so
    // SimulationControls renders the deformation-scale slider /
    // play / stop instead of the GLTF-clip controls. Range follows
    // the field's analysis_kind: static = [0, 1] (one-directional),
    // eigen = [-1, +1] (mode shape has no inherent sign).
    const animStore = useFeaAnimationStore.getState();
    animStore.setMesh(active.mesh);
    animStore.setSourceName(sourceName);
    animStore.setManifest(manifest);
    if (field) {
        // Results present -> activate the FEA session (SimulationControls: step slider / field
        // selector / warp). Range follows analysis_kind: static = [0, 1], eigen = [-1, +1].
        animStore.setSessionActive(true);
        const range: [number, number] = field.analysis_kind === "eigen" ? [-1, 1] : [0, 1];
        animStore.setRange(range);
        // Only when the caller asked. See ``sliderFactor`` on the argument type:
        // the influence and the slider are different numbers, and equating them
        // moved the indicator on every component change.
        if (sliderFactor !== undefined) animStore.setFactor(sliderFactor);
        animStore.setStepIndex(stepIndex);
        animStore.setNSteps(field.n_steps);
        // A deformation scale the model can be seen at. Derived from the
        // displacement field and the model size, and only ever applied while the
        // user has not set a scale of their own.
        {
            const geom = active.mesh.geometry;
            // Recompute rather than trust a cached box: a stale one from an
            // earlier state made the derived scale wobble between field
            // switches, and a number that changes on its own is worse than a
            // number that is slightly off. Base positions do not change, so
            // this is the same answer every time.
            geom.computeBoundingBox();
            const size = geom.boundingBox
                ? geom.boundingBox.min.distanceTo(geom.boundingBox.max)
                : 0;
            animStore.applyAutoScaleFactor(
                autoWarpScale(findDisplacementField(manifest), size),
            );
            // A fresh load (the caller moved the slider) was painted before the
            // scale above existed, so its influence is the bare slider value. Put
            // the mesh where the controls now say it is -- slider times scale --
            // or the first view of a deck that needed scaling showed it unscaled
            // until something happened to repaint it.
            if (sliderFactor !== undefined && active.mesh.morphTargetInfluences) {
                active.mesh.morphTargetInfluences[0] =
                    sliderFactor * useFeaAnimationStore.getState().scaleFactor;
            }
        }
        animStore.setFieldName(fieldName);
        if (reduction != null) animStore.setReduction(reduction);
        animStore.setColormap(colormap);
        // Through the same resolver the kernels used: a pinned range the legend
        // does not know about is a legend that disagrees with the picture beside
        // it, which is worse than no legend at all.
        const [legendMin, legendMax] = resolveContourRange(
            selectedResultRange(field, reduction ?? "magnitude"),
            useFeaAnimationStore.getState().contour,
        );
        const legendStore = useColorStore.getState();
        legendStore.setMin(legendMin);
        legendStore.setMax(legendMax);
        legendStore.setShowLegend(true);
    } else {
        // Field-less FEM mesh (model only): no results -> NO simulation session, so
        // SimulationControls + the results-only "show in data" action stay hidden. The
        // beam-solids toggle acts on the session's FEA mesh, not on a result session, so
        // it still works from the Scene > FEM panel.
        animStore.setSessionActive(false);
        animStore.setFieldName(null);
        animStore.setNSteps(1);
        animStore.setStepIndex(0);
        useColorStore.getState().setShowLegend(false);
    }

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
        animStore.setApplyStep(async (newStepIndex: number) => {
            // The influence is read at call time like the colormap, and for the
            // same reason: without it a step change repainted at the default of
            // 1 and dropped the slider and the warp scale the user had set.
            const {factor, scaleFactor} = useFeaAnimationStore.getState();
            await load_fea_streaming({
                sourceName,
                manifest,
                fieldName,
                stepIndex: newStepIndex,
                reduction,
                displacementScale: factor * scaleFactor,
            });
        });
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
    if (!capabilities.fea.supports("fetchManifest")) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const scope = scopeUrlPart(useScopeStore.getState().current);

    // Mirror the FEA bake's queue lifecycle into the global
    // conversion store so the bottom-right ConversionProgress toast
    // shows queue + bake progress for SIF / RMED files the same way
    // it does for CAD-GLB conversions. Without this hook the SIF
    // path is silent: feaManifestPoll only fires its onProgress
    // callback, which by default has no consumer. Store key follows
    // serverPipeline.ts's ``${sourceKey}::${target}`` convention so
    // duplicate keys can't collide with a CAD conversion of the same
    // source (different target).
    const convStore = useConversionStore.getState();
    const storeKey = `${sourceName}::fea`;
    const startedAt = Date.now();
    // Seed the entry as ``queued`` immediately so the toast appears
    // for the gap between click and the first server progress event.
    // The 202 response from feaManifest fills in the real jobId on
    // the next tick.
    convStore.setJob(storeKey, {
        sourceKey: storeKey,
        jobId: "",
        derivedKey: "",
        status: "queued",
        progress: 0,
        stage: "queuing fea bake",
        error: null,
        startedAt,
    });

    // AbortController + store subscription so the user clicking Kill
    // in the toast actually stops the manifest poll. ConversionProgress
    // calls clearJob() after the cancel endpoint resolves; that drops
    // the row from the store, our subscriber fires .abort(), and the
    // poll loop's signal.aborted check throws AbortError on the next
    // tick. Without this the poll keeps ticking every 600 ms and the
    // onProgress callback re-inserts the toast row 600 ms after the
    // user dismissed it (the "flash, comes back" UX bug).
    const controller = new AbortController();
    const unsubscribe = useConversionStore.subscribe((state, prev) => {
        if (prev.jobs[storeKey] && !state.jobs[storeKey]) {
            controller.abort();
        }
    });

    // The toast covers three phases: queue+convert (server-side bake,
    // polled by feaManifest) → mesh-load (client fetches GLB + sidecars)
    // → render (apply field, install warp). We map them into one 0..1
    // progress bar so the user sees uninterrupted motion: the manifest
    // poll fills 0..0.55, the load_fea_streaming stages map into
    // 0.55..1.0. Keeping the row alive through all three is what makes
    // the load survive the user dismissing the storage panel — the
    // async chain itself runs to completion regardless of UI mount
    // state, but only this toast tells the user that.
    const MANIFEST_PROGRESS_CEILING = 0.55;

    let manifest: FeaManifest;
    try {
        manifest = await capabilities.fea.fetchManifest(scope, sourceName, {
            signal: controller.signal,
            onProgress: ({jobId, stage, progress, status}) => {
                // Race guard: if the user cleared the row between
                // .abort() and AbortError actually propagating up the
                // poll loop, don't resurrect it.
                if (!useConversionStore.getState().jobs[storeKey]) return;
                convStore.setJob(storeKey, {
                    sourceKey: storeKey,
                    jobId,
                    derivedKey: "",
                    status,
                    progress: progress * MANIFEST_PROGRESS_CEILING,
                    stage,
                    error: null,
                    startedAt,
                });
            },
        });
        if (!manifest) {
            convStore.clearJob(storeKey);
            return;
        }
        // Fire registered plugin result-sidecar loaders once the FEA geometry is
        // loaded — the FEA path does its own scene setup and bypasses
        // setupModelLoader (the CAD/GLB run-point), so without this a plugin's
        // sidecar (e.g. a code-check result next to the FEA manifest) never loads.
        // Core names no plugin; the fetcher is rooted at the same _derived/<src>.fea/
        // dir the mesh + field blobs come from. Best-effort — never breaks the load.
        const fireResultSidecarLoaders = () => {
            try {
                const {fetcher, rangeFetcher} = makeViewerApiFetcher(scope, sourceName);
                const feaPrefix = `_derived/${sourceName.replace(/^\/+/, "")}.fea/`;
                const sidecar: SidecarFetcher = {
                    url: (rel) => capabilities.files.blobUrl(scope, feaPrefix + rel.replace(/^\/+/, "")),
                    json: async (rel) =>
                        JSON.parse(new TextDecoder().decode(new Uint8Array(await fetcher(rel)))),
                    bytes: async (rel, range) =>
                        range ? (await rangeFetcher(rel, range.start, range.end)).buf : fetcher(rel),
                };
                void runResultSidecarLoaders({manifest, fetcher: sidecar, scope, sourceName});
            } catch (err) {
                console.warn("[fea] plugin result-sidecar loaders failed (non-fatal)", err);
            }
        };
        if (!Array.isArray(manifest.fields) || manifest.fields.length === 0) {
            // No result fields — a design-model FEM mesh (.inp/.fem/.med) or a results deck
            // whose nodal output was all filtered out. Load the geometry field-lessly: mesh +
            // beam-solids + selection wiring, no coloring / warp / step animation.
            await load_fea_streaming({
                sourceName,
                manifest,
                fieldName: null,
                stepIndex: 0,
                reduction: null,
                onStage: (stage, progress) => {
                    if (!useConversionStore.getState().jobs[storeKey]) return;
                    const overall =
                        MANIFEST_PROGRESS_CEILING + progress * (1 - MANIFEST_PROGRESS_CEILING);
                    convStore.setJob(storeKey, {
                        sourceKey: storeKey, jobId: "", derivedKey: "", status: "running",
                        progress: overall, stage, error: null, startedAt,
                    });
                },
            });
            convStore.setJob(storeKey, {
                sourceKey: storeKey, jobId: "", derivedKey: "", status: "done",
                progress: 1, stage: "ready", error: null, startedAt,
            });
            fireResultSidecarLoaders();
            return;
        }
        // Prefer ``category === "displacement"`` so a fresh load opens
        // on the deformation field — that's the field most users want
        // to see first, and it's also the warp source for everything
        // else. Falls back to the first renderable field (nodal or
        // element) when the manifest has no displacement (e.g.
        // stress-only output).
        const field =
            manifest.fields.find((f) => f.category === "displacement") ??
            manifest.fields[0] ??
            // A result-less deck (a design-model .fem/.inp/.med bake, or an
            // input deck exported from a SIN) has geometry and no fields at
            // all. Mesh-only is the correct open, not a crash on fields[0].
            null;
        const reduction = field?.default_view?.reduction ?? "magnitude";
        await load_fea_streaming({
            sourceName,
            manifest,
            fieldName: field ? field.name_canonical : null,
            stepIndex: 0,
            reduction: field ? reduction : null,
            displacementScale: 1,
            sliderFactor: 1,
            signal: controller.signal,
            onStage: (stage, progress) => {
                if (!useConversionStore.getState().jobs[storeKey]) return;
                const overall =
                    MANIFEST_PROGRESS_CEILING
                    + progress * (1 - MANIFEST_PROGRESS_CEILING);
                convStore.setJob(storeKey, {
                    sourceKey: storeKey,
                    jobId: "",
                    derivedKey: "",
                    status: "running",
                    progress: overall,
                    stage,
                    error: null,
                    startedAt,
                });
            },
        });
        // Mark done so the toast self-removes (ConversionProgress
        // filters out done jobs). All three phases completed.
        convStore.setJob(storeKey, {
            sourceKey: storeKey,
            jobId: "",
            derivedKey: "",
            status: "done",
            progress: 1,
            stage: "ready",
            error: null,
            startedAt,
        });
        fireResultSidecarLoaders();
    } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
            // User cancelled (or server-side cancel via the kill
            // endpoint). The store row is already gone by the time
            // we get here; don't surface an error toast for an
            // explicitly-requested abort.
            return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        convStore.setJob(storeKey, {
            sourceKey: storeKey,
            jobId: "",
            derivedKey: "",
            status: "error",
            progress: 0,
            stage: "failed",
            error: msg,
            startedAt,
        });
        throw err;
    } finally {
        unsubscribe();
    }
}

