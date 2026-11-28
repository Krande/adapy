// Where a procedural model's Excel export / import, relocation proposals and
// IFC/Genie export run: the execution policy's "procedural_io" order (qualifier: the
// engine slug), the browser (Pyodide, ada.topo_model.wasm_io) first by default and
// the server worker as the fallback. The route is decided before anything runs --
// pure, so it is testable without Pyodide -- and says whether the browser can run the
// task at all; a task it cannot run goes to the worker unjudged, as before.
//
// The browser runs what the server's jobs run, on the document the builder holds:
//   * the workbook (export/import) of the default engine, another built-in engine, or
//     an engine a baked plugin wheel registers (install that plugin, then call the
//     engine's export_xlsx / import_xlsx, as for a compile);
//   * relocation proposals (core's relocate, engine-independent);
//   * IFC / Genie XML / Genie workspace of the built-in engine (the server refuses the
//     others), except an IFC that splices linked CAD equipment or runs a detailing
//     engine: those stay on the worker.
// Catalog equipment the model places is read from the scope's catalog and handed over,
// as the server reads it from its database.

import type {PluginWheelsManifest} from "@/utils/pyodide/pluginWheels";

import {
    planExecution,
    runWithFallback,
    type AttemptContext,
    type ExecutionPlan,
    type FallbackResult,
} from "./executionPolicy";
import {bakedEngineWheel, type ListedEngine} from "./proceduralBrowserEngine";

export type ProceduralIoAction = "export_xlsx" | "import_xlsx" | "propose_relocations" | "export_model";
export type ProceduralModelFormat = "ifc" | "gxml" | "gnx";

export interface ProceduralIoTask {
    action: ProceduralIoAction;
    /** The engine slug ("" / "adapy-default" / null = the default engine). */
    slug: string | null | undefined;
    /** The engines the builder lists. */
    engines: readonly ListedEngine[];
    /** /wheels/plugin-wheels-manifest.json ({plugins: {}} when nothing is baked). */
    manifest: PluginWheelsManifest;
    /** export_model: the file format. */
    format?: ProceduralModelFormat;
    /** export_model ifc: the selected detailing engine ("none" / "" = none). */
    detailing?: string | null;
    /** export_model ifc: linked CAD geometry would be spliced for some placed equipment. */
    cadEquipment?: boolean;
}

export type ProceduralIoRoute =
    /** In the browser: `engine` is the slug handed to wasm_io (null = default), and
     * `engineId` the baked plugin to install first (null = a built-in engine). */
    | {kind: "browser"; engine: string | null; engineId: string | null}
    | {kind: "server-only"; reason: string};

const isDefault = (slug: string | null | undefined): boolean => !slug || slug === "adapy-default";

/** The execution policy's qualifier for a task: the engine slug, else "*". */
export function proceduralIoQualifier(slug: string | null | undefined): string {
    return isDefault(slug) ? "*" : (slug as string).toLowerCase();
}

/** Where the browser runs this task, or why it cannot. */
export function proceduralIoRoute(task: ProceduralIoTask): ProceduralIoRoute {
    const {action, slug, engines, manifest} = task;
    if (action === "propose_relocations") return {kind: "browser", engine: null, engineId: null};
    if (action === "export_model") {
        // The server's export-model is the built-in engine's alone; another engine's
        // refusal is the worker's to give, as before.
        if (!isDefault(slug)) return {kind: "server-only", reason: "IFC/Genie export is the built-in engine's"};
        if (task.format === "ifc") {
            const detailing = (task.detailing ?? "").trim();
            if (detailing && detailing !== "none") return {kind: "server-only", reason: "a detailing engine is selected"};
            if (task.cadEquipment) return {kind: "server-only", reason: "the IFC splices linked CAD equipment"};
        }
        return {kind: "browser", engine: null, engineId: null};
    }
    // export_xlsx / import_xlsx
    if (isDefault(slug)) return {kind: "browser", engine: null, engineId: null};
    const listed = engines.find((e) => e.slug === slug);
    if (listed?.origin !== "builtin" && bakedEngineWheel(manifest, slug)) {
        return {kind: "browser", engine: slug as string, engineId: slug as string};
    }
    if (!listed || listed.origin === "builtin") return {kind: "browser", engine: slug as string, engineId: null};
    return {kind: "server-only", reason: "an engine with no baked browser wheel"};
}

