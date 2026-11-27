import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import type { WorkerAccessPolicy } from "@/services/api/auth";
import {
  hasAnyWorkerAccess,
  hasGlobalWorkerAccess,
  serverOnlyNotice,
  useWorkerAccessStore,
  workerPreferred,
} from "@/services/compute/workerAccess";
import { useConversionEngineStore } from "@/state/conversionEngineStore";

const NONE: WorkerAccessPolicy = { enabled: false, allowed_capabilities: [], effective_all: false };
const ABAQUS_ONLY: WorkerAccessPolicy = { enabled: false, allowed_capabilities: ["abaqus"], effective_all: false };
const GLOBAL: WorkerAccessPolicy = { enabled: true, allowed_capabilities: [], effective_all: true };
const ADMIN: WorkerAccessPolicy = { enabled: false, allowed_capabilities: [], effective_all: true };

describe("browser-first engine, opt-in worker", () => {
  afterEach(() => {
    useConversionEngineStore.setState({ engine: "browser" });
    useWorkerAccessStore.setState({ policy: null, loaded: false });
  });

  it("defaults to the browser engine", () => {
    assert.equal(useConversionEngineStore.getState().engine, "browser");
    for (const p of [null, NONE, ABAQUS_ONLY, GLOBAL, ADMIN]) {
      assert.equal(workerPreferred(p), false);
    }
  });

  it("uses the worker only when opted in AND globally granted", () => {
    useConversionEngineStore.setState({ engine: "worker" });
    assert.equal(workerPreferred(null), false);
    assert.equal(workerPreferred(NONE), false);
    // A capability grant is for the server-only workload it names, not for
    // moving browser-capable conversions off the browser.
    assert.equal(workerPreferred(ABAQUS_ONLY), false);
    assert.equal(workerPreferred(GLOBAL), true);
    assert.equal(workerPreferred(ADMIN), true);
  });

  it("reads the stored policy when none is passed", () => {
    useConversionEngineStore.setState({ engine: "worker" });
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    assert.equal(workerPreferred(), true);
    useWorkerAccessStore.setState({ policy: NONE, loaded: true });
    assert.equal(workerPreferred(), false);
  });

  it("distinguishes global from any access", () => {
    assert.equal(hasGlobalWorkerAccess(ABAQUS_ONLY), false);
    assert.equal(hasAnyWorkerAccess(ABAQUS_ONLY), true);
    assert.equal(hasAnyWorkerAccess(NONE), false);
    assert.equal(hasAnyWorkerAccess(null), false);
    assert.equal(hasAnyWorkerAccess(ADMIN), true);
  });

  it("names the workload and the grant in the server-only notice", () => {
    const msg = serverOnlyNotice("Opening .odb files", "abaqus");
    assert.match(msg, /Opening \.odb files runs on the server worker pool/);
    assert.match(msg, /'abaqus'/);
    assert.match(msg, /Admin Panel/);
  });
});
