/**
 * Cellbuilder COMPILE/PREVIEW JOBS slice.
 *
 * Owns: the in-flight build (job id, status, engine log) and the two loaded
 * result sources, simulation and detail; the committed compile, the ephemeral
 * preview of the uncommitted document, the in-browser compile, and loading or
 * unloading a result GLB into the scene. The polling loop itself lives in
 * ./compileJobRunner so compile and preview behave identically.
 */

import {capabilities} from "@/services/capabilities";
import type {CompileJobState} from "./types";
import type {CellBuilderSlice} from "./state";
import {currentScopePart, proceduralToastKey, setProceduralToast} from "./shared";
import {useConversionStore} from "@/state/conversionStore";
import {modelMaxX} from "./bounds";
import {placedCells} from "@/utils/cellbuilder/include";
import {makeStartCompileJob} from "./compileJobRunner";
import {NoAllowedEngineError, planExecution} from "@/services/compute/executionPolicy";
import {refreshAccessIfStale} from "@/services/compute/workerAccess";
import {browserEngineRoute} from "@/services/compute/proceduralBrowserEngine";
import {loadPluginWheelsManifest} from "@/utils/pyodide/pluginWheels";

export interface JobsSlice {
  autoCompile: boolean;
  compileJob: CompileJobState | null;
  /** Engine messages captured during the most recent compile/preview (logging +
   * stdout), fetched once the job reaches done/cached/error. null = no log yet
   * (nothing compiled this session); "" = compiled but the engine emitted nothing. */
  compileLog: string | null;
  // The compile RUN the shown log belongs to (the job id), and whether that run
  // is the one just triggered. A cache hit shows the log of the run that BUILT
  // the artifact — still a real run, but not this one, so the panel says so
  // instead of passing off an old failure as the current build's.
  compileLogRunId: string | null;
  compileLogIsCurrentRun: boolean;
  /** Source name of the compiled SIMULATION result currently loaded in the scene. */
  resultSourceName: string | null;
  /** Source name of the compiled DETAIL result currently loaded in the scene. */
  detailSourceName: string | null;
  setAutoCompile: (v: boolean) => void;
  /** Compile the active model. ``force`` recompiles even if the revision's GLB
   * is already cached — used when the compiler engine changed but the document
   * (the cache key) did not, so a plain Compile would return the stale blob. */
  compile: (force?: boolean, lod?: "sim" | "detail") => Promise<void>;
  /** Build the current (uncommitted) document as an ephemeral preview — no
   * commit, no revision bump; the interactive visualise-then-commit loop. */
  compilePreview: (force?: boolean, lod?: "sim" | "detail") => Promise<void>;
  /** Preview the LOD(s) selected by buildSim/buildDetail (the Compile button and
   * the ⇧↵ shortcut). Builds each so switching views is instant. */
  compilePreviewSelected: (force?: boolean) => Promise<void>;
  /** Which level(s) of detail a Compile produces: simulation, detail, or both. */
  buildSim: boolean;
  buildDetail: boolean;
  setBuildSim: (on: boolean) => void;
  setBuildDetail: (on: boolean) => void;
  /** Compile the CURRENT (uncommitted) doc entirely in the browser via the
   * built-in adapy engine (Pyodide/WASM), loading the result straight into the
   * scene — no server round-trip, no commit. Catalog/CAD equipment falls back to
   * archetypes/boxes (the browser has no DB). */
  compileInBrowser: () => Promise<void>;
  viewResult: (
    derivedKey: string,
    lod?: "sim" | "detail",
    /** Explicit scene source name.
     *
     * Without it the name is derived from whichever model is ACTIVE, which is
     * fine while the cellbuilder is the only caller but makes "is this model's
     * result in the scene?" unanswerable for any other one: the same model
     * loads under a different name depending on what was active at the time.
     * The storage panel passes a name derived from the model itself, so a row
     * can show whether its result is loaded — and for the active model the two
     * rules agree, because that name IS active.name. */
    sourceName?: string,
  ) => Promise<void>;
  hideResult: () => void;
  hideDetail: () => void;
}

// The toast label of a browser build; the detail LOD gets its own toast.
const browserLabel = (name: string, lod: "sim" | "detail"): string =>
  lod === "detail" ? `${name} (detail, browser)` : `${name} (browser)`;

