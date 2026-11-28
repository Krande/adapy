// Core jobs the browser runs (services/compute/coreJobs): the execution policy's "core_job"
// operation decides the header a request carries; a route's claim is run in Pyodide (here a fake),
// its outputs stored and the claim completed, or handed to the worker when the browser cannot run
// it and the policy lists the worker next.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, it } from "node:test";

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

const jobs = await import("@/services/compute/coreJobs");
const model = await import("@/services/compute/executionPolicyModel");
const editor = await import("@/components/admin/execution/executionPolicyEditor");
const wheels = await import("@/utils/pyodide/pluginWheels");
const { useExecutionPolicyStore, useExecutionPreferenceStore } = await import("@/state/executionPolicyStore");
const { useWorkerAccessStore } = await import("@/services/compute/workerAccess");

import type { BrowserJobClaim, CoreJobDeps, CoreJobRun } from "@/services/compute/coreJobs";

const GLOBAL = { enabled: true, allowed_capabilities: [], effective_all: true };

afterEach(() => {
  useExecutionPolicyStore.setState({ policy: null });
  useExecutionPreferenceStore.setState({ preferred: {} });
  useWorkerAccessStore.setState({ policy: null, loaded: false });
});

const claim: BrowserJobClaim = {
  id: "job-1",
  kind: "clash_check",
  family: "clash_check",
  source_key: "m.ifc",
  derived_key: "_derived/clash/a/b/result.json",
  conversion_options: { source_key: "m.ifc", options: {} },
  target_capability: null,
  scope: "project:p1",
};

function fakeDeps(run: CoreJobRun | Error, calls: string[]): CoreJobDeps {
  return {
    pyodideAvailable: () => true,
    async start(scope, id) {
      calls.push(`start ${scope} ${id}`);
      return claim;
    },
    async run() {
      calls.push("run");
      if (run instanceof Error) throw run;
      return run;
    },
    async putOutput(scope, id, name, bytes) {
      calls.push(`put ${name} ${bytes.length}`);
    },
    async complete(scope, id, body) {
      calls.push(`complete ${body.status}${body.error ? ` ${body.error}` : ""}`);
    },
    async toWorker(scope, id, reason) {
      calls.push(`to-worker ${reason}`);
      return { job_id: id };
    },
  };
}

