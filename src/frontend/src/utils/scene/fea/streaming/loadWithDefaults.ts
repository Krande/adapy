// FEA streaming: the toggle entry point.
//
// Owns: turning a source name into a load -- fetch (or bake and poll) the
// manifest, mirror the bake's queue lifecycle into the conversion toast, pick
// the opening field and reduction, run the load with the toast following its
// stages, and fire the plugin result-sidecar loaders once the geometry is in.
// Abort from the toast's Kill is honoured through the manifest poll and the
// load's own signal. Holds no state: the toast row is the conversion store's.
//
// Inputs: the load function (injected, so this module does not import the
// loader it feeds), the source name, the scope store, the FEA and files
// capabilities, and the conversion store.

import {capabilities} from "@/services/capabilities";
import {makeViewerApiFetcher} from "@/services/feaFieldBlob";
import type {FeaManifest} from "@/services/viewerApi";
import type {SidecarFetcher} from "@/plugins/registry";
import {runResultSidecarLoaders} from "@/plugins/sidecarLoaders";
import {useConversionStore} from "@/state/conversionStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import type {LoadFeaStreaming} from "./types";

/** Toggle entry point: fetch the manifest, pick sensible defaults
 * (first field, default reduction, step 0, factor 1), and run
 * ``load_fea_streaming``. The user then refines via SimulationControls
 * — they no longer have to step through a modal picker.
 *
 * Returns silently on a manifest with no fields; the storage row
 * stays unchecked-but-toggled which the user can interpret as
 * "nothing renderable in this file". */
export async function loadFeaWithDefaults(load: LoadFeaStreaming, sourceName: string): Promise<void> {
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
            await load({
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
        await load({
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
