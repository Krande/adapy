import assert from "node:assert/strict";
import { test } from "node:test";

import { budgetText, queueSummary } from "../../components/options/LocalJobsOptions";
import type { LocalJobsView } from "../../services/api/localJobs";

const view = (over: Partial<LocalJobsView> = {}): LocalJobsView => ({
  max_parallel: 2,
  cpu_budget: null,
  cpu_budget_cores: 8,
  limited: false,
  host_cores: 8,
  default_max_parallel: 4,
  running: [],
  queued: [],
  ...over,
});

test("an unset CPU budget is an empty field (every core)", () => {
  assert.equal(budgetText(view()), "");
  assert.equal(budgetText(view({ cpu_budget: "50%" })), "50%");
});

test("the queue summary names what runs and what waits", () => {
  assert.equal(queueSummary(view()), "0 running");
  const v = view({
    running: [{ job_id: "a", kind: "glb", threads: 4, light: false }],
    queued: [
      { job_id: "b", kind: "glb", light: false },
      { job_id: "c", kind: "glb", light: false },
    ],
  });
  assert.equal(queueSummary(v), "1 running, 2 queued");
});
