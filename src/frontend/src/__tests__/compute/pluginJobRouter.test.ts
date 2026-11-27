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
    assert.deepEqual(allowed, { target: "worker" });
  });

  it("handles server-only workloads (.odb / abaqus) with clear denial vs capability override", () => {
    const denied = decideExecutionTarget({
      wasmCapable: false,
      workerAccess: { enabled: false, allowed_capabilities: [], effective_all: false },
      capability: "abaqus",
    });
    assert.equal(denied.target, "denied");
    if (denied.target === "denied") {
      assert.equal(denied.capability, "abaqus");
      assert.match(denied.reason, /abaqus/);
    }

    const allowed = decideExecutionTarget({
      wasmCapable: false,
      workerAccess: { enabled: false, allowed_capabilities: ["abaqus"], effective_all: false },
      capability: "abaqus",
    });
    assert.deepEqual(allowed, { target: "worker" });
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
