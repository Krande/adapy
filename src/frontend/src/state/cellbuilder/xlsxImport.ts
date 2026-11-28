/**
 * Excel IMPORT driver.
 *
 * Owns: importing a staged workbook with a resolved engine and opening the
 * freshly-created model on success. Runs where the execution policy's
 * "procedural_io" order says (services/compute/proceduralIo): in the browser the
 * engine parses the workbook into a document, which the ordinary create + commit
 * REST calls then store (no job); on the worker the import job does both. Bound to
 * the store once (set/get) and shared by the auto-detected and engine-picked paths.
 */

import {capabilities} from "@/services/capabilities";
import type {ProceduralModelDetail} from "@/services/viewerApi";
import {runProceduralIo} from "@/services/compute/proceduralIo";
import type {CellBuilderGet, CellBuilderSet} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";
import {awaitProceduralJob, proceduralIoTask, runInBrowser} from "./proceduralIoSupport";

export function makeRunImport(set: CellBuilderSet, get: CellBuilderGet) {
  const IMPORT_LABEL = "Import from Excel";
  // The workbook bytes of each staged upload, for the in-browser parse (the staged
  // copy on the server is the model's full-fidelity source, as a worker import keeps).
  const staged = new Map<string, Uint8Array>();

  const runImport = async (
    sourceKey: string,
    engine: string,
    name: string,
    workbook?: ArrayBuffer | Uint8Array,
  ): Promise<void> => {
    if (workbook) staged.set(sourceKey, new Uint8Array(workbook.slice(0)));
    const scope = currentScopePart();
    setProceduralToast(IMPORT_LABEL, {
      status: "running",
      progress: 0.3,
      stage: "importing…",
    });
    try {
      const { value: detail } = await runProceduralIo<ProceduralModelDetail>(
        await proceduralIoTask(get, "import_xlsx", { slug: engine }),
        {
          browser: async (route) => {
            const bytes = staged.get(sourceKey);
            if (!bytes) throw new Error("the workbook is no longer in the browser");
            setProceduralToast(IMPORT_LABEL, { stage: "importing in browser…" });
            const answer = await runInBrowser(
              "import_xlsx",
              { engine: route.engine },
              // A copy: the worker takes the buffer it is handed.
              { engineId: route.engineId, data: bytes.slice() },
            );
            const doc = JSON.parse(new TextDecoder().decode(answer)) as Record<string, unknown>;
            doc.source_xlsx_key = sourceKey;
            return capabilities.procedural.createModelWithDoc(scope, name, doc);
          },
          worker: async (explicit) => {
            const res = await capabilities.procedural.importXlsx(
              scope,
              { source_key: sourceKey, engine, name },
              { runOnWorker: explicit },
            );
            const jobId = res.job_id;
            const key = await awaitProceduralJob(jobId, (st) =>
              setProceduralToast(IMPORT_LABEL, {
                status: "running",
                progress: st.progress ?? 0.4,
                stage: st.stage || "importing…",
                jobId,
              }),
            );
            return capabilities.procedural.fetchImportedModel(scope, key || res.derived_key);
          },
        },
      );
      staged.delete(sourceKey);
      get().open(detail.id, detail.name, detail.revision, detail.doc);
      setProceduralToast(IMPORT_LABEL, {
        status: "done",
        progress: 1,
        stage: "imported",
      });
    } catch (e) {
      setProceduralToast(IMPORT_LABEL, {
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      });
    } finally {
      set({ xlsxBusy: false });
    }
  };

  /** Keep a staged upload's bytes for an import that waits for an engine choice. */
  const stage = (sourceKey: string, workbook: ArrayBuffer | Uint8Array) =>
    void staged.set(sourceKey, new Uint8Array(workbook.slice(0)));

  return Object.assign(runImport, { stage });
}
