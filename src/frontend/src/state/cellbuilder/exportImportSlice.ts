/**
 * Cellbuilder EXPORT/IMPORT slice.
 *
 * Owns: downloading the committed model as a workbook or a CAD/analysis file
 * (IFC / Genie XML / Genie workspace), and the Excel import round-trip —
 * staging an upload, auto-detecting its owning engine and prompting for one
 * when the workbook carries no metadata. Each commits first when dirty so the
 * file matches what is on screen.
 */

import {capabilities} from "@/services/capabilities";
import type {CellBuilderSlice} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";
import {makeRunImport} from "./xlsxImport";

export interface ExportImportSlice {
  // ── Excel round-trip ──────────────────────────────────────────────
  /** True while an Excel export or import job is in flight (disables the
   * Export/Import buttons). */
  xlsxBusy: boolean;
  /** A staged import awaiting an engine choice: set when an uploaded workbook had
   * no `_ADA_META` sheet so the engine couldn't be auto-detected. Non-null shows
   * the engine-picker prompt in the panel. */
  importPrompt: { sourceKey: string; name: string } | null;
  /** Export the active model to its (selected) engine's Excel workbook and
   * trigger a browser download. Commits first when there are unsaved edits so the
   * workbook matches what's on screen. */
  exportToExcel: () => Promise<void>;
  /** Export + download the committed model as a CAD/analysis file: "ifc" (the
   * DETAIL model, clash cuts as IfcRelVoidsElement voids), "gxml" (the
   * SIMULATION model as a Genie concept XML) or "gnx" (that XML as a Genie
   * workspace). Commits first when dirty. IFC honours `exportIfcCad` (splice
   * real catalog CAD equipment). */
  exportModel: (format: "ifc" | "gxml" | "gnx") => Promise<void>;
  /** IFC export: splice real catalog CAD geometry for equipment (default on).
   * Off = placeholder boxes. gxml ignores this (Genie equipment concept type). */
  exportIfcCad: boolean;
  setExportIfcCad: (v: boolean) => void;
  /** Begin importing a workbook: upload it, auto-detect the owning engine from its
   * `_ADA_META` sheet, and import immediately when detected — otherwise set
   * `importPrompt` so the user picks an engine. */
  beginImportFromExcel: (file: File) => Promise<void>;
  /** Resolve an import that needed an engine choice (from the prompt). The
   * caller passes the prompt captured at render time: the menu that hosts the
   * engine picker dismisses (running `cancelImport`, which nulls `importPrompt`)
   * BEFORE the item's click handler fires, so reading `importPrompt` back from
   * the store here would race to null and silently drop the import. */
  confirmImportEngine: (
    engine: string,
    prompt?: { sourceKey: string; name: string },
  ) => Promise<void>;
  /** Dismiss the pending-import engine prompt without importing. */
  cancelImport: () => void;
}

