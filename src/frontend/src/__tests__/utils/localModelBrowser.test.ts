import assert from "node:assert/strict";
import { test } from "node:test";

// The "open a local model" decision for the CellBuilderPanel's local-disk browser (ws/REST parity
// plan, step 6). Exercised against a fake capability -- no React render, no real websocket --
// pinning the two branches the panel relies on: `canEdit` true opens a real editable session
// (`open`), `canEdit` false loads view-only (`loadFromDoc`), and a model that cannot be resolved
// reports an error rather than silently doing nothing.

import { openLocalModel } from "@/utils/cellbuilder/localModelBrowser";
import { LOCAL_MODEL_SCOPE } from "@/services/capabilities";
import type { ProceduralDoc } from "@/services/viewerApi";

const DOC = { spaces: [{ NAME: "Deck1" }], equipments: [] } as unknown as ProceduralDoc;

test("a connected transport opens a real, editable session via open()", async () => {
  const openCalls: unknown[] = [];
  const loadFromDocCalls: unknown[] = [];
  const fetchCalls: unknown[] = [];

  const result = await openLocalModel(
    { modelId: "hull-a" },
    {
      fetchModel: async (source) => {
        fetchCalls.push(source);
        return { available: true, doc: DOC };
      },
      canEdit: true,
      open: (...args) => openCalls.push(args),
      loadFromDoc: (...args) => loadFromDocCalls.push(args),
    },
  );

  assert.deepEqual(result, { ok: true });
  assert.deepEqual(fetchCalls, [{ scope: LOCAL_MODEL_SCOPE, modelId: "hull-a" }]);
  assert.equal(openCalls.length, 1, "open() called exactly once");
  assert.deepEqual(openCalls[0], ["hull-a", "hull-a", 0, DOC]);
  assert.equal(loadFromDocCalls.length, 0, "loadFromDoc() must not be called when canEdit is true");
});

test("a disconnected transport loads the document view-only via loadFromDoc()", async () => {
  const openCalls: unknown[] = [];
  const loadFromDocCalls: unknown[] = [];

  const result = await openLocalModel(
    { modelId: "hull-a" },
    {
      fetchModel: async () => ({ available: true, doc: DOC }),
      canEdit: false,
      open: (...args) => openCalls.push(args),
      loadFromDoc: (...args) => loadFromDocCalls.push(args),
    },
  );

  assert.deepEqual(result, { ok: true });
  assert.equal(openCalls.length, 0, "open() must not be called when canEdit is false");
  assert.deepEqual(loadFromDocCalls, [[DOC]]);
});

test("a model that cannot be resolved reports an error and calls neither store action", async () => {
  const openCalls: unknown[] = [];
  const loadFromDocCalls: unknown[] = [];

  const result = await openLocalModel(
    { modelId: "missing" },
    {
      fetchModel: async () => ({ available: false }),
      canEdit: true,
      open: (...args) => openCalls.push(args),
      loadFromDoc: (...args) => loadFromDocCalls.push(args),
    },
  );

  assert.equal(result.ok, false);
  assert.match((result as { ok: false; error: string }).error, /missing/);
  assert.equal(openCalls.length, 0);
  assert.equal(loadFromDocCalls.length, 0);
});

test("a fetchModel that throws surfaces its message instead of a raw rejection", async () => {
  const result = await openLocalModel(
    { modelId: "hull-a" },
    {
      fetchModel: async () => {
        throw new Error("socket closed");
      },
      canEdit: true,
      open: () => {},
      loadFromDoc: () => {},
    },
  );

  assert.equal(result.ok, false);
  assert.equal((result as { ok: false; error: string }).error, "socket closed");
});