export const createJobsSlice: CellBuilderSlice<JobsSlice> = (set, get) => {
  const startCompileJob = makeStartCompileJob(set, get);

  // Build the CURRENT document in the browser (Pyodide/WASM) and show it.
  // LOCAL ONLY: nothing is uploaded or cached on the server (the browser has no
  // catalog DB, so its result is not the server's and must not stand in for it).
  // Committing still stores the model itself on the server. Drives compileJob
  // like a server build so the toolbar, busy guards and failure surface agree.
  // "server-only": the selected engine cannot run in a browser (at this lod). Which
  // engines can is decided by browserEngineRoute (services/compute/proceduralBrowserEngine).
  const runBrowserCompile = async (
    label: string,
    lod: "sim" | "detail" = "sim",
  ): Promise<"done" | "server-only" | "error"> => {
    const active = get().active;
    if (!active) return "error";
    const doc = get().toDoc();
    set({
      compileJob: { jobId: null, derivedKey: "", status: "running" },
      compileLog: null,
      compileLogRunId: null,
      compileLogIsCurrentRun: false,
    });
    setProceduralToast(label, {
      status: "running",
      stage: "compiling in browser…",
      progress: 0,
      startedAt: Date.now(),
    });
    // Handing the build to the server: the server job shows its own toast, so
    // the browser's must go -- left "running", it never finishes and the build
    // looks stuck forever.
    const toServer = (): "server-only" => {
      set({ compileJob: null });
      useConversionStore.getState().clearJob(proceduralToastKey(label));
      return "server-only";
    };
    try {
      // Resolve the engine for the browser: the default and built-in slugs
      // dispatch directly; an engine a baked plugin wheel registers (a
      // worker-advertised one too) installs that plugin and dispatches by slug; a
      // database engine asks the server, which answers with a wheel the browser
      // micropip-installs from its presigned URL, or server-only.
      const route = browserEngineRoute({
        slug: get().selectedEngine,
        engines: get().engines,
        manifest: await loadPluginWheelsManifest(),
        lod,
        detailing: get().selectedDetailing,
      });
      if (route.kind === "server-only") return toServer();
      const onLog = (m: string) => setProceduralToast(label, { stage: m });
      const pyodide = await import("@/utils/pyodide/pyodide_converter");
      const { load_glb_from_bytes } = await import(
        "@/utils/scene/handlers/view_file_object_from_server"
      );
      let bytes: Uint8Array;
      if (route.kind === "baked") {
        bytes = await pyodide.runProceduralEngineViaPyodide(route.engine, doc, { onLog, lod });
      } else {
        let engineArg: string | null = route.kind === "builtin" ? route.engine : null;
        let wheel: { entrypoint: string; deps: string[]; url: string } | null = null;
        if (route.kind === "resolve") {
          const resolved = await capabilities.procedural.resolveEngine(currentScopePart(), route.engineId);
          if (resolved.kind === "wheel") {
            if (!resolved.ready || !resolved.wheel_url || !resolved.entrypoint)
              throw new Error("engine wheel is not built yet — try again shortly");
            wheel = {
              entrypoint: resolved.entrypoint,
              deps: resolved.pyodide_deps ?? [],
              url: resolved.wheel_url,
            };
          } else if (resolved.kind === "server") {
            return toServer();
          } else if (resolved.entrypoint) {
            engineArg = resolved.entrypoint;
          } else {
            engineArg = get().selectedEngine;
          }
        }
        bytes = await pyodide.compileProceduralViaPyodide(doc, { onLog, engine: engineArg, wheel });
      }
      const sourceName = lod === "detail" ? `procedural-detail:${active.name}` : `procedural:${active.name}`;
      // never move the camera; only this result's own prior load is replaced
      await load_glb_from_bytes(bytes, sourceName, false, "replaceSource");
      set({
        ...(lod === "detail" ? { detailSourceName: sourceName } : { resultSourceName: sourceName }),
        compileJob: { jobId: null, derivedKey: "", status: "done" },
      });
      void import("@/utils/cellbuilder/groupView").then(({ reapplyGroupViewToResult }) =>
        reapplyGroupViewToResult(),
      );
      if (get().sideBySide) {
        const { applySideBySideOffset } = await import("@/utils/scene/handlers/side_by_side");
        applySideBySideOffset(
          sourceName,
          true,
          modelMaxX(placedCells(get().cells, get().groups, get().loftMembers)),
        );
      }
      setProceduralToast(label, { status: "done", progress: 1, stage: "rendered in browser (not saved)" });
      return "done";
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      set({ compileJob: { jobId: null, derivedKey: "", status: "error", error } });
      setProceduralToast(label, { status: "error", error, stage: "browser compile failed" });
      return "error";
    }
  };

  // Where a build runs: the execution policy's "procedural_compile" order for the
  // selected engine (services/compute/executionPolicy). Pyodide is runBrowserCompile;
  // a browser build that fails falls back to the worker when the policy lists it
  // next. The detail LOD runs the server's detailing stack (except for an engine a
  // baked plugin wheel registers, with no detailing engine selected: see
  // browserEngineRoute), and an engine that cannot run in a browser ("server-only")
  // has no browser path either: those go to the worker unjudged, for everyone.
  // Returns "done" (built in the browser), the server build to start (runOnWorker:
  // an explicit worker choice), or null (nothing the policy allows could build it;
  // the error is shown).
  const routeBuild = async (
    lod: "sim" | "detail",
    label: string,
  ): Promise<"done" | { runOnWorker: boolean } | null> => {
    if (lod === "detail") {
      const route = browserEngineRoute({
        slug: get().selectedEngine,
        engines: get().engines,
        manifest: await loadPluginWheelsManifest(),
        lod,
        detailing: get().selectedDetailing,
      });
      if (route.kind === "server-only") return { runOnWorker: false };
    }
    await refreshAccessIfStale();
    const plan = planExecution("procedural_compile", get().selectedEngine || "*", (e) => e === "pyodide");
    for (const engine of plan.engines) {
      if (engine === "worker") return { runOnWorker: plan.browserPath };
      if (engine !== "pyodide") continue;
      const outcome = await runBrowserCompile(label, lod);
      if (outcome === "done") return "done";
      if (outcome === "server-only") return { runOnWorker: false };
      // "error": the next engine in the order, if any (the failure toast stays).
    }
    if (plan.engines.length === 0) {
      const error = new NoAllowedEngineError(plan).message;
      set({ compileJob: { jobId: null, derivedKey: "", status: "error", error } });
      setProceduralToast(label, { status: "error", error, stage: "no execution engine allowed" });
    }
    return null;
  };

  return {
    autoCompile: true,
    compileJob: null,
    compileLog: null,
    compileLogRunId: null,
    compileLogIsCurrentRun: false,
    resultSourceName: null,
    detailSourceName: null,
    buildSim: true,
    buildDetail: false,
    setAutoCompile: (autoCompile) => set({ autoCompile }),

    compile: async (force = false, lod = "sim") => {
      // A COMMITTED build: persists the revision (if dirty) then builds the
      // revision-keyed GLB. This is what commit()'s auto-compile and the detail
      // view use; interactive previewing is compilePreview() (no commit).
      const s = get();
      if (!s.active) return;
      if (s.dirty) {
        const ok = await get().commit();
        // commit() auto-compiles on success when enabled; avoid double-run.
        // A forced recompile still proceeds — the auto-compile after commit is a
        // normal (cache-honouring) run, so we fall through to re-run with force.
        // The auto-compile only covers the SIMULATION lod, so a detail request
        // must still proceed after a commit to build its own artifact.
        if (ok && get().autoCompile && !force && lod === "sim") return;
        if (!ok) return;
      }
      const active = get().active;
      if (!active) return;
      const label = lod === "detail" ? `${active.name} (detail)` : active.name;
      const route = await routeBuild(lod, browserLabel(active.name, lod));
      if (route === "done" || route === null) return;
      await startCompileJob(label, lod, () =>
        capabilities.procedural.compileModel(currentScopePart(), active.modelId, {
          force,
          lod,
          engine: get().selectedEngine,
          detailing: get().selectedDetailing,
          detailingOptions: get().detailingOptionsPayload(),
          runOnWorker: route.runOnWorker,
        }),
      );
    },

    compilePreview: async (force = false, lod = "sim") => {
      // Build the CURRENT (uncommitted) document as an ephemeral preview — no
      // commit, no revision bump. The server keys the GLB on the doc's content
      // hash, so re-previewing an unchanged doc is free; committing later
      // promotes this exact blob to the revision. This is the interactive
      // visualise-then-commit loop (and the ⇧↵ shortcut / side-by-side driver).
      const active = get().active;
      if (!active) return;
      const route = await routeBuild(lod, browserLabel(active.name, lod));
      if (route === "done" || route === null) return;
      const doc = get().toDoc();
      const label = `${active.name} (preview)`;
      await startCompileJob(label, lod, () =>
        capabilities.procedural.previewModel(
          currentScopePart(),
          active.modelId,
          doc,
          {
            engine: get().selectedEngine,
            lod,
            force,
            detailing: get().selectedDetailing,
            detailingOptions: get().detailingOptionsPayload(),
            runOnWorker: route.runOnWorker,
          },
        ),
      );
    },

    compilePreviewSelected: async (force = false) => {
      // Build whichever LOD(s) the user selected (defaulting to simulation if
      // somehow neither is on). Sequential so the two jobs don't contend; each
      // shows in its own view (autoShow renders only the active view's LOD).
      const { buildSim, buildDetail } = get();
      const sim = buildSim || !buildDetail;
      if (sim) await get().compilePreview(force, "sim");
      if (buildDetail) await get().compilePreview(force, "detail");
    },

    setBuildSim: (on) => set({ buildSim: on }),
    setBuildDetail: (on) => set({ buildDetail: on }),

    viewResult: async (derivedKey: string, lod = "sim", explicitSourceName?: string) => {
      const active = get().active;
      const base = active ? active.name : derivedKey;
      // Simulation and detail are distinct scene sources so they never collide.
      const sourceName =
        explicitSourceName ??
        (lod === "detail" ? `procedural-detail:${base}` : `procedural:${base}`);
      const { load_glb_by_url_rest } = await import(
        "@/utils/scene/handlers/view_file_object_from_server"
      );
      // autoFit=false: a compile/recompile must never move the camera. translate=true: reuse
      // the scene's current translation instead of recentring on this result's own box, so the
      // cells don't shift under that still camera (a fresh scene with no translation yet still
      // recentres as before — loadModel/placeModel fall back to that when there is none to reuse).
      // placement: a result this slice names itself (the compile path) only ever replaces its
      // own prior result, leaving another model, a streamed result and the other LOD's result
      // loaded; a caller-named result (the storage panel opening a result as "the" model) keeps
      // the plain "replace" it had.
      await load_glb_by_url_rest(
        currentScopePart(),
        derivedKey,
        sourceName,
        false,
        true,
        explicitSourceName ? "replace" : "replaceSource",
      );
      set(
        lod === "detail"
          ? { detailSourceName: sourceName }
          : { resultSourceName: sourceName },
      );
      // A (re)loaded result arrives fully shown and in its own colours: put the
      // Groups section's hidden groups and colour-by-group back on it.
      void import("@/utils/cellbuilder/groupView").then(({ reapplyGroupViewToResult }) =>
        reapplyGroupViewToResult(),
      );
      // Side-by-side: nudge the freshly-loaded result beside the topology. The
      // loader replaces the group (position resets to the model translation), so
      // re-apply on every load.
      if (get().sideBySide) {
        const topoMaxX = modelMaxX(placedCells(get().cells, get().groups, get().loftMembers));
        void import("@/utils/scene/handlers/side_by_side").then(
          ({ applySideBySideOffset }) =>
            applySideBySideOffset(sourceName, true, topoMaxX),
        );
      }
    },

    compileInBrowser: async () => {
      const active = get().active;
      if (!active) return;
      const label = `${active.name} (browser)`;
      if ((await runBrowserCompile(label)) === "server-only") {
        setProceduralToast(label, {
          status: "error",
          error: "this engine runs server-side only — it needs the server worker pool",
          stage: "browser compile failed",
        });
      }
    },

    hideResult: () => {
      const sourceName = get().resultSourceName;
      if (!sourceName) return;
      void import("@/utils/scene/handlers/unload_any_source").then(({ unload_any_source }) => unload_any_source(sourceName));
      set({ resultSourceName: null });
    },

    hideDetail: () => {
      const sourceName = get().detailSourceName;
      if (!sourceName) return;
      void import("@/utils/scene/handlers/unload_any_source").then(({ unload_any_source }) => unload_any_source(sourceName));
      set({ detailSourceName: null });
    },
  };
};