export const createExportImportSlice: CellBuilderSlice<ExportImportSlice> = (set, get) => {
  const runImport = makeRunImport(set, get);

  return {
    xlsxBusy: false,
    exportIfcCad: true,
    importPrompt: null,

    // ── Excel round-trip ──────────────────────────────────────────────
    exportToExcel: async () => {
      const active = get().active;
      if (!active || get().xlsxBusy) return;
      const scope = currentScopePart();
      const engine = get().selectedEngine;
      const label = "Export to Excel";
      set({ xlsxBusy: true });
      setProceduralToast(label, {
        status: "running",
        progress: 0,
        stage: "exporting…",
        startedAt: Date.now(),
      });
      const download = async (derivedKey: string) => {
        try {
          await capabilities.procedural.downloadArtifact(
            scope,
            derivedKey,
            `${active.name || "procedural-model"}.xlsx`,
          );
          setProceduralToast(label, {
            status: "done",
            progress: 1,
            stage: "downloaded",
          });
        } catch (e) {
          setProceduralToast(label, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        } finally {
          set({ xlsxBusy: false });
        }
      };
      try {
        // Export the COMMITTED revision (the worker reads the DB doc); commit any
        // unsaved edits first so the workbook matches what's on screen.
        if (get().dirty) await get().commit();
        const res = await capabilities.procedural.exportModel(
          scope,
          active.modelId,
          "xlsx",
          { engine },
        );
        if (res.cached || !res.job_id) {
          await download(res.derived_key);
          return;
        }
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await capabilities.procedural.jobStatus(jobId);
            if (st.status === "done") {
              await download(st.derived_key || res.derived_key);
              return;
            }
            if (st.status === "error") {
              set({ xlsxBusy: false });
              setProceduralToast(label, {
                status: "error",
                stage: st.stage || "",
                error: st.error ?? "export failed",
              });
              return;
            }
            setProceduralToast(label, {
              status: "running",
              progress: st.progress ?? 0,
              stage: st.stage || "exporting…",
              jobId,
            });
            setTimeout(poll, 1500);
          } catch (e) {
            set({ xlsxBusy: false });
            setProceduralToast(label, {
              status: "error",
              error: e instanceof Error ? e.message : String(e),
            });
          }
        };
        setTimeout(poll, 1200);
      } catch (e) {
        set({ xlsxBusy: false });
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    setExportIfcCad: (v) => set({ exportIfcCad: v }),

    exportModel: async (format) => {
      const active = get().active;
      if (!active || get().xlsxBusy) return;
      const scope = currentScopePart();
      const label =
        format === "ifc"
          ? "Download IFC (detail)"
          : format === "gnx"
            ? "Download Genie workspace (sim)"
            : "Download Genie XML (sim)";
      set({ xlsxBusy: true });
      setProceduralToast(label, {
        status: "running",
        progress: 0,
        stage: "compiling…",
        startedAt: Date.now(),
      });
      const download = async (derivedKey: string) => {
        try {
          await capabilities.procedural.downloadArtifact(
            scope,
            derivedKey,
            `${active.name || "procedural-model"}.${format}`,
          );
          setProceduralToast(label, {
            status: "done",
            progress: 1,
            stage: "downloaded",
          });
        } catch (e) {
          setProceduralToast(label, {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          });
        } finally {
          set({ xlsxBusy: false });
        }
      };
      try {
        // Export the COMMITTED revision (the worker reads the DB doc); commit any
        // unsaved edits first so the file matches what's on screen.
        if (get().dirty) await get().commit();
        const res = await capabilities.procedural.exportModel(
          scope,
          active.modelId,
          format,
          format === "ifc" ? { cad: get().exportIfcCad } : undefined,
        );
        if (res.cached || !res.job_id) {
          await download(res.derived_key);
          return;
        }
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await capabilities.procedural.jobStatus(jobId);
            if (st.status === "done") {
              await download(st.derived_key || res.derived_key);
              return;
            }
            if (st.status === "error") {
              set({ xlsxBusy: false });
              setProceduralToast(label, {
                status: "error",
                stage: st.stage || "",
                error: st.error ?? "export failed",
              });
              return;
            }
            setProceduralToast(label, {
              status: "running",
              progress: st.progress ?? 0,
              stage: st.stage || "exporting…",
              jobId,
            });
            setTimeout(poll, 1500);
          } catch (e) {
            set({ xlsxBusy: false });
            setProceduralToast(label, {
              status: "error",
              error: e instanceof Error ? e.message : String(e),
            });
          }
        };
        setTimeout(poll, 1200);
      } catch (e) {
        set({ xlsxBusy: false });
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    beginImportFromExcel: async (file: File) => {
      if (get().xlsxBusy) return;
      const scope = currentScopePart();
      const label = "Import from Excel";
      // Derive a model name from the file (drop the extension).
      const name = file.name.replace(/\.[^.]+$/, "").trim() || "Imported model";
      set({ xlsxBusy: true, importPrompt: null });
      setProceduralToast(label, {
        status: "running",
        progress: 0,
        stage: "uploading…",
        startedAt: Date.now(),
      });
      try {
        const buf = await file.arrayBuffer();
        const detect = await capabilities.procedural.stageXlsxImport(scope, buf);
        if (detect.engine) {
          // Engine known from the workbook's _ADA_META — import straight away.
          await runImport(detect.source_key, detect.engine, name);
        } else {
          // No metadata (hand-made / legacy workbook): ask which engine to use.
          await get().fetchEngines();
          setProceduralToast(label, {
            status: "running",
            progress: 0.2,
            stage: "choose an engine…",
          });
          set({
            xlsxBusy: false,
            importPrompt: { sourceKey: detect.source_key, name },
          });
        }
      } catch (e) {
        set({ xlsxBusy: false });
        setProceduralToast(label, {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
        });
      }
    },

    confirmImportEngine: async (engine, prompt) => {
      // Prefer the prompt the caller captured at render time; fall back to the
      // store only if it's still there. The picker menu runs its `onClose`
      // (cancelImport) before this click handler, so `get().importPrompt` has
      // usually already been nulled — relying on it alone stalls the import.
      const active = prompt ?? get().importPrompt;
      if (!active) return;
      set({ importPrompt: null, xlsxBusy: true });
      await runImport(active.sourceKey, engine, active.name);
    },

    cancelImport: () => set({ importPrompt: null }),
  };
};
