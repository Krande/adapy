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
import type {ProceduralCompileResponse} from "@/services/viewerApi";
import {
  downloadBytes,
  runProceduralIo,
  XLSX_MIME,
  type ProceduralIoRoute,
  type ProceduralIoTask,
} from "@/services/compute/proceduralIo";
import type {CellBuilderGet, CellBuilderSet, CellBuilderSlice} from "./state";
import {currentScopePart, setProceduralToast} from "./shared";
import {awaitProceduralJob, equipmentFor, proceduralIoTask, runInBrowser} from "./proceduralIoSupport";
import {makeRunImport} from "./xlsxImport";

type BrowserRoute = Extract<ProceduralIoRoute, { kind: "browser" }>;

/** One export, browser first: commit unsaved edits (the file matches what is on
 * screen, and the worker reads the committed revision), then run the policy's order.
 * The browser answers with the file's bytes; the worker with a stored file (cached, or
 * a job to poll) that is downloaded as a blob. */
async function runExport(
  get: CellBuilderGet,
  set: CellBuilderSet,
  spec: {
    label: string;
    filename: string;
    mime: string;
    task: (doc: unknown) => Promise<ProceduralIoTask>;
    browser: (route: BrowserRoute, doc: unknown) => Promise<Uint8Array>;
    worker: (scope: string, explicit: boolean) => Promise<ProceduralCompileResponse>;
  },
): Promise<void> {
  const { label } = spec;
  const scope = currentScopePart();
  set({ xlsxBusy: true });
  setProceduralToast(label, { status: "running", progress: 0, stage: "exporting…", startedAt: Date.now() });
  try {
    if (get().dirty) await get().commit();
    const doc = get().toDoc();
    const { value } = await runProceduralIo<{ bytes: Uint8Array } | { key: string }>(await spec.task(doc), {
      browser: async (route) => {
        setProceduralToast(label, { stage: "exporting in browser…" });
        return { bytes: await spec.browser(route, doc) };
      },
      worker: async (explicit) => {
        const res = await spec.worker(scope, explicit);
        if (res.cached || !res.job_id) return { key: res.derived_key };
        const jobId = res.job_id;
        const key = await awaitProceduralJob(jobId, (st) =>
          setProceduralToast(label, { status: "running", progress: st.progress ?? 0, stage: st.stage || "exporting…", jobId }),
        );
        return { key: key || res.derived_key };
      },
    });
    if ("bytes" in value) downloadBytes(value.bytes, spec.filename, spec.mime);
    else await capabilities.procedural.downloadArtifact(scope, value.key, spec.filename);
    setProceduralToast(label, { status: "done", progress: 1, stage: "downloaded" });
  } catch (e) {
    setProceduralToast(label, { status: "error", error: e instanceof Error ? e.message : String(e) });
  } finally {
    set({ xlsxBusy: false });
  }
}

/** The download's file extension for an export format: "gxml" is Genie XML, a plain XML
 *  document -- its REST format id/capability type stays "gxml", only the downloaded file's
 *  own name says ".xml", matching the other route's own job result. */
export function downloadExtensionFor(format: "ifc" | "gxml" | "gnx"): string {
  return format === "gxml" ? "xml" : format;
}

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
    // Both exports run where the execution policy's "procedural_io" order says
    // (services/compute/proceduralIo): the browser first, producing the file bytes
    // the download hands over directly, the server worker as the fallback (its job
    // stores the file, which is then downloaded as a blob).
    exportToExcel: async () => {
      const active = get().active;
      if (!active || get().xlsxBusy) return;
      const engine = get().selectedEngine;
      await runExport(get, set, {
        label: "Export to Excel",
        filename: `${active.name || "procedural-model"}.xlsx`,
        mime: XLSX_MIME,
        task: () => proceduralIoTask(get, "export_xlsx", { slug: engine }),
        browser: (route, doc) =>
          runInBrowser(
            "export_xlsx",
            { doc, engine: route.engine, name: active.name || "procedural-model" },
            { engineId: route.engineId },
          ),
        worker: (scope, explicit) =>
          capabilities.procedural.exportModel(scope, active.modelId, "xlsx", { engine, runOnWorker: explicit }),
      });
    },

    setExportIfcCad: (v) => set({ exportIfcCad: v }),

    exportModel: async (format) => {
      const active = get().active;
      if (!active || get().xlsxBusy) return;
      const name = active.name || "procedural-model";
      await runExport(get, set, {
        label:
          format === "ifc"
            ? "Download IFC (detail)"
            : format === "gnx"
              ? "Download Genie workspace (sim)"
              : "Download Genie XML (sim)",
        filename: `${name}.${downloadExtensionFor(format)}`,
        mime: format === "gnx" ? "application/zip" : format === "ifc" ? "application/x-step" : "application/xml",
        // The built-in engine's (the server refuses the others): the model's own
        // engine, which is the one selected.
        task: (doc) => proceduralIoTask(get, "export_model", { format, doc }),
        browser: async (_route, doc) =>
          runInBrowser("export_model", {
            doc,
            format,
            name,
            equipment: await equipmentFor(get, doc),
          }),
        worker: (scope, explicit) =>
          capabilities.procedural.exportModel(scope, active.modelId, format, {
            ...(format === "ifc" ? { cad: get().exportIfcCad } : {}),
            runOnWorker: explicit,
          }),
      });
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
          await runImport(detect.source_key, detect.engine, name, buf);
        } else {
          runImport.stage(detect.source_key, buf);
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
