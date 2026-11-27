// The execution policy resolver: which engine runs an operation, in what order
// (services/compute/executionPolicy.ts), on top of the pure rules it shares with
// the server (executionPolicyModel.ts / ada.comms.rest.execution_policy).
import assert from "node:assert/strict";
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

const model = await import("@/services/compute/executionPolicyModel");
const ep = await import("@/services/compute/executionPolicy");
const { useExecutionPolicyStore, useExecutionPreferenceStore, legacyPreferences } = await import(
  "@/state/executionPolicyStore"
);
const { useWorkerAccessStore } = await import("@/services/compute/workerAccess");

type Access = { enabled: boolean; allowed_capabilities: string[]; effective_all: boolean };
const NONE: Access = { enabled: false, allowed_capabilities: [], effective_all: false };
const GLOBAL: Access = { enabled: true, allowed_capabilities: [], effective_all: true };
const CAP: Access = { enabled: false, allowed_capabilities: ["example-cap"], effective_all: false };

afterEach(() => {
  useExecutionPolicyStore.setState({ policy: null });
  useExecutionPreferenceStore.setState({ preferred: {} });
  useWorkerAccessStore.setState({ policy: null, loaded: false });
});

describe("policy model", () => {
  it("defaults to today's routing, the same table as the server", () => {
    const d = model.defaultExecutionPolicy();
    assert.deepEqual(Object.keys(d), [...model.EXEC_OPERATIONS]);
    for (const op of ["convert", "read"] as const) {
      assert.deepEqual(d[op]["*"], ["pyodide", "worker"]);
      for (const ext of [".ifc", ".step", ".stp"]) assert.deepEqual(d[op][ext], ["native", "pyodide", "worker"]);
    }
    for (const op of ["fea_bake", "procedural_compile", "plugin_job", "procedural_io", "asset_build", "core_job"] as const) {
      assert.deepEqual(d[op], { "*": ["pyodide", "worker"] });
    }
    d.convert["*"].push("native");
    assert.deepEqual(model.DEFAULT_EXECUTION_POLICY.convert["*"], ["pyodide", "worker"]);
  });

  it("validates like the server", () => {
    assert.deepEqual(model.validationErrors(model.defaultExecutionPolicy()), []);
    const errs = model.validationErrors({
      convert: { ifc: ["worker"], ".IFC": ["native"], ".ifc": ["native"], "*": [], ".obj": ["gpu" as never] },
      plugin_job: { "*": ["worker", "worker"], "bad id": ["pyodide"] },
    });
    assert.ok(errs.some((e) => /source extension/.test(e)));
    assert.ok(errs.some((e) => /more than once/.test(e)));
    assert.ok(errs.some((e) => /at least one engine/.test(e)));
    assert.ok(errs.some((e) => /listed twice/.test(e)));
    assert.ok(errs.some((e) => /plugin id/.test(e)));
    assert.ok(errs.some((e) => /unknown engine/.test(e)));
  });

  it("completes, merges and looks up per row", () => {
    const full = model.completePolicy({ read: { ".ifc": ["worker"] } });
    assert.deepEqual(full.read, { ".ifc": ["worker"], "*": ["pyodide", "worker"] });
    const eff = model.mergePolicy(full, { read: { "*": ["pyodide"] } });
    assert.deepEqual(model.lookupOrder(eff, "read", ".IFC"), ["worker"]);
    assert.deepEqual(model.lookupOrder(eff, "read", ".obj"), ["pyodide"]);
    assert.deepEqual(model.lookupOrder(eff, "read", null), ["pyodide"]);
    assert.deepEqual(model.lookupOrder({}, "read", ".obj"), []);
  });

  it("moves only an allowed preference to the front", () => {
    assert.deepEqual(model.applyPreference(["native", "pyodide", "worker"], "worker"), ["worker", "native", "pyodide"]);
    assert.deepEqual(model.applyPreference(["pyodide"], "worker"), ["pyodide"]);
    assert.deepEqual(model.applyPreference(["pyodide", "worker"], null), ["pyodide", "worker"]);
  });

  it("takes the qualifier from the source's extension", () => {
    assert.equal(model.sourceQualifier("a/b/Model.IFC"), ".ifc");
    assert.equal(model.sourceQualifier("dir.v2/noext"), "*");
    assert.equal(model.sourceQualifier("x.step"), ".step");
  });
});

