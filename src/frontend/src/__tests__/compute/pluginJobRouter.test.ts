import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  canUseWorker,
  decideExecutionTarget,
  dispatchPluginJob,
  isPluginWasmCapable,
  loadPluginWheelsManifest,
} from "@/services/compute/pluginJobRouter";

describe("pluginJobRouter worker access & browser-first routing", () => {
  it("evaluates canUseWorker identically to backend worker_access policy", () => {
    assert.equal(canUseWorker(undefined, "abaqus"), false);
    assert.equal(
      canUseWorker({ enabled: false, allowed_capabilities: [], effective_all: false }, "abaqus"),
      false,
    );
    assert.equal(
      canUseWorker({ enabled: false, allowed_capabilities: ["abaqus"], effective_all: false }, "ABAQUS"),
      true,
    );
    assert.equal(
      canUseWorker({ enabled: false, allowed_capabilities: ["abaqus"], effective_all: false }, "batch"),
      false,
    );
    assert.equal(
      canUseWorker({ enabled: true, allowed_capabilities: [], effective_all: true }, "anything"),
      true,
    );
    assert.equal(
      canUseWorker({ enabled: false, allowed_capabilities: [], effective_all: true }, "anything"),
      true,
    );
  });

  it("defaults WASM-capable plugins to browser even when worker access is OFF", () => {
    const decision = decideExecutionTarget({
      wasmCapable: true,
      workerAccess: { enabled: false, allowed_capabilities: [], effective_all: false },
      capability: "batch",
    });
    assert.deepEqual(decision, { target: "browser" });
  });

  it("routes explicit preferWorker to worker only when permitted, otherwise denied", () => {
    const denied = decideExecutionTarget({
      wasmCapable: true,
      workerAccess: { enabled: false, allowed_capabilities: [], effective_all: false },
      capability: "batch",
      preferWorker: true,
    });
    assert.equal(denied.target, "denied");
    if (denied.target === "denied") {
      assert.match(denied.reason, /batch/);
    }

    const allowed = decideExecutionTarget({
      wasmCapable: true,
      workerAccess: { enabled: true, allowed_capabilities: [], effective_all: true },
      capability: "batch",
      preferWorker: true,
    });
    // Marked explicit: the request carries X-Ada-Run-On and the server judges it.
    assert.deepEqual(allowed, { target: "worker", explicit: true });
  });

  it("runs a workload with no browser path on the worker for everyone, unjudged", () => {
    // Nothing to choose between, so no access is needed and nothing is marked
    // explicit -- with or without preferWorker.
    for (const preferWorker of [false, true]) {
      const decision = decideExecutionTarget({
        wasmCapable: false,
        workerAccess: { enabled: false, allowed_capabilities: [], effective_all: false },
        capability: "abaqus",
        preferWorker,
      });
      assert.deepEqual(decision, { target: "worker" });
    }
  });

  it("follows the execution policy's order", () => {
    const GLOBAL = { enabled: true, allowed_capabilities: [], effective_all: true };
    const NONE = { enabled: false, allowed_capabilities: [], effective_all: false };
    // Worker first in the policy (and allowed): an explicit worker choice.
    assert.deepEqual(
      decideExecutionTarget({ wasmCapable: true, workerAccess: GLOBAL, order: ["worker", "pyodide"] }),
      { target: "worker", explicit: true },
    );
    // Worker first but no access: the browser, the next allowed engine.
    assert.deepEqual(
      decideExecutionTarget({ wasmCapable: true, workerAccess: NONE, order: ["worker", "pyodide"] }),
      { target: "browser" },
    );
    // "Run on worker" against a policy without the worker is refused, naming the policy.
    const refused = decideExecutionTarget({
      wasmCapable: true,
      workerAccess: GLOBAL,
      preferWorker: true,
      order: ["pyodide"],
    });
    assert.equal(refused.target, "denied");
    if (refused.target === "denied") assert.match(refused.reason, /execution policy/);
    // Nothing allowed that runs a plugin.
    const none = decideExecutionTarget({ wasmCapable: true, workerAccess: NONE, order: ["native", "worker"] });
    assert.equal(none.target, "denied");
    // No browser path: still the worker, unjudged, whatever the order says.
    assert.deepEqual(decideExecutionTarget({ wasmCapable: false, workerAccess: NONE, order: ["pyodide"] }), {
      target: "worker",
    });
  });

  it("falls back from a failed browser run to the worker when the policy lists it next", async () => {
    // runtime.apiBase() reads window; a bare node process has none.
    (globalThis as unknown as { window?: unknown }).window ??= {};
    const manifestFetch = (async () =>
      new Response(JSON.stringify({ plugins: { "example-plugin": { wheel: "example_plugin-1.0-py3-none-any.whl" } } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })) as typeof fetch;
    await loadPluginWheelsManifest(manifestFetch, true);
    const submitted: Array<{ runOnWorker?: boolean }> = [];
    const submitToWorker = (async (_id: string, _body: unknown, opts?: { runOnWorker?: boolean }) => {
      submitted.push({ runOnWorker: opts?.runOnWorker });
      return { job_id: "job-1", derived_key: "out.json" };
    }) as never;
    const failing = (async () => {
      throw new Error("wheel failed to import");
    }) as never;
    const logs: string[] = [];
    const out = await dispatchPluginJob({
      pluginId: "example-plugin",
      scope: "shared" as never,
      options: {},
      workerAccess: { enabled: true, allowed_capabilities: [], effective_all: true },
      order: ["pyodide", "worker"],
      runInBrowser: failing,
      submitToWorker,
      onLog: (m) => logs.push(m),
    });
    assert.equal(out.execution, "worker");
    assert.deepEqual(submitted, [{ runOnWorker: true }]);
    assert.match(logs.join("\n"), /Pyodide failed \(wheel failed to import\); trying Server worker/);

    // Without the worker in the order (or without access) the browser failure surfaces.
    await assert.rejects(
      dispatchPluginJob({
        pluginId: "example-plugin",
        scope: "shared" as never,
        options: {},
        workerAccess: { enabled: false, allowed_capabilities: [], effective_all: false },
        order: ["pyodide", "worker"],
        runInBrowser: failing,
        submitToWorker,
      }),
      /wheel failed to import/,
    );
    assert.equal(submitted.length, 1);
  });

  it("loads plugin-wheels-manifest.json and reports WASM capability", async () => {
    const fakeFetch = (async () =>
      new Response(
        JSON.stringify({
          plugins: {
            "demo-plugin": {
              wheel: "demo_plugin-1.0.0-py3-none-any.whl",
              entrypoint: "demo_plugin.job:run",
              deps: ["adapy"],
            },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )) as typeof fetch;

    const manifest = await loadPluginWheelsManifest(fakeFetch, true);
    assert.ok(manifest.plugins["demo-plugin"]);
    assert.equal(await isPluginWasmCapable("demo-plugin", fakeFetch), true);
    assert.equal(await isPluginWasmCapable("unknown-plugin", fakeFetch), false);
  });
});
