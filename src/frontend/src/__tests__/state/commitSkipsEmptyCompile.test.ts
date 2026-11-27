// commit() auto-compiles afterward when autoCompile is on -- but a brand-new, still-empty
// procedural model (no spaces, no loft members) has nothing to compile: docHasNothingToCompile
// (documentSlice.ts) says so, and commit() skips the auto-compile for it, so opening a fresh
// model and committing once does not fire a compile job the engine would just log as pointless.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { docHasNothingToCompile } = await import("@/state/cellbuilder/documentSlice");

test("docHasNothingToCompile: true with no spaces and no loft members", () => {
  assert.equal(docHasNothingToCompile({ spaces: [], loft_members: [] }), true);
  assert.equal(docHasNothingToCompile({ spaces: [] }), true, "loft_members absent counts as none");
});

test("docHasNothingToCompile: false with at least one space", () => {
  assert.equal(docHasNothingToCompile({ spaces: [{ NAME: "A" }], loft_members: [] }), false);
});

test("docHasNothingToCompile: false with at least one loft member, even with no spaces", () => {
  assert.equal(docHasNothingToCompile({ spaces: [], loft_members: [{ NAME: "m1" }] }), false);
});

const emptyDoc: ProceduralDoc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [],
  equipments: [],
};

const withASpace: ProceduralDoc = {
  ...emptyDoc,
  spaces: [{ NAME: "deckA", INCLUDE: true, X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 4 }],
};

test("commit(): autoCompile on, an empty model -- compile() is not called", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { capabilities } = await import("@/services/capabilities");
  const st = () => useCellBuilderStore.getState();
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const originalCommit = proc.commitModel;
  proc.commitModel = async (_scope: unknown, _id: string, _d: ProceduralDoc, revision: number) => ({ revision: revision + 1 });
  const originalCompile = st().compile;
  let compileCalls = 0;
  useCellBuilderStore.setState({ compile: async () => void compileCalls++ } as never);
  try {
    st().open("m-empty", "Model", 1, emptyDoc);
    st().setAutoCompile(true);
    assert.equal(await st().commit(), true);
    assert.equal(compileCalls, 0, "nothing to compile: auto-compile is skipped");
  } finally {
    proc.commitModel = originalCommit;
    useCellBuilderStore.setState({ compile: originalCompile } as never);
  }
});

test("commit(): autoCompile on, a model with a space -- compile() runs as before", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { capabilities } = await import("@/services/capabilities");
  const st = () => useCellBuilderStore.getState();
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const originalCommit = proc.commitModel;
  proc.commitModel = async (_scope: unknown, _id: string, _d: ProceduralDoc, revision: number) => ({ revision: revision + 1 });
  const originalCompile = st().compile;
  let compileCalls = 0;
  useCellBuilderStore.setState({ compile: async () => void compileCalls++ } as never);
  try {
    st().open("m-withspace", "Model", 1, withASpace);
    st().setAutoCompile(true);
    assert.equal(await st().commit(), true);
    assert.equal(compileCalls, 1, "something to compile: auto-compile still fires, unchanged");
  } finally {
    proc.commitModel = originalCommit;
    useCellBuilderStore.setState({ compile: originalCompile } as never);
  }
});

test("commit(): an empty doc skips the auto-compile for any engine, not only the built-in one", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { capabilities } = await import("@/services/capabilities");
  const st = () => useCellBuilderStore.getState();
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const originalCommit = proc.commitModel;
  proc.commitModel = async (_scope: unknown, _id: string, _d: ProceduralDoc, revision: number) => ({ revision: revision + 1 });
  const originalCompile = st().compile;
  let compileCalls = 0;
  useCellBuilderStore.setState({ compile: async () => void compileCalls++ } as never);
  try {
    // A doc naming an engine other than the built-in one: with no spaces and no loft members
    // it has nothing to start a compile from either, so a job would only come back as an error.
    st().open("m-engine", "Model", 1, { ...emptyDoc, engine: "a-capability-engine" });
    st().setAutoCompile(true);
    assert.equal(await st().commit(), true);
    assert.equal(compileCalls, 0, "nothing to compile for this engine either: auto-compile is skipped");
  } finally {
    proc.commitModel = originalCommit;
    useCellBuilderStore.setState({ compile: originalCompile } as never);
  }
});

test("commit(): the skip clears a previously compiled result instead of leaving it on screen", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { capabilities } = await import("@/services/capabilities");
  const st = () => useCellBuilderStore.getState();
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const originalCommit = proc.commitModel;
  proc.commitModel = async (_scope: unknown, _id: string, _d: ProceduralDoc, revision: number) => ({ revision: revision + 1 });
  try {
    st().open("m-stale", "Model", 1, emptyDoc);
    // Stand in for a result left over from before the model's last space was deleted: a real
    // compile would have set these through viewResult/compileJobRunner.
    useCellBuilderStore.setState({ resultSourceName: "procedural:Model", detailSourceName: "procedural-detail:Model" } as never);
    st().setAutoCompile(true);
    assert.equal(await st().commit(), true);
    assert.equal(st().resultSourceName, null, "the stale sim result is dropped, not left on screen");
    assert.equal(st().detailSourceName, null, "the stale detail result too");
  } finally {
    proc.commitModel = originalCommit;
  }
});

test("commit(): autoCompile off -- compile() is never called, empty or not", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { capabilities } = await import("@/services/capabilities");
  const st = () => useCellBuilderStore.getState();
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const originalCommit = proc.commitModel;
  proc.commitModel = async (_scope: unknown, _id: string, _d: ProceduralDoc, revision: number) => ({ revision: revision + 1 });
  const originalCompile = st().compile;
  let compileCalls = 0;
  useCellBuilderStore.setState({ compile: async () => void compileCalls++ } as never);
  try {
    st().open("m-noauto", "Model", 1, withASpace);
    st().setAutoCompile(false);
    assert.equal(await st().commit(), true);
    assert.equal(compileCalls, 0);
  } finally {
    proc.commitModel = originalCommit;
    useCellBuilderStore.setState({ compile: originalCompile } as never);
  }
});
