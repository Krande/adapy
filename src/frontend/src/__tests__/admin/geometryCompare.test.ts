import assert from "node:assert/strict";
import { test } from "node:test";

import { metricLines, nodeOf } from "@/components/admin/geometryCompareView";
import { compareQueryString, type CompareItem } from "@/services/api/geometryCompare";

const item = (over: Partial<CompareItem> = {}): CompareItem => ({
  provider_a: "pa",
  provider_b: "pb",
  collection: "col",
  key: "S/ONE",
  name: "/S/ONE",
  verdict: "uncertain",
  reason: "surfaces up to 4.0 mm apart",
  metrics: { box_delta_mm: 5.04, hausdorff_mm: 4.0, p95_mm: 3.2, volume_ratio: 1.01, open: [false, false] },
  a: { node: "n1", subject: "s1" },
  b: { subject: "s9" },
  picture_a: null,
  picture_b: null,
  compared_at: null,
  ...over,
});

test("the metric lines say what was measured, most telling first", () => {
  assert.deepEqual(metricLines(item()), ["box Δ 5.0 mm", "surface ≤ 4.0 mm (p95 3.2)", "volume ×1.010"]);
  assert.deepEqual(metricLines(item({ verdict: "missing", metrics: { missing: "second" }, b: null })), ["only in pa"]);
  assert.ok(metricLines(item({ metrics: { box_delta_mm: 1, open: [true, false] } })).includes("a mesh is not closed"));
});

test("a side's node is its published node, else its subject", () => {
  assert.equal(nodeOf(item(), "a"), "n1");
  assert.equal(nodeOf(item(), "b"), "s9");
  assert.equal(nodeOf(item({ b: null }), "b"), null);
});

test("the gallery query repeats collections and verdicts and pages", () => {
  const q = new URLSearchParams(
    compareQueryString({ providerA: "pa", providerB: "pb", collections: ["a", "b"], verdicts: ["fail"], q: " x ", sort: "box_delta_mm", offset: 24 }),
  );
  assert.deepEqual(q.getAll("collection"), ["a", "b"]);
  assert.deepEqual(q.getAll("verdict"), ["fail"]);
  assert.equal(q.get("q"), "x");
  assert.equal(q.get("limit"), "24");
  assert.equal(q.get("offset"), "24");
});
