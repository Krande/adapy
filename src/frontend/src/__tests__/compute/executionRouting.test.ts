// Every routing path goes through the execution-policy resolver, not a
// hard-coded order. The pipelines need a browser (Pyodide, embind workers), so
// the plans are checked by behaviour and the call sites at the source, as
// builderBrowserFirst.test.ts does for the builder.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { afterEach, describe, it } from "node:test";

// conversion/index.ts reaches services/auth/oidc.ts (sessionStorage at module load).
const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
(globalThis as unknown as { sessionStorage: unknown; localStorage: unknown }).sessionStorage = storage;
(globalThis as unknown as { sessionStorage: unknown; localStorage: unknown }).localStorage = storage;

const { conversionPlan, feaBakePlan } = await import("@/services/conversion");
const { useWorkerAccessStore } = await import("@/services/compute/workerAccess");
const { useExecutionPolicyStore, useExecutionPreferenceStore } = await import("@/state/executionPolicyStore");

const read = (rel: string): string => readFileSync(new URL(`../../${rel}`, import.meta.url), "utf8");
const GLOBAL = { enabled: true, allowed_capabilities: [], effective_all: true };

afterEach(() => {
  useWorkerAccessStore.setState({ policy: null, loaded: false });
  useExecutionPolicyStore.setState({ policy: null });
  useExecutionPreferenceStore.setState({ preferred: {} });
});

describe("conversion and bake plans", () => {
  it("opens STEP/IFC native first, then Pyodide, then (with access) the worker", () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    assert.deepEqual(conversionPlan("read", "m/model.ifc", "glb").engines, ["native", "pyodide", "worker"]);
    assert.deepEqual(conversionPlan("read", "m/model.STEP", "glb").engines, ["native", "pyodide", "worker"]);
    // The native module only does -> GLB; a forced streaming-STEP request skips it.
    assert.deepEqual(conversionPlan("convert", "m/model.ifc", "step").engines, ["pyodide", "worker"]);
    assert.deepEqual(conversionPlan("read", "m/model.step", "glb", { nativeOff: true }).engines, ["pyodide", "worker"]);
    // Without worker access the worker is not allowed for browser-capable work.
    useWorkerAccessStore.setState({ policy: null, loaded: true });
    assert.deepEqual(conversionPlan("read", "m/model.obj", "glb").engines, ["pyodide"]);
  });

  it("sends what no browser engine reads to the worker for everyone, unjudged", () => {
    const plan = conversionPlan("read", "results/job.odb", "glb");
    assert.deepEqual(plan.engines, ["worker"]);
    assert.equal(plan.browserPath, false);
    const bake = feaBakePlan("results/job.odb");
    assert.deepEqual([bake.engines, bake.browserPath], [["worker"], false]);
    assert.deepEqual(feaBakePlan("results/job.rmed").engines, ["pyodide"]);
  });

  it("follows an administrator's order and the user's preference", () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    useExecutionPolicyStore.setState({ policy: { read: { "*": ["pyodide", "worker"], ".ifc": ["pyodide"] } } });
    assert.deepEqual(conversionPlan("read", "m/model.ifc", "glb").engines, ["pyodide"]);
    useExecutionPreferenceStore.getState().setPreferred("read", "worker");
    assert.deepEqual(conversionPlan("read", "m/model.obj", "glb").engines, ["worker", "pyodide"]);
    assert.deepEqual(conversionPlan("read", "m/model.ifc", "glb").engines, ["pyodide"]);
  });
});

describe("routing call sites", () => {
  it("conversions and bakes run through runWithFallback with the policy's plan", () => {
    const src = read("services/conversion/index.ts");
    for (const fn of ["ensureConverted", "convertWithSelection", "ensureBakedFeaManifest"]) {
      const start = src.indexOf(`export async function ${fn}(`);
      assert.ok(start >= 0, fn);
      const rest = src.slice(start);
      const body = rest.slice(0, rest.search(/\r?\n\}\r?\n/));
      assert.match(body, /runWithFallback\(/, `${fn} falls back through the resolver`);
    }
    assert.doesNotMatch(src, /workerPreferred\(\)/);
    // The worker runner sends the explicit-choice header only when a browser engine could have run it.
    assert.match(src, /runOnWorker: ctx\.explicitWorker/);
    assert.match(src, /operation,\r?\n/);
  });

  it("the FEA viewer bakes through the resolver", () => {
    const src = read("utils/scene/fea/streaming/loadWithDefaults.ts");
    assert.match(src, /planExecution\("fea_bake"/);
    assert.match(src, /runWithFallback\(/);
    assert.doesNotMatch(src, /workerPreferred/);
  });

  it("the serializer default and plugin jobs consult the resolver", () => {
    assert.match(read("services/conversion/serializerMatrix.ts"), /workerPreferred\("convert", ext \|\| "\*"\)/);
    const router = read("services/compute/pluginJobRouter.ts");
    assert.match(router, /engineOrder\("plugin_job", params\.pluginId/);
    assert.doesNotMatch(read("plugins/context.ts"), /workerPreferred/);
  });

  it("nothing reads the retired binary engine setting", () => {
    for (const rel of [
      "services/compute/workerAccess.ts",
      "components/rest_mode/RestModeUI.tsx",
      "layout/optionCommands.ts",
      "state/AdaViewerContext.tsx",
    ]) {
      assert.doesNotMatch(read(rel), /conversionEngineStore|useConversionEngineStore/, rel);
    }
  });
});
