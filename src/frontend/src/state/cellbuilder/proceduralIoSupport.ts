/**
 * The builder's half of the procedural IO routing (services/compute/proceduralIo):
 * the task for the active model, the catalog equipment it places, the in-browser run
 * and the worker job's poll. Shared by the export/import and relocations slices.
 */

import {capabilities} from "@/services/capabilities";
import type {ConvertResponse} from "@/services/viewerApi";
import {
    catalogEquipmentDocs,
    placedCatalogTypes,
    type ProceduralIoAction,
    type ProceduralIoTask,
    type ProceduralModelFormat,
} from "@/services/compute/proceduralIo";
import {refreshAccessIfStale} from "@/services/compute/workerAccess";
import {loadPluginWheelsManifest} from "@/utils/pyodide/pluginWheels";
import type {CellBuilderGet} from "./state";
import {currentScopePart} from "./shared";

/** The task for the active model (`slug` defaults to the selected engine). */
export async function proceduralIoTask(
  get: CellBuilderGet,
  action: ProceduralIoAction,
  extra: {slug?: string | null; format?: ProceduralModelFormat; doc?: unknown} = {},
): Promise<ProceduralIoTask> {
  await refreshAccessIfStale();
  const s = get();
  const doc = (extra.doc ?? (s.active ? s.toDoc() : null)) as Parameters<typeof placedCatalogTypes>[0];
  const cadEquipment =
    extra.format === "ifc" && s.exportIfcCad && placedCatalogTypes(doc, s.equipmentTypes).some((t) => t.has_cad);
  return {
    action,
    slug: extra.slug !== undefined ? extra.slug : s.selectedEngine,
    engines: s.engines,
    manifest: await loadPluginWheelsManifest(),
    format: extra.format,
    detailing: s.selectedDetailing,
    cadEquipment,
  };
}

/** `{slug: catalog doc}` for the catalog equipment `doc` places (what the server
 * resolves from its database for the same job). */
export async function equipmentFor(get: CellBuilderGet, doc: unknown): Promise<Record<string, unknown>> {
  const placed = placedCatalogTypes(doc as Parameters<typeof placedCatalogTypes>[0], get().equipmentTypes);
  if (placed.length === 0) return {};
  const scope = currentScopePart();
  return catalogEquipmentDocs(placed, async (id) => (await capabilities.catalog.getEquipmentType(scope, id)).doc);
}

/** Run `action` in the browser Pyodide worker (ada.topo_model.wasm_io). */
export async function runInBrowser(
  action: ProceduralIoAction,
  payload: Record<string, unknown>,
  opts: {engineId?: string | null; data?: Uint8Array | null; onLog?: (m: string) => void} = {},
): Promise<Uint8Array> {
  const pyodide = await import("@/utils/pyodide/pyodide_converter");
  return pyodide.runProceduralIoViaPyodide(action, payload, opts);
}

/** Poll a worker job until it is done (resolves to its derived key) or fails (throws). */
export function awaitProceduralJob(
  jobId: string,
  onProgress: (st: ConvertResponse) => void,
  opts: {firstDelayMs?: number; intervalMs?: number} = {},
): Promise<string | undefined> {
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const st = await capabilities.procedural.jobStatus(jobId);
        if (st.status === "done") return resolve(st.derived_key || undefined);
        if (st.status === "error") return reject(new Error(st.error ?? "job failed"));
        onProgress(st);
        setTimeout(poll, opts.intervalMs ?? 1500);
      } catch (e) {
        reject(e);
      }
    };
    setTimeout(poll, opts.firstDelayMs ?? 1200);
  });
}
