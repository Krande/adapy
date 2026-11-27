import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  canUseWorker,
  decideExecutionTarget,
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
