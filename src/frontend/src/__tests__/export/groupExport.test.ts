// Several elements as one file: which providers the members were delivered by as a mesh, the
// "Geometry from" choices and their default, what blocks an export, and the body the route gets.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { WireGeometryProvider } from "@/services/api/clashCheck";
import {
  groupExportBlocker,
  groupExportBody,
  meshDeliveredProviders,
  planGroupExport,
  type LoadedAssetFacts,
} from "@/utils/export/selectionExport";
import type { GroupMember } from "@/utils/groups/savedGroups";

const node = (provider: string, subject: string, element: string | null): GroupMember => ({
  target: { kind: "node", provider, collection: "c", subject, revision: "r1", node: null },
  element,
  path: element ? [element] : [],
});

const file = (element: string): GroupMember => ({ target: { kind: "file", source_key: "models/a.ifc" }, element, path: [element] });

const loaded = (provider: string, subject: string, glbKey?: string): LoadedAssetFacts => ({
  sourceName: `assets:${provider}/c/${subject}@r1`,
  ref: { provider, collection: "c", subject, revision: "r1" },
  ...(glbKey ? { glbKey } : {}),
});

const providers: WireGeometryProvider[] = [
  { id: "solid", label: "Solid", readable: true },
  { id: "mesh", label: "Mesh", readable: false, unavailable_reason: "no reader" },
  { id: "other", readable: false },
];

test("a member whose model was loaded without a derived GLB was delivered as a mesh", () => {
  const members = [node("mesh", "s1", "bm1"), node("solid", "s2", "bm2")];
  assert.deepEqual(meshDeliveredProviders(members, [loaded("mesh", "s1"), loaded("solid", "s2", "glb/key")]), ["mesh"]);
});

test("with a mesh member the default is a readable provider and each model's own is disabled", () => {
  const plan = planGroupExport({ members: [node("mesh", "s1", "bm1")], loadedAssets: [loaded("mesh", "s1")], providers });
  assert.equal(plan.defaultProvider, "solid");
  assert.equal(plan.choices[0].id, null);
  assert.equal(plan.choices[0].disabled, true);
  // The mesh provider itself is not offered as a source of objects.
  assert.ok(!plan.choices.some((c) => c.id === "mesh"));
  // Unreadable providers come last, disabled.
  assert.deepEqual(
    plan.choices.slice(1).map((c) => [c.id, c.disabled]),
    [["solid", false], ["other", true]],
  );
});

test("without a mesh member each model's own provider is the default", () => {
  const plan = planGroupExport({ members: [node("solid", "s2", "bm2")], loadedAssets: [loaded("solid", "s2", "glb")], providers });
  assert.equal(plan.defaultProvider, null);
  assert.equal(groupExportBlocker(plan, null), null);
});

test("a member from a provider the server cannot read defaults to one it can, even with a GLB", () => {
  const plan = planGroupExport({
    members: [node("mesh", "s1", "bm1")],
    loadedAssets: [loaded("mesh", "s1", "glb/key")],
    providers,
  });
  assert.equal(plan.defaultProvider, "solid");
  assert.equal(plan.choices[0].disabled, true);
  assert.match(groupExportBlocker(plan, null) ?? "", /cannot be read into objects/);
});

test("a provider the list does not name counts as unreadable once the list is known", () => {
  const plan = planGroupExport({ members: [node("unlisted", "s1", "bm1")], loadedAssets: [], providers });
  assert.equal(plan.defaultProvider, "solid");
  // Unknown list: nothing is assumed about the member's provider.
  assert.equal(planGroupExport({ members: [node("unlisted", "s1", "bm1")], loadedAssets: [], providers: null }).defaultProvider, null);
});

test("file members alone offer no provider choice", () => {
  const plan = planGroupExport({ members: [file("bm1"), file("bm2")], loadedAssets: [], providers });
  assert.equal(plan.hasNodes, false);
  assert.deepEqual(plan.choices.map((c) => c.id), [null]);
});

test("a mesh member read from its own provider is blocked, with what to do about it", () => {
  const plan = planGroupExport({ members: [node("mesh", "s1", "bm1")], loadedAssets: [loaded("mesh", "s1")], providers });
  assert.match(groupExportBlocker(plan, null) ?? "", /choose a provider/);
  assert.equal(groupExportBlocker(plan, "solid"), null);
});

test("a mesh member with no readable provider says so", () => {
  const plan = planGroupExport({
    members: [node("mesh", "s1", "bm1")],
    loadedAssets: [loaded("mesh", "s1")],
    providers: [{ id: "solid", readable: false }],
  });
  assert.equal(plan.defaultProvider, null);
  assert.match(groupExportBlocker(plan, null) ?? "", /no provider/);
});

test("the body carries the group, and the provider only when one is chosen", () => {
  const members = [file("bm1"), file("bm2")];
  assert.deepEqual(groupExportBody(members, "picked", "ifc", null), {
    group: { name: "picked", members },
    format: "ifc",
    label: "picked",
  });
  assert.equal(groupExportBody(members, "picked", "step", "solid").geometry_provider, "solid");
});
