import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { authApi, type MeResponse, type WorkerAccessPolicy } from "@/services/api/auth";
import {
  hasAnyWorkerAccess,
  hasGlobalWorkerAccess,
  loadWorkerAccess,
  serverOnlyNotice,
  useWorkerAccessStore,
} from "@/services/compute/workerAccess";
import { useExecutionPolicyStore } from "@/state/executionPolicyStore";

const NONE: WorkerAccessPolicy = { enabled: false, allowed_capabilities: [], effective_all: false };
const ABAQUS_ONLY: WorkerAccessPolicy = { enabled: false, allowed_capabilities: ["abaqus"], effective_all: false };
const ADMIN: WorkerAccessPolicy = { enabled: false, allowed_capabilities: [], effective_all: true };

describe("worker access", () => {
  afterEach(() => {
    useWorkerAccessStore.setState({ policy: null, loaded: false });
    useExecutionPolicyStore.setState({ policy: null });
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

  it("stores the execution policy /api/me returns alongside the worker access", async () => {
    const original = authApi.me;
    authApi.me = async () =>
      ({
        worker_access: ABAQUS_ONLY,
        execution_policy: { read: { "*": ["worker"] } },
      }) as unknown as MeResponse;
    try {
      const policy = await loadWorkerAccess(true);
      assert.deepEqual(policy, ABAQUS_ONLY);
      assert.deepEqual(useExecutionPolicyStore.getState().policy, { read: { "*": ["worker"] } });
    } finally {
      authApi.me = original;
    }
  });
});