/** The execution plan for a task: the policy's "procedural_io" order for its engine,
 * or the worker alone (unjudged) when the browser cannot run it. */
export function planProceduralIo(task: ProceduralIoTask, route: ProceduralIoRoute = proceduralIoRoute(task)): ExecutionPlan {
    // The server judges relocations and IFC/Genie exports with no engine (their jobs
    // carry none), so they are looked up as "*" here too.
    const engineScoped = task.action === "export_xlsx" || task.action === "import_xlsx";
    const qualifier = engineScoped ? proceduralIoQualifier(task.slug) : "*";
    return planExecution("procedural_io", qualifier, (e) => e === "pyodide" && route.kind === "browser");
}

/** Run a task by its plan: `browser` on Pyodide, `worker` on the server worker pool
 * (`explicit`: the worker was chosen over the browser, sent as X-Ada-Run-On). */
export async function runProceduralIo<T>(
    task: ProceduralIoTask,
    runners: {
        browser: (route: Extract<ProceduralIoRoute, {kind: "browser"}>) => Promise<T>;
        worker: (explicit: boolean, ctx: AttemptContext) => Promise<T>;
    },
    opts: {log?: (msg: string) => void} = {},
): Promise<FallbackResult<T>> {
    const route = proceduralIoRoute(task);
    const plan = planProceduralIo(task, route);
    return runWithFallback(
        plan,
        {
            ...(route.kind === "browser" ? {pyodide: () => runners.browser(route)} : {}),
            worker: (ctx) => runners.worker(ctx.explicitWorker, ctx),
        },
        {label: `procedural_io ${task.action} ${plan.qualifier}`, log: opts.log},
    );
}

/** An equipment type as the builder lists it (a subset of ProceduralTypeOption). */
export interface ListedEquipmentType {
    slug: string;
    origin: string;
    id?: string;
    has_cad?: boolean;
}

/** The catalog equipment types a document places (by its equipment's DESCRIPTION),
 * the ones the server resolves from its database. */
export function placedCatalogTypes(
    doc: {equipments?: readonly {DESCRIPTION?: unknown}[] | null} | null | undefined,
    types: readonly ListedEquipmentType[],
): ListedEquipmentType[] {
    const used = new Set(
        (doc?.equipments ?? []).map((e) => String(e?.DESCRIPTION ?? "").trim()).filter((s) => s.length > 0),
    );
    return types.filter((t) => t.origin === "catalog" && t.id && used.has(t.slug));
}

/** `{slug: catalog doc}` for the catalog types a document places. */
export async function catalogEquipmentDocs(
    placed: readonly ListedEquipmentType[],
    fetchDoc: (typeId: string) => Promise<unknown>,
): Promise<Record<string, unknown>> {
    const out: Record<string, unknown> = {};
    await Promise.all(
        placed.map(async (t) => {
            out[t.slug] = await fetchDoc(t.id as string);
        }),
    );
    return out;
}

/** Hand bytes to the user as a file download. */
export function downloadBytes(bytes: Uint8Array, filename: string, mime = "application/octet-stream"): void {
    const blob = new Blob([bytes as BlobPart], {type: mime});
    const url = URL.createObjectURL(blob);
    try {
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.style.display = "none";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    } finally {
        // Revoked on the next task: some browsers start the download asynchronously.
        setTimeout(() => URL.revokeObjectURL(url), 0);
    }
}

export const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
