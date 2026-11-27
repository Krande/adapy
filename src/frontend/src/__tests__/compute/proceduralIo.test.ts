// Where a procedural model's Excel export / import, relocation proposals and
// IFC/Genie export run (services/compute/proceduralIo): the execution policy's
// "procedural_io" order, the browser first by default and the worker as the
// fallback; a task the browser cannot run goes to the worker unjudged.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, it } from "node:test";

// The preference store persists to localStorage; a bare node process has none.
const memory = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  getItem: (k: string) => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string) => void memory.set(k, String(v)),
  removeItem: (k: string) => void memory.delete(k),
  clear: () => memory.clear(),
  key: () => null,
  get length() {
    return memory.size;
  },
} as Storage;

const io = await import("@/services/compute/proceduralIo");
const model = await import("@/services/compute/executionPolicyModel");
const editor = await import("@/components/admin/execution/executionPolicyEditor");
const { useExecutionPolicyStore, useExecutionPreferenceStore } = await import("@/state/executionPolicyStore");
const { useWorkerAccessStore } = await import("@/services/compute/workerAccess");

import type { ListedEngine } from "@/services/compute/proceduralBrowserEngine";
import type { ProceduralIoAction, ProceduralIoTask } from "@/services/compute/proceduralIo";
import type { PluginWheelsManifest } from "@/utils/pyodide/pluginWheels";

const GLOBAL = { enabled: true, allowed_capabilities: [], effective_all: true };
const NONE = { enabled: false, allowed_capabilities: [], effective_all: false };

const manifest: PluginWheelsManifest = {
  plugins: {
    "wheel-engine": { wheel: "wheel_engine-1.0-py3-none-any.whl", register: "wheel_engine.register", deps: ["adapy"] },
    "wheel-job": { wheel: "wheel_job-1.0-py3-none-any.whl", entrypoint: "wheel_job:run" },
  },
};
const engines: ListedEngine[] = [
  { id: "builtin:adapy-default", slug: "adapy-default", origin: "builtin" },
  { id: "builtin:echo", slug: "echo", origin: "builtin" },
  { id: "worker:wheel-engine", slug: "wheel-engine", origin: "worker" },
  { id: "worker:other-engine", slug: "other-engine", origin: "worker" },
  { id: "db-1", slug: "db-engine", origin: "db" },
];

const task = (
  action: ProceduralIoAction,
  over: Partial<ProceduralIoTask> = {},
): ProceduralIoTask => ({ action, slug: null, engines, manifest, ...over });

afterEach(() => {
  useExecutionPolicyStore.setState({ policy: null });
  useExecutionPreferenceStore.setState({ preferred: {} });
  useWorkerAccessStore.setState({ policy: null, loaded: false });
});

