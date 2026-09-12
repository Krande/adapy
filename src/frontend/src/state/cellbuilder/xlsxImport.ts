/**
 * Excel IMPORT JOB driver.
 *
 * Owns: enqueuing an import for a staged workbook with a resolved engine,
 * polling it, and opening the freshly-created model on success. Bound to the
 * store once (set/get) and shared by the auto-detected and engine-picked
 * import paths.
 */

import {capabilities} from "@/services/capabilities";
import type {CellBuilderGet, CellBuilderSet} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";

  // Enqueue an import job for a staged workbook (source_key) with a resolved
  // engine, poll it, and open the freshly-created model on success. Shared by the
  // auto-detected path and the "picked an engine from the prompt" path.
export function makeRunImport(set: CellBuilderSet, get: CellBuilderGet) {
  const IMPORT_LABEL = "Import from Excel";
  const runImport = async (
    sourceKey: string,
    engine: string,
    name: string,
  ): Promise<void> => {
    const scope = currentScopePart();
    setProceduralToast(IMPORT_LABEL, {
      status: "running",
      progress: 0.3,
      stage: "importing…",
    });
    const openImported = async (derivedKey: string) => {
      try {
        const detail = await capabilities.procedural.fetchImportedModel(scope, derivedKey);
        get().open(detail.id, detail.name, detail.revision, detail.doc);
        setProceduralToast(IMPORT_LABEL, {
          status: "done",
          progress: 1,
          stage: "imported",
          derivedKey,
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
    try {
      const res = await capabilities.procedural.importXlsx(scope, {
        source_key: sourceKey,
        engine,
        name,
      });
      const jobId = res.job_id;
      const poll = async () => {
        try {
          const st = await capabilities.procedural.jobStatus(jobId);
          if (st.status === "done") {
            await openImported(st.derived_key || res.derived_key);
            return;
          }
          if (st.status === "error") {
            set({ xlsxBusy: false });
            setProceduralToast(IMPORT_LABEL, {
              status: "error",
              stage: st.stage || "",
              error: st.error ?? "import failed",
            });
            return;
          }
          setProceduralToast(IMPORT_LABEL, {
            status: "running",
            progress: st.progress ?? 0.4,
            stage: st.stage || "importing…",
            jobId,
          });
          setTimeout(poll, 1500);
        } catch (e) {
          set({ xlsxBusy: false });
          setProceduralToast(IMPORT_LABEL, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        }
      };
      setTimeout(poll, 1200);
    } catch (e) {
      set({ xlsxBusy: false });
      setProceduralToast(IMPORT_LABEL, {
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      });
    }
  };

  return runImport;
}
