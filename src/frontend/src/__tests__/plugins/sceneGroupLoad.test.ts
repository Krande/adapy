// `ctx.scene.loadSavedGroup` (plugin API 1.31.0): the Scene panel's "Load into scene" for a saved
// group, asked by a plugin, answered as plain data -- and refused clearly without a scope or a group.

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadSavedGroupFor, type PluginGroupLoadEnv } from "@/plugins/sceneGroupLoad";
import { groupLoadNote } from "@/utils/groups/groupLoadNote";
import type { GroupLoadOutcome } from "@/utils/groups/groupLoadRun";
import type { GroupMember, SavedGroup } from "@/utils/groups/savedGroups";

const node = (element: string | null): GroupMember => ({
  target: { kind: "node", provider: "p", collection: "deck", subject: "s1", revision: null, node: null },
  element,
  path: element ? [element] : [],
});

const beam = node("BM1");
const plate = node("PL2");
const group: SavedGroup = { id: "g1", name: "Deck beams", created_at: "2026-01-01T00:00:00Z", members: [beam, plate] };

function env(out: GroupLoadOutcome, scope: string | null = "project:x"): PluginGroupLoadEnv & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    scope: () => scope,
    findGroup: async (s, id) => {
      calls.push(`find ${s} ${id}`);
      return id === group.id ? group : null;
    },
    load: async (g, s, onStage) => {
      calls.push(`load ${g.id} ${s}`);
      onStage?.("loading 1 of 2");
      return out;
    },
  };
}

const partly: GroupLoadOutcome = {
  loaded: 1,
  already: 2,
  hidden: 3,
  failed: [{ source: "deck/s1", members: [plate], reason: "nothing published to load" }],
  selection: { ranges: 4, notLoaded: [], notFound: [beam] },
  framed: true,
};

test("loads the group of the current scope and answers as plain data, in the Scene panel's words", async () => {
  const e = env(partly);
  const stages: string[] = [];
  const got = await loadSavedGroupFor(e, "g1", { onStage: (s) => stages.push(s) });
  assert.deepEqual(e.calls, ["find project:x g1", "load g1 project:x"]);
  assert.deepEqual(stages, ["loading 1 of 2"]);
  assert.deepEqual(got, {
    groupId: "g1",
    groupName: "Deck beams",
    loaded: 1,
    already: 2,
    hidden: 3,
    failed: [{ source: "deck/s1", members: ["PL2 — deck/s1"], reason: "nothing published to load" }],
    selected: 4,
    notFound: ["BM1 — deck/s1"],
    framed: true,
    text: groupLoadNote(partly).text,
    tone: "warn",
  });
  // Serialisable: nothing but data.
  assert.deepEqual(JSON.parse(JSON.stringify(got)), got);
  assert.match(got.text, /1 model loaded, 2 already in the scene/);
  assert.match(got.text, /4 objects selected/);
});

test("a clean load reads as info", async () => {
  const clean: GroupLoadOutcome = { ...partly, failed: [], selection: { ranges: 1, notLoaded: [], notFound: [] } };
  const got = await loadSavedGroupFor(env(clean), "g1");
  assert.equal(got.tone, "info");
  assert.match(got.text, /1 object selected/);
});

test("a stage callback that throws does not stop the load", async () => {
  const original = console.error;
  console.error = () => undefined;
  try {
    const got = await loadSavedGroupFor(env(partly), "g1", {
      onStage: () => {
        throw new Error("boom");
      },
    });
    assert.equal(got.loaded, 1);
  } finally {
    console.error = original;
  }
});

test("rejects for a group the scope does not have, without loading anything", async () => {
  const e = env(partly);
  await assert.rejects(loadSavedGroupFor(e, "nope"), /no saved group 'nope' in scope 'project:x'/);
  assert.deepEqual(e.calls, ["find project:x nope"]);
});

test("rejects without a scope", async () => {
  const e = env(partly, null);
  await assert.rejects(loadSavedGroupFor(e, "g1"), /no scope/);
  assert.deepEqual(e.calls, []);
});