describe("the procedural_io operation", () => {
  it("is in the policy: browser first, worker after, keyed by engine slug", () => {
    assert.ok(model.EXEC_OPERATIONS.includes("procedural_io"));
    assert.deepEqual(model.defaultExecutionPolicy().procedural_io, { "*": ["pyodide", "worker"] });
    assert.equal(model.QUALIFIER_HINTS.procedural_io, "engine slug");
    assert.ok(model.OPERATION_LABELS.procedural_io.length > 0);
    const bad = model.normaliseQualifier("procedural_io", "bad slug");
    assert.ok("error" in bad && /engine slug/.test(bad.error));
  });

  it("is a row of the Admin > Execution editor, and takes engine-slug rows", () => {
    const rows = editor.rowsFromPolicy(model.defaultExecutionPolicy());
    assert.deepEqual(
      rows.filter((r) => r.op === "procedural_io").map((r) => [r.qualifier, r.engines]),
      [["*", ["pyodide", "worker"]]],
    );
    const added = editor.addRow(rows, "procedural_io", "Wheel-Engine", ["worker"]);
    assert.ok("rows" in added);
    if ("rows" in added) assert.deepEqual(editor.policyFromRows(added.rows).procedural_io?.["wheel-engine"], ["worker"]);
  });

  it("is offered by Settings > Execution engine and the option commands (they iterate the list)", () => {
    const root = path.resolve(import.meta.dirname, "..", "..");
    for (const rel of ["components/options/ExecutionEngineOptions.tsx", "layout/optionCommands.ts"]) {
      assert.match(fs.readFileSync(path.join(root, rel), "utf8"), /EXEC_OPERATIONS\.map\(/, rel);
    }
  });
});

describe("proceduralIoRoute", () => {
  it("runs the default engine's workbook in the browser, and a built-in engine by slug", () => {
    for (const slug of [null, "", "adapy-default"]) {
      assert.deepEqual(io.proceduralIoRoute(task("export_xlsx", { slug })), { kind: "browser", engine: null, engineId: null });
    }
    assert.deepEqual(io.proceduralIoRoute(task("import_xlsx", { slug: "echo" })), {
      kind: "browser",
      engine: "echo",
      engineId: null,
    });
  });

  it("installs the baked plugin that registers a worker-advertised engine", () => {
    for (const action of ["export_xlsx", "import_xlsx"] as const) {
      assert.deepEqual(io.proceduralIoRoute(task(action, { slug: "wheel-engine" })), {
        kind: "browser",
        engine: "wheel-engine",
        engineId: "wheel-engine",
      });
    }
  });

  it("leaves an engine with no baked browser wheel to the worker", () => {
    for (const slug of ["other-engine", "db-engine"]) {
      assert.equal(io.proceduralIoRoute(task("export_xlsx", { slug })).kind, "server-only");
    }
    // A job-only manifest entry registers no engine.
    assert.equal(
      io.proceduralIoRoute(task("export_xlsx", { slug: "wheel-job", engines: [{ id: "w", slug: "wheel-job", origin: "worker" }] }))
        .kind,
      "server-only",
    );
  });

  it("answers relocations in the browser for any engine", () => {
    for (const slug of [null, "wheel-engine", "db-engine"]) {
      assert.equal(io.proceduralIoRoute(task("propose_relocations", { slug })).kind, "browser");
    }
  });

  it("exports IFC, Genie XML and the Genie workspace of the built-in engine in the browser", () => {
    for (const format of ["ifc", "gxml", "gnx"] as const) {
      assert.equal(io.proceduralIoRoute(task("export_model", { format })).kind, "browser", format);
    }
    assert.equal(io.proceduralIoRoute(task("export_model", { format: "gxml", slug: "wheel-engine" })).kind, "server-only");
    // IFC with linked CAD equipment or a detailing engine stays on the worker.
    assert.equal(io.proceduralIoRoute(task("export_model", { format: "ifc", cadEquipment: true })).kind, "server-only");
    assert.equal(io.proceduralIoRoute(task("export_model", { format: "ifc", detailing: "fabrication" })).kind, "server-only");
    assert.equal(io.proceduralIoRoute(task("export_model", { format: "ifc", detailing: "none" })).kind, "browser");
    assert.equal(io.proceduralIoRoute(task("export_model", { format: "gxml", cadEquipment: true })).kind, "browser");
  });
});

describe("planProceduralIo", () => {
  it("follows the policy for the engine: worker only with worker access", () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    assert.deepEqual(io.planProceduralIo(task("export_xlsx")).engines, ["pyodide", "worker"]);
    useWorkerAccessStore.setState({ policy: NONE, loaded: true });
    assert.deepEqual(io.planProceduralIo(task("export_xlsx")).engines, ["pyodide"]);
  });

  it("looks the workbook up by engine slug, relocations and IFC/Genie by '*' (as the server)", () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    useExecutionPolicyStore.setState({
      policy: model.completePolicy({ procedural_io: { "*": ["pyodide"], "wheel-engine": ["worker", "pyodide"] } }),
    });
    const xlsx = io.planProceduralIo(task("export_xlsx", { slug: "Wheel-Engine" }));
    assert.equal(xlsx.qualifier, "wheel-engine");
    assert.deepEqual(xlsx.engines, ["worker", "pyodide"]);
    const reloc = io.planProceduralIo(task("propose_relocations", { slug: "wheel-engine" }));
    assert.equal(reloc.qualifier, "*");
    assert.deepEqual(reloc.engines, ["pyodide"]);
  });

  it("sends a task the browser cannot run to the worker, unjudged", () => {
    useWorkerAccessStore.setState({ policy: NONE, loaded: true });
    const plan = io.planProceduralIo(task("export_xlsx", { slug: "db-engine" }));
    assert.deepEqual(plan.engines, ["worker"]);
    assert.equal(plan.browserPath, false);
  });
});