describe("resolver", () => {
  it("drops the worker without worker access, whatever the order says", () => {
    useExecutionPolicyStore.setState({ policy: { convert: { "*": ["worker", "pyodide"] } } });
    assert.deepEqual(ep.allowedEngines("convert", ".obj", { workerAccess: NONE }), ["pyodide"]);
    assert.deepEqual(ep.allowedEngines("convert", ".obj", { workerAccess: GLOBAL }), ["worker", "pyodide"]);
    // A per-capability grant moves browser-capable work only for the capability it names.
    assert.deepEqual(ep.allowedEngines("plugin_job", "x", { workerAccess: CAP, capability: "example-cap", policy: { plugin_job: { "*": ["worker"] } } }), ["worker"]);
    assert.deepEqual(ep.allowedEngines("convert", ".obj", { workerAccess: CAP }), ["pyodide"]);
  });

  it("uses the defaults until /api/me answered", () => {
    assert.deepEqual(ep.engineOrder("read", ".step", { workerAccess: GLOBAL }), ["native", "pyodide", "worker"]);
    assert.deepEqual(ep.engineOrder("read", ".obj", { workerAccess: NONE }), ["pyodide"]);
  });

  it("puts the user's preferred engine first, only among the allowed ones", () => {
    useExecutionPreferenceStore.getState().setPreferred("read", "worker");
    assert.deepEqual(ep.engineOrder("read", ".ifc", { workerAccess: GLOBAL }), ["worker", "native", "pyodide"]);
    // No access: the preference names an engine that is not allowed and is ignored.
    assert.deepEqual(ep.engineOrder("read", ".ifc", { workerAccess: NONE }), ["native", "pyodide"]);
    useExecutionPreferenceStore.getState().setPreferred("read", "pyodide");
    assert.deepEqual(ep.engineOrder("read", ".ifc", { workerAccess: NONE }), ["pyodide", "native"]);
    useExecutionPreferenceStore.getState().setPreferred("read", null);
    assert.deepEqual(useExecutionPreferenceStore.getState().preferred, {});
  });

  it("skips engines that cannot handle the input", () => {
    const supports = (e: string) => e !== "native";
    assert.deepEqual(ep.engineOrder("read", ".ifc", { workerAccess: GLOBAL, supports }), ["pyodide", "worker"]);
  });

  it("plans a workload with no browser path as the worker, unjudged", () => {
    const plan = ep.planExecution("fea_bake", ".odb", (e) => e === "worker", { workerAccess: NONE });
    assert.deepEqual(plan, { op: "fea_bake", qualifier: ".odb", engines: ["worker"], browserPath: false });
    const browser = ep.planExecution("fea_bake", ".rmed", (e) => e !== "native", { workerAccess: GLOBAL });
    assert.deepEqual(browser.engines, ["pyodide", "worker"]);
    assert.equal(browser.browserPath, true);
    // Nothing allowed that can run it.
    const empty = ep.planExecution("read", ".obj", (e) => e !== "native", {
      workerAccess: NONE,
      policy: { read: { "*": ["native", "worker"] } },
    });
    assert.deepEqual(empty.engines, []);
  });

  it("derives workerPreferred from the order", () => {
    assert.equal(ep.workerPreferred("convert", "*", { workerAccess: GLOBAL }), false);
    useExecutionPreferenceStore.getState().setPreferred("convert", "worker");
    assert.equal(ep.workerPreferred("convert", "*", { workerAccess: GLOBAL }), true);
    assert.equal(ep.workerPreferred("convert", "*", { workerAccess: NONE }), false);
    // An admin can put the worker first for a qualifier.
    useExecutionPreferenceStore.getState().setPreferred("convert", null);
    useExecutionPolicyStore.setState({ policy: { convert: { "*": ["pyodide"], ".obj": ["worker", "pyodide"] } } });
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    assert.equal(ep.workerPreferred("convert", ".obj"), true);
    assert.equal(ep.workerPreferred("convert", ".stl"), false);
  });

  it("turns the old binary setting into a worker preference", () => {
    memory.set("ada-conversion-engine", JSON.stringify({ state: { engine: "worker" }, version: 0 }));
    const prefs = legacyPreferences();
    for (const op of model.EXEC_OPERATIONS) assert.equal(prefs[op], "worker");
    memory.set("ada-conversion-engine", JSON.stringify({ state: { engine: "browser" }, version: 0 }));
    assert.deepEqual(legacyPreferences(), {});
    memory.delete("ada-conversion-engine");
    assert.deepEqual(legacyPreferences(), {});
  });
});

