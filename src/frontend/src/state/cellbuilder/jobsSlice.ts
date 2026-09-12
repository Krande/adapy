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
import {currentScopePart, setProceduralToast} from "./shared";
import {modelMaxX} from "./bounds";
import {makeStartCompileJob} from "./compileJobRunner";

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

export const createJobsSlice: CellBuilderSlice<JobsSlice> = (set, get) => {
  const startCompileJob = makeStartCompileJob(set, get);

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
      await startCompileJob(label, lod, () =>
        capabilities.procedural.compileModel(currentScopePart(), active.modelId, {
          force,
          lod,
          engine: get().selectedEngine,
          detailing: get().selectedDetailing,
          detailingOptions: get().detailingOptionsPayload(),
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
      // autoFit=false: a compile/recompile must never move the camera.
      await load_glb_by_url_rest(currentScopePart(), derivedKey, sourceName, false);
      set(
        lod === "detail"
          ? { detailSourceName: sourceName }
          : { resultSourceName: sourceName },
      );
      // Side-by-side: nudge the freshly-loaded result beside the topology. The
      // loader replaces the group (position resets to the model translation), so
      // re-apply on every load.
      if (get().sideBySide) {
        const topoMaxX = modelMaxX(get().cells);
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
      const doc = get().toDoc();
      setProceduralToast(label, {
        status: "running",
        stage: "compiling in browser…",
        progress: 0,
        startedAt: Date.now(),
      });
      try {
        const { compileProceduralViaPyodide } = await import(
          "@/utils/pyodide/pyodide_converter"
        );
        const { load_glb_from_bytes } = await import(
          "@/utils/scene/handlers/view_file_object_from_server"
        );
        // Resolve the engine for the browser: a built-in slug (e.g. echo)
        // dispatches directly; a kind:wheel engine is micropip-installed from its
        // presigned wheel URL then dispatched via its entrypoint; a server engine
        // can't run in-browser.
        const engineSlug = get().selectedEngine;
        let engineArg: string | null =
          engineSlug && engineSlug !== "adapy-default" ? engineSlug : null;
        let wheel: { entrypoint: string; deps: string[]; url: string } | null =
          null;
        if (engineArg) {
          const eng = get().engines.find((e) => e.slug === engineSlug);
          if (eng && eng.origin !== "builtin") {
            const resolved = await capabilities.procedural.resolveEngine(
              currentScopePart(),
              eng.id,
            );
            if (resolved.kind === "wheel") {
              if (
                !resolved.ready ||
                !resolved.wheel_url ||
                !resolved.entrypoint
              )
                throw new Error(
                  "engine wheel is not built yet — try again shortly",
                );
              wheel = {
                entrypoint: resolved.entrypoint,
                deps: resolved.pyodide_deps ?? [],
                url: resolved.wheel_url,
              };
              engineArg = null; // dispatch happens via the wheel entrypoint
            } else if (resolved.kind === "server") {
              throw new Error(
                "this engine runs server-side only — use Compile, not in-browser",
              );
            } else if (resolved.entrypoint) {
              engineArg = resolved.entrypoint;
            }
          }
        }
        const bytes = await compileProceduralViaPyodide(doc, {
          onLog: (m) => setProceduralToast(label, { stage: m }),
          engine: engineArg,
          wheel,
        });
        const sourceName = `procedural:${active.name}`;
        await load_glb_from_bytes(bytes, sourceName, false); // never move the camera
        set({ resultSourceName: sourceName });
        if (get().sideBySide) {
          const { applySideBySideOffset } = await import(
            "@/utils/scene/handlers/side_by_side"
          );
          applySideBySideOffset(sourceName, true, modelMaxX(get().cells));
        }
        setProceduralToast(label, {
          status: "done",
          progress: 1,
          stage: "rendered in browser",
        });
      } catch (e) {
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
          stage: "browser compile failed",
        });
      }
    },

    hideResult: () => {
      const sourceName = get().resultSourceName;
      if (!sourceName) return;
      void import("@/utils/scene/handlers/unload_source_from_scene").then(
        ({ unload_source_from_scene }) => {
          unload_source_from_scene(sourceName);
        },
      );
      set({ resultSourceName: null });
    },

    hideDetail: () => {
      const sourceName = get().detailSourceName;
      if (!sourceName) return;
      void import("@/utils/scene/handlers/unload_source_from_scene").then(
        ({ unload_source_from_scene }) => {
          unload_source_from_scene(sourceName);
        },
      );
      set({ detailSourceName: null });
    },
  };
};