describe("runProceduralIo", () => {
  const quiet = { log: () => {} };

  it("runs in the browser first and never reaches the worker", async () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    let worker = 0;
    const out = await io.runProceduralIo(
      task("export_xlsx", { slug: "wheel-engine" }),
      {
        browser: async (route) => `browser:${route.engineId}`,
        worker: async () => `worker:${++worker}`,
      },
      quiet,
    );
    assert.equal(out.value, "browser:wheel-engine");
    assert.equal(out.engine, "pyodide");
    assert.equal(worker, 0);
  });

  it("falls back to the worker as an explicit choice when the browser fails", async () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    const out = await io.runProceduralIo(
      task("propose_relocations"),
      {
        browser: async () => {
          throw new Error("no wasm");
        },
        worker: async (explicit) => `worker explicit=${explicit}`,
      },
      quiet,
    );
    assert.equal(out.value, "worker explicit=true");
    assert.deepEqual(out.fallbacks, [{ engine: "pyodide", reason: "no wasm" }]);
  });

  it("does not fall back when the policy offers no worker (wasm-only)", async () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    useExecutionPolicyStore.setState({ policy: model.completePolicy({ procedural_io: { "*": ["pyodide"] } }) });
    let worker = 0;
    await assert.rejects(
      io.runProceduralIo(
        task("export_xlsx"),
        {
          browser: async () => {
            throw new Error("boom");
          },
          worker: async () => ++worker,
        },
        quiet,
      ),
      /boom/,
    );
    assert.equal(worker, 0);
  });

  it("runs a task with no browser path on the worker, not as an explicit choice", async () => {
    useWorkerAccessStore.setState({ policy: NONE, loaded: true });
    let browser = 0;
    const out = await io.runProceduralIo<string>(
      task("export_model", { format: "ifc", cadEquipment: true }),
      { browser: async () => `browser ${++browser}`, worker: async (explicit) => (explicit ? "explicit" : "unjudged") },
      quiet,
    );
    assert.equal(out.value, "unjudged");
    assert.equal(browser, 0);
  });
});

describe("catalog equipment", () => {
  const types = [
    { slug: "pump-a", origin: "catalog", id: "t1", has_cad: true },
    { slug: "tank-b", origin: "catalog", id: "t2" },
    { slug: "pump", origin: "code" },
  ];

  it("picks the catalog types a document places", () => {
    const doc = { equipments: [{ DESCRIPTION: "pump-a" }, { DESCRIPTION: "pump" }, { DESCRIPTION: " " }] };
    assert.deepEqual(
      io.placedCatalogTypes(doc, types).map((t) => t.slug),
      ["pump-a"],
    );
    assert.deepEqual(io.placedCatalogTypes(null, types), []);
  });

  it("reads each placed type's catalog doc", async () => {
    const docs = await io.catalogEquipmentDocs(io.placedCatalogTypes({ equipments: [{ DESCRIPTION: "tank-b" }] }, types), async (id) => ({
      id,
    }));
    assert.deepEqual(docs, { "tank-b": { id: "t2" } });
  });
});

describe("the builder's call sites go through the router", () => {
  const read = (rel: string) => fs.readFileSync(path.resolve(import.meta.dirname, "..", "..", rel), "utf8");

  it("exports, imports and relocations run runProceduralIo, the worker only as its runner", () => {
    const exp = read("state/cellbuilder/exportImportSlice.ts");
    assert.match(exp, /runProceduralIo</);
    assert.match(exp, /runInBrowser\(\s*"export_xlsx"/);
    assert.match(exp, /runInBrowser\("export_model"/);
    assert.match(exp, /runOnWorker: explicit/);
    const imp = read("state/cellbuilder/xlsxImport.ts");
    assert.match(imp, /runInBrowser\(\s*"import_xlsx"/);
    assert.match(imp, /createModelWithDoc\(/);
    assert.match(imp, /source_xlsx_key = sourceKey/);
    const rel = read("state/cellbuilder/relocationsSlice.ts");
    assert.match(rel, /runInBrowser\("propose_relocations"/);
    assert.match(rel, /proposeRelocations\(scope, active\.modelId, \{\s*runOnWorker: explicit/);
  });

  it("the Pyodide worker answers run-procedural-io through ada.topo_model.wasm_io", () => {
    const worker = read("utils/pyodide/pyodide_worker.js");
    assert.match(worker, /data\.type === "run-procedural-io"/);
    assert.match(worker, /import ada\.topo_model\.wasm_io as _pio/);
    assert.match(worker, /ensurePluginPlans\(engineId, plans\)/);
  });
});
