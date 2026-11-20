import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

// The builder's before-commit hooks (state/cellbuilder/beforeCommit.ts): every commit runs them
// first, handed the doc and its extras; one answering false vetoes the commit (nothing is sent, the
// model stays dirty), one that throws does not, and an unregistered one is not asked.
//
// The store reads sessionStorage/localStorage at import and the commit path reads `window`: the
// panel realm provides both.
await import("../fixtures/panelRealm");

const doc: ProceduralDoc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "deckA", INCLUDE: true, X: 0, Y: 0, Z: 0, DX: 10, DY: 10, DZ: 4 }],
  equipments: [],
};

test("a hook vetoes a commit, lets one go, or throws without stopping it", async () => {
  const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
  const { registerBeforeCommit } = await import("@/state/cellbuilder/beforeCommit");
  const { capabilities } = await import("@/services/capabilities");
  const st = () => useCellBuilderStore.getState();
  const proc = capabilities.procedural as unknown as Record<string, unknown>;
  const original = proc.commitModel;
  let sent = 0;
  proc.commitModel = async (_scope: unknown, _id: string, _d: ProceduralDoc, revision: number) => {
    sent++;
    return { revision: revision + 1 };
  };
  const seen: { extras: unknown; spaces: number }[] = [];
  let answer: boolean | "throw" = false;
  const off = registerBeforeCommit("test", async ({ doc: d, docExtras }) => {
    seen.push({ extras: docExtras.modifications, spaces: d.spaces?.length ?? 0 });
    if (answer === "throw") throw new Error("broken check");
    return answer;
  });
  try {
    st().open("m-1", "Model", 3, { ...doc, modifications: [{ NAME: "m1" }] } as ProceduralDoc);
    st().setDocExtra("modifications", [{ NAME: "m2" }]);
    assert.equal(await st().commit(), false, "vetoed");
    assert.equal(sent, 0);
    assert.deepEqual(seen[0], { extras: [{ NAME: "m2" }], spaces: 1 }, "the hook sees the doc and its extras as they are");
    assert.equal(st().dirty, true);
    assert.equal(st().committing, false);

    answer = "throw";
    assert.equal(await st().commit(), true, "a hook that throws does not stop it");
    assert.equal(sent, 1);
    assert.equal(st().active?.revision, 4);

    answer = true;
    st().setDocExtra("modifications", [{ NAME: "m3" }]);
    assert.equal(await st().commit(), true);
    assert.equal(sent, 2);

    off();
    answer = false;
    st().setDocExtra("modifications", [{ NAME: "m4" }]);
    assert.equal(await st().commit(), true, "an unregistered hook is not asked");
    assert.equal(seen.length, 3);
  } finally {
    off();
    proc.commitModel = original;
    st().close?.();
  }
});