describe("the core_job operation", () => {
  it("defaults every family to the browser first, the worker after it", () => {
    assert.deepEqual(model.defaultExecutionPolicy().core_job, { "*": ["pyodide", "worker"] });
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    for (const family of model.CORE_JOB_FAMILIES) {
      assert.deepEqual(jobs.planCoreJob(family, { pyodideAvailable: true }).engines, ["pyodide", "worker"]);
    }
  });

  it("takes a row per family, and refuses a qualifier that names none", () => {
    assert.deepEqual(model.normaliseQualifier("core_job", "Clash_Detail"), { value: "clash_detail" });
    assert.ok("error" in model.normaliseQualifier("core_job", "clash"));
    const added = editor.addRow(editor.rowsFromPolicy(model.defaultExecutionPolicy()), "core_job", "clash_detail", ["worker"]);
    assert.ok("rows" in added);
    if ("rows" in added) assert.deepEqual(editor.policyFromRows(added.rows).core_job?.clash_detail, ["worker"]);
  });

  it("is listed by Admin > Execution and Settings > Execution engine (they iterate the operations)", () => {
    assert.ok((model.EXEC_OPERATIONS as readonly string[]).includes("core_job"));
    const root = path.resolve(import.meta.dirname, "..", "..");
    for (const rel of ["components/options/ExecutionEngineOptions.tsx", "layout/optionCommands.ts"]) {
      assert.match(fs.readFileSync(path.join(root, rel), "utf8"), /EXEC_OPERATIONS\.map\(/, rel);
    }
  });

  it("asks for the browser, the worker, or nothing, as the plan runs", () => {
    useWorkerAccessStore.setState({ policy: GLOBAL, loaded: true });
    assert.deepEqual(jobs.coreJobHeaders(jobs.planCoreJob("clash_check", { pyodideAvailable: true })), {
      "X-Ada-Run-On": "browser",
    });
    useExecutionPolicyStore.setState({ policy: { ...model.defaultExecutionPolicy(), core_job: { "*": ["pyodide", "worker"], clash_detail: ["worker", "pyodide"] } } });
    assert.deepEqual(jobs.coreJobHeaders(jobs.planCoreJob("clash_detail", { pyodideAvailable: true })), {
      "X-Ada-Run-On": "worker",
    });
    // No Pyodide here: the worker is the only path, sent unjudged as before.
    assert.deepEqual(jobs.coreJobHeaders(jobs.planCoreJob("clash_check", { pyodideAvailable: false })), {});
    // A policy that leaves nothing that can run it.
    useExecutionPolicyStore.setState({ policy: { ...model.defaultExecutionPolicy(), core_job: { "*": ["worker"] } } });
    useWorkerAccessStore.setState({ policy: { enabled: false, allowed_capabilities: [], effective_all: false }, loaded: true });
    assert.throws(() => jobs.coreJobHeaders(jobs.planCoreJob("utility", { pyodideAvailable: true })), /No execution engine/);
  });
});

describe("settleCoreJob", () => {
  const plan = (engines: ("pyodide" | "worker")[]) => ({ op: "core_job" as const, qualifier: "clash_check", engines, browserPath: true });
  const out = new TextEncoder().encode('{"schema": "ada.clash/result@1"}');

  it("returns an answer without a claim as it is", async () => {
    const calls: string[] = [];
    const resp = { job_id: "w1", derived_key: "k", cached: false };
    assert.deepEqual(await jobs.settleCoreJob("shared", resp, plan(["pyodide", "worker"]), fakeDeps(new Error("x"), calls)), resp);
    assert.deepEqual(calls, []);
  });

  it("runs the claim in the claim's scope, stores its outputs, completes it, and answers its id", async () => {
    const calls: string[] = [];
    const deps = fakeDeps({ status: "done", error: null, outputs: { result: out } }, calls);
    const resp = { job_id: null, derived_key: claim.derived_key, cached: false, browser_job: claim };
    const settled = await jobs.settleCoreJob("shared", resp, plan(["pyodide", "worker"]), deps);
    assert.equal(settled.job_id, "job-1");
    assert.equal(settled.ran_in_browser, true);
    assert.equal("browser_job" in settled, false);
    assert.deepEqual(calls, ["start project:p1 job-1", "run", `put result ${out.length}`, "complete done"]);
  });

  it("hands a claim the browser cannot run to the worker when the policy lists it", async () => {
    const calls: string[] = [];
    const deps = fakeDeps({ status: "error", error: "LookupError: spec 'x' is not registered in this browser", outputs: {} }, calls);
    const settled = await jobs.settleCoreJob("shared", { job_id: null, browser_job: claim }, plan(["pyodide", "worker"]), deps);
    assert.equal(settled.job_id, "job-1");
    assert.equal(settled.ran_in_browser, undefined);
    assert.deepEqual(calls, ["start project:p1 job-1", "run", "to-worker LookupError: spec 'x' is not registered in this browser"]);

    const crashed: string[] = [];
    await jobs.settleCoreJob("shared", { job_id: null, browser_job: claim }, plan(["pyodide", "worker"]), fakeDeps(new Error("no Worker"), crashed));
    assert.deepEqual(crashed, ["start project:p1 job-1", "run", "to-worker no Worker"]);
  });

  it("ends the claim with the browser's reason when nothing else may run it", async () => {
    const calls: string[] = [];
    const deps = fakeDeps({ status: "error", error: "boom", outputs: {} }, calls);
    await assert.rejects(jobs.settleCoreJob("shared", { job_id: null, browser_job: claim }, plan(["pyodide"]), deps), /boom/);
    assert.deepEqual(calls, ["start project:p1 job-1", "run", "complete error boom"]);
  });

  it("keeps a failure the job explains (its report stored), without asking the worker", async () => {
    const calls: string[] = [];
    const report = new TextEncoder().encode('{"error": "declined", "declined": true}');
    const deps = fakeDeps({ status: "error", error: "declined", outputs: { error: report } }, calls);
    await assert.rejects(jobs.settleCoreJob("shared", { job_id: null, browser_job: claim }, plan(["pyodide", "worker"]), deps), /declined/);
    assert.deepEqual(calls, ["start project:p1 job-1", "run", `put error ${report.length}`, "complete error declined"]);
  });
});

describe("plugin wheels by capability", () => {
  it("reads a wheel's capabilities and picks the ones serving a job's pool", () => {
    const manifest = wheels.parsePluginWheelsManifest({
      plugins: {
        welds: { wheel: "welds-1.0-py3-none-any.whl", register: "welds.browser:register", capabilities: ["Welds"] },
        other: { wheel: "other-1.0-py3-none-any.whl", capabilities: "other-pool" },
        plain: { wheel: "plain-1.0-py3-none-any.whl" },
      },
    });
    assert.deepEqual(manifest.plugins.welds.capabilities, ["welds"]);
    assert.deepEqual(manifest.plugins.other.capabilities, ["other-pool"]);
    assert.equal(manifest.plugins.plain.capabilities, undefined);
    assert.deepEqual(wheels.pluginsForCapability(manifest, "welds"), ["welds"]);
    // A wheel listed under the pool's own name serves it (an asset builder's convention).
    assert.deepEqual(wheels.pluginsForCapability(manifest, "Plain"), ["plain"]);
    assert.deepEqual(wheels.pluginsForCapability(manifest, null), []);
    assert.deepEqual(wheels.pluginsForCapability(manifest, "nobody"), []);
  });
});

describe("the call sites", () => {
  const root = path.resolve(import.meta.dirname, "..", "..");
  const read = (rel: string) => fs.readFileSync(path.join(root, rel), "utf8");

  it("submit their core jobs through postCoreJob, each under its family", () => {
    const sites: [string, RegExp][] = [
      ["services/api/clashCheck.ts", /"clash_check"[\s\S]*"clash_detail"/],
      ["services/clash/contactRuns.ts", /postCoreJob[\s\S]*"clash_check"[\s\S]*"clash_detail"/],
      ["services/api/components.ts", /"component_build"/],
      ["services/api/selectionExport.ts", /"export_selection"/],
      ["services/api/equipmentTypes.ts", /"equipment_bbox"/],
      ["services/api/conversion.ts", /"utility"/],
    ];
    for (const [rel, family] of sites) {
      const text = read(rel);
      assert.match(text, /postCoreJob/, rel);
      assert.match(text, family, rel);
    }
  });

  it("the Pyodide worker answers run-core-job through ada.comms.rest.wasm_jobs", () => {
    const worker = read("utils/pyodide/pyodide_worker.js");
    assert.match(worker, /data\.type === "run-core-job"/);
    assert.match(worker, /import ada\.comms\.rest\.wasm_jobs as _wj/);
    assert.match(read("utils/pyodide/pyodide_converter.ts"), /type: "run-core-job"/);
  });
});