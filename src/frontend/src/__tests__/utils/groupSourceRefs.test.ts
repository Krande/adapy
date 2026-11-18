// A saved-group member's SOURCE reference: saved from each kind of loaded model (a node loaded
// alone, an element of a published model, a whole stored file, a file opened from disk), resolved
// to the publish's source file at the revision it was saved against; old (@1) documents still read;
// what the Details say; and what the downstream users (a server check, "Request geometry…") take.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import {
  attachMemberSources,
  hasUnrecordedSources,
  memberSourceLines,
  serverCheckMembers,
  sourceArtefactOf,
  type SourceRefDeps,
} from "../../utils/groups/groupSourceRefs";
import {
  collapseSelectedRows,
  groupNodePlan,
  GROUPS_SCHEMA,
  GROUPS_SCHEMA_V1,
  memberKey,
  membersForRows,
  parseGroupsDoc,
  serialiseGroupsDoc,
  sourceFormatOf,
  type GroupMember,
} from "../../utils/groups/savedGroups";
import { buildTreeIndices } from "../../utils/tree_view/treeGraph";

const REV = "20260101T000000Z";
const REV2 = "20260201T000000Z";
const SCOPE = "user:me";

/** A provider's manifest for `subject` at `revision`, its source shared by absolute key. */
function manifest(subject: string, revision: string, source: Record<string, unknown> | null = { key: `assets/plant/S10/${revision}/model.db` }) {
  return {
    schema: "ada.assets/manifest@1",
    provider: "solids",
    collection: "plant",
    subject,
    revision,
    artefacts: [
      ...(source ? [{ role: "source", sha256: "abc", size: 10, ...source }] : []),
      { role: "hierarchy", file: "hierarchy.json", sha256: "def", size: 5 },
    ],
  };
}

function deps(over: Partial<SourceRefDeps> = {}, reads: string[] = []): SourceRefDeps {
  return {
    scope: SCOPE,
    async readManifest(collection, subject, revision) {
      reads.push(`${collection}/${subject}@${revision}`);
      return manifest(subject, revision);
    },
    async elementIds(members) {
      return new Map(members.filter((m) => m.element === "Beam 2").map((m) => [memberKey(m), "N22"]));
    },
    isLocalFile: (key) => key === "scratch.glb",
    ...over,
  };
}

// --- a scene with one model of each kind ------------------------------------------------------

let range = 0;
const leaf = (id: string, name: string, model: string): TreeNodeData => ({
  id,
  name,
  children: [],
  model_key: model,
  node_name: `m_${model}`,
  rangeId: String(range++),
});

/** A node loaded alone (per-node build), a whole published subject, a stored STEP file, a GLB
 *  opened from disk. */
function scene() {
  range = 0;
  const alone: TreeNodeData = {
    id: "rA",
    name: "Frame 1",
    model_key: "kA",
    source_name: `assets:solids/plant/S10@${REV}#N20`,
    children: [leaf("a1", "Beam 1", "kA"), leaf("a2", "Beam 2", "kA")],
  };
  const whole: TreeNodeData = {
    id: "rB",
    name: "Area",
    model_key: "kB",
    source_name: `assets:solids/plant/S10@${REV}`,
    children: [leaf("b1", "Beam 2", "kB"), leaf("b2", "Beam 3", "kB")],
  };
  const stored: TreeNodeData = {
    id: "rC",
    name: "deck.step",
    model_key: "kC",
    source_name: "proj/deck.step",
    children: [leaf("c1", "Plate 1", "kC"), leaf("c2", "Plate 2", "kC")],
  };
  const local: TreeNodeData = {
    id: "rD",
    name: "scratch.glb",
    model_key: "kD",
    source_name: "scratch.glb",
    children: [leaf("d1", "Mesh 1", "kD"), leaf("d2", "Mesh 2", "kD")],
  };
  const root: TreeNodeData = { id: "__roots__", name: "", children: [alone, whole, stored, local] };
  return buildTreeIndices(root);
}

async function saved(ids: string[], d: SourceRefDeps = deps()): Promise<GroupMember[]> {
  const idx = scene();
  const { members } = membersForRows(collapseSelectedRows(new Set(ids), idx), idx, []);
  return attachMemberSources(members, d);
}

// --- saving from each kind of loaded model -----------------------------------------------------

test("a node loaded alone refers to its subject's source file at the pinned revision, by its own node id", async () => {
  const reads: string[] = [];
  const [m] = await saved(["a1", "a2"], deps({}, reads));
  assert.equal(m.element, null);
  assert.deepEqual(m.source, {
    kind: "stored",
    scope: SCOPE,
    provider: "solids",
    collection: "plant",
    subject: "S10",
    revision: REV,
    key: `assets/plant/S10/${REV}/model.db`,
    format: "database",
    sha256: "abc",
    element_id: "N20",
  });
  assert.deepEqual(reads, [`plant/S10@${REV}`], "the SUBJECT's manifest is read, not the node's");
});

test("elements of a published model carry their id in the source where the index places them, else names only", async () => {
  const reads: string[] = [];
  const members = await saved(["a2", "b1", "b2"], deps({}, reads));
  // a2 is one of two in its model; b1 + b2 are the whole of model B.
  assert.deepEqual(
    members.map((m) => [m.element, m.source?.kind === "stored" ? m.source.element_id : "-"]),
    [
      ["Beam 2", "N22"],
      [null, "S10"],
    ],
  );
  assert.deepEqual(reads, [`plant/S10@${REV}`], "one manifest read per subject-revision");
  const [one] = await saved(["a1"]);
  assert.equal(one.source?.kind === "stored" && one.source.element_id, null, "not placed: found by element/path");
  assert.equal(one.element, "Beam 1");
});

test("a whole model loaded from a stored file refers to the file itself, which has no revisions", async () => {
  const [m] = await saved(["c1", "c2"]);
  assert.deepEqual(m.target, { kind: "file", source_key: "proj/deck.step" });
  assert.deepEqual(m.source, {
    kind: "stored",
    scope: SCOPE,
    provider: null,
    collection: null,
    subject: null,
    revision: null,
    key: "proj/deck.step",
    format: "step",
    sha256: null,
    element_id: null,
  });
});

test("a model opened from a local file has no source, and says so; a stored GLB has no solid model", async () => {
  const [m] = await saved(["d1"]);
  assert.equal(m.source?.kind, "none");
  assert.equal(m.source?.kind === "none" && m.source.reason, "local-file");
  // The target is kept as before, so the member still selects while that file is loaded.
  assert.deepEqual(m.target, { kind: "file", source_key: "scratch.glb" });
  const [glb] = await attachMemberSources([{ target: { kind: "file", source_key: "proj/hull.glb" }, element: null, path: [] }], deps());
  assert.equal(glb.source?.kind === "none" && glb.source.reason, "mesh-only");
});

test("a publish without a source file, an unreadable manifest, and a member with no revision", async () => {
  const node = { kind: "node", provider: "solids", collection: "plant", subject: "S10", revision: REV, node: null } as const;
  const [bare] = await attachMemberSources([{ target: node, element: null, path: [] }], deps({ readManifest: async (_c, s, r) => manifest(s, r, null) }));
  assert.equal(bare.source?.kind === "none" && bare.source.reason, "no-source-artefact");
  const [gone] = await attachMemberSources(
    [{ target: node, element: null, path: [] }],
    deps({
      readManifest: async () => {
        throw new Error("404");
      },
    }),
  );
  assert.equal(gone.source?.kind === "none" && gone.source.reason, "unreadable");
  // No revision on the member: the one its node resolves to now is pinned.
  const [latest] = await attachMemberSources([{ target: { ...node, revision: null }, element: null, path: [] }], deps({ currentRevision: async () => REV2 }));
  assert.equal(latest.source?.kind === "stored" && latest.source.revision, REV2);
  assert.equal(latest.source?.kind === "stored" && latest.source.key, `assets/plant/S10/${REV2}/model.db`);
});

test("the source artefact resolves to the right file: an absolute key, or a sibling of the revision", () => {
  assert.deepEqual(sourceArtefactOf(manifest("S10", REV), "plant", "S10", REV), { key: `assets/plant/S10/${REV}/model.db`, sha256: "abc" });
  assert.deepEqual(sourceArtefactOf(manifest("S11", REV, { file: "source.ifc" }), "plant", "S11", REV), {
    key: `assets/plant/S11/${REV}/source.ifc`,
    sha256: "abc",
  });
  assert.equal(sourceArtefactOf(manifest("S11", REV, null), "plant", "S11", REV), null);
  assert.equal(sourceArtefactOf("not a manifest", "plant", "S11", REV), null);
  assert.deepEqual(
    ["a/model.db", "a/b.STEP", "a/b.stp", "a/b.ifc", "a/b.xml", "a/b.sat", "a/b.bin"].map(sourceFormatOf),
    ["database", "step", "step", "ifc", "genie-xml", "sat", "other"],
  );
});

test("a member that has a source keeps it; only the others are looked up", async () => {
  const reads: string[] = [];
  const first = await saved(["a1", "a2"], deps({}, reads));
  const again = await attachMemberSources(first, deps({}, reads));
  assert.deepEqual(again, first);
  assert.equal(reads.length, 1);
});