describe("runWithFallback", () => {
  const plan = (engines: Array<"native" | "pyodide" | "worker">, browserPath = true) => ({
    op: "read" as const,
    qualifier: ".ifc",
    engines,
    browserPath,
  });

  it("runs the first engine that succeeds and records why it fell back", async () => {
    const logs: string[] = [];
    const seen: Array<{ engine: string; explicit: boolean; fallbacks: number }> = [];
    const res = await ep.runWithFallback(
      plan(["native", "pyodide", "worker"]),
      {
        native: async (ctx) => {
          seen.push({ engine: ctx.engine, explicit: ctx.explicitWorker, fallbacks: ctx.fallbacks.length });
          throw new Error("module missing");
        },
        pyodide: async (ctx) => {
          seen.push({ engine: ctx.engine, explicit: ctx.explicitWorker, fallbacks: ctx.fallbacks.length });
          return "derived.glb";
        },
        worker: async () => "never",
      },
      { log: (m) => logs.push(m) },
    );
    assert.deepEqual(res, { value: "derived.glb", engine: "pyodide", fallbacks: [{ engine: "native", reason: "module missing" }] });
    assert.deepEqual(seen, [
      { engine: "native", explicit: false, fallbacks: 0 },
      { engine: "pyodide", explicit: false, fallbacks: 1 },
    ]);
    assert.match(logs.join("\n"), /Native wasm failed \(module missing\); trying Pyodide/);
    assert.match(logs.join("\n"), /ran on Pyodide after Native wasm \(module missing\)/);
    assert.deepEqual(ep.executionAudit({ engine: "pyodide", explicitWorker: false, fallbacks: res.fallbacks }), {
      engine: "pyodide",
      fallbacks: [{ engine: "native", reason: "module missing" }],
    });
  });

  it("marks the worker explicit only when a browser engine could have run it", async () => {
    const flags: boolean[] = [];
    const worker = async (ctx: { explicitWorker: boolean }) => {
      flags.push(ctx.explicitWorker);
      return 1;
    };
    await ep.runWithFallback(plan(["worker"], true), { worker }, { log: () => {} });
    await ep.runWithFallback(plan(["worker"], false), { worker }, { log: () => {} });
    assert.deepEqual(flags, [true, false]);
  });

  it("rethrows the last failure, stops on an abort, refuses an empty plan", async () => {
    await assert.rejects(
      ep.runWithFallback(
        plan(["pyodide", "worker"]),
        {
          pyodide: async () => {
            throw new Error("first");
          },
          worker: async () => {
            throw new Error("403 not allowed");
          },
        },
        { log: () => {} },
      ),
      /403 not allowed/,
    );
    let workerRan = false;
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    await assert.rejects(
      ep.runWithFallback(
        plan(["pyodide", "worker"]),
        {
          pyodide: async () => {
            throw abort;
          },
          worker: async () => {
            workerRan = true;
            return 1;
          },
        },
        { log: () => {} },
      ),
      /aborted/,
    );
    assert.equal(workerRan, false);
    await assert.rejects(ep.runWithFallback(plan([]), {}, { log: () => {} }), ep.NoAllowedEngineError);
    // An engine without a runner is skipped.
    const res = await ep.runWithFallback(plan(["native", "pyodide"]), { pyodide: async () => "ok" }, { log: () => {} });
    assert.equal(res.engine, "pyodide");
  });
});
