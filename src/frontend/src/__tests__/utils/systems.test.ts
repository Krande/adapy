import assert from "node:assert/strict";
import { test } from "node:test";

import type { SystemMember } from "@/services/api/nodeRelations";
import { loadPlan, systemKindLabel } from "@/utils/systems/systems";

const member = (full_name: string, sites: SystemMember["sites"]): SystemMember => ({
  full_name,
  node_id: `id-${full_name}`,
  run: null,
  sites,
});

test("a system kind reads as the domain a person knows it by", () => {
  assert.equal(systemKindLabel("duct"), "HVAC");
  assert.equal(systemKindLabel("piping"), "Piping");
  assert.equal(systemKindLabel("drainage"), "Drainage");
  assert.equal(systemKindLabel(null), "System");
});

test("a member loads through the provider the collection is loaded from, when that one publishes it", () => {
  const plan = loadPlan(
    "COL",
    [
      member("/A", [
        { provider: "other", subject: "s-other", node_id: "n-other" },
        { provider: "loaded", subject: "s-loaded", node_id: "n-loaded" },
      ]),
      member("/B", [{ provider: "other", subject: "s2", node_id: "n2" }]),
      member("/C", []),
    ],
    new Set(["loaded"]),
  );
  assert.deepEqual(
    plan.members.map((m) => (m.target.kind === "node" ? [m.target.provider, m.target.subject, m.target.node] : null)),
    [
      ["loaded", "s-loaded", "n-loaded"],
      ["other", "s2", "n2"],
    ],
  );
  assert.equal(plan.unplaced, 1);
  assert.equal(plan.names.get("col\u0000n-loaded"), "/A");
});