// --- the document ------------------------------------------------------------------------------

test("an @1 document still reads -- its members have no source -- and is written back as @2", () => {
  const v1 = {
    schema: GROUPS_SCHEMA_V1,
    groups: [
      {
        id: "g1",
        name: "Frames",
        created_at: "t",
        members: [{ target: { kind: "node", provider: "solids", collection: "plant", subject: "S10", revision: REV, node: "N20" }, element: "Beam 1", path: ["Beam 1"] }],
      },
    ],
  };
  const doc = parseGroupsDoc(JSON.stringify(v1));
  assert.equal(doc.groups[0].members[0].source, undefined);
  assert.ok(hasUnrecordedSources(doc.groups[0].members));
  assert.equal(JSON.parse(serialiseGroupsDoc(doc)).schema, GROUPS_SCHEMA);
  assert.equal(GROUPS_SCHEMA, "ada.viewer/groups@2");
});

test("sources round-trip through the stored document; a malformed source drops, not its member", async () => {
  const members = await saved(["a1", "a2", "c1", "c2", "d1"]);
  const doc = { schema: GROUPS_SCHEMA, groups: [{ id: "g1", name: "Mixed", created_at: "t", members }] } as const;
  assert.deepEqual(parseGroupsDoc(serialiseGroupsDoc(doc)), doc);
  const bad = parseGroupsDoc(
    JSON.stringify({
      schema: GROUPS_SCHEMA,
      groups: [{ id: "g1", name: "x", members: [{ ...members[0], source: { kind: "stored", key: "" } }] }],
    }),
  );
  assert.equal(bad.groups[0].members.length, 1);
  assert.equal(bad.groups[0].members[0].source, undefined);
  // The source is no part of a member's identity.
  assert.equal(memberKey(members[0]), memberKey({ ...members[0], source: undefined }));
});

// --- the Details ---------------------------------------------------------------------------------

test("the Details say where each source is, with how many members are in it", async () => {
  const members = await saved(["a2", "b1", "b2", "c1", "d1"]);
  const v1: GroupMember = { target: { kind: "file", source_key: "proj/old.ifc" }, element: null, path: [] };
  assert.deepEqual(
    memberSourceLines([...members, v1]).map((l) => l.text),
    [
      `source: model.db of S10 @ ${REV}, 2 members`,
      "source: deck.step (stored file, not versioned), 1 member",
      "no source in the store: scratch.glb was opened from a local file, 1 member",
      "source not recorded (saved before sources were kept), 1 member",
    ],
  );
});

// --- downstream -----------------------------------------------------------------------------------

test("a server check leaves out members opened from disk, and pins a member with no revision to its source's", () => {
  const node = { kind: "node", provider: "solids", collection: "plant", subject: "S10", revision: null, node: null } as const;
  const pinned: GroupMember = {
    target: node,
    element: null,
    path: [],
    source: { kind: "stored", scope: SCOPE, provider: "solids", collection: "plant", subject: "S10", revision: REV, key: "k/model.db", format: "database", sha256: null, element_id: "S10" },
  };
  const local: GroupMember = {
    target: { kind: "file", source_key: "scratch.glb" },
    element: null,
    path: [],
    source: { kind: "none", reason: "local-file", detail: "" },
  };
  const old: GroupMember = { target: { ...node, revision: REV2 }, element: "Beam 1", path: ["Beam 1"] };
  const out = serverCheckMembers([pinned, local, old]);
  assert.deepEqual(out.skipped, [local]);
  assert.equal(out.members[0].target.kind === "node" && out.members[0].target.revision, REV);
  assert.deepEqual(out.members[1], old, "a member without a source goes as it was");
});

test("Request geometry asks for an element by the id saved with its source, without a tree walk", () => {
  const inside: GroupMember = {
    target: { kind: "node", provider: "solids", collection: "plant", subject: "S10", revision: REV, node: "N20" },
    element: "Beam 2",
    path: ["Beam 2"],
    source: { kind: "stored", scope: SCOPE, provider: "solids", collection: "plant", subject: "S10", revision: REV, key: "k/model.db", format: "database", sha256: null, element_id: "N22" },
  };
  const plan = groupNodePlan([inside]);
  assert.deepEqual(plan.byCollection.get("plant"), [{ collection: "plant", id: "N22", label: "Beam 2" }]);
  // A row the caller resolved still wins.
  assert.deepEqual(groupNodePlan([inside], new Map([[memberKey(inside), "N99"]])).byCollection.get("plant")?.[0].id, "N99");
});
