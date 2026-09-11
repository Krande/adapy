import assert from "node:assert/strict";
import { test } from "node:test";

// Imported from the concrete modules, not the index: the index picks the
// implementation via `runtime.isRestMode()`, which reads `window`.
import { RESTProceduralModelCapability } from "@/services/capabilities/rest_capabilities";
import { WSProceduralModelCapability } from "@/services/capabilities/ws_capabilities";
import type { ProceduralDoc } from "@/services/viewerApi";

// The "Procedural equipment" and "Procedural system" panels read the cellbuilder store, which was
// only ever filled by `viewerApi.getProceduralModel` — a REST call. On the websocket path
// (`assembly.show()`) both panels therefore rendered nothing, even though everything they need was
// sitting in the GLB that had just been pushed into the viewer.
//
// These pin the two halves of the capability that fixes it:
//   - the websocket half serves the document the model arrived carrying,
//   - the REST half keeps fetching the stored model and refuses an embedded copy, so introducing
//     the capability cannot change the hosted viewer's behaviour.

const DOC: ProceduralDoc = {
  spaces: [{ NAME: "Deck1", X: 0, Y: 0, Z: 0, DX: 24, DY: 12, DZ: 5 }],
  equipments: [
    { NAME: "V-201", DESCRIPTION: "separator", SPACE_NAME: "Deck1", X: 1, Y: 1, Z: 0, LX: 5, LY: 2.5, LZ: 2.5 },
  ],
  systems: [{ NAME: "201/1", TYPE: "piping", MEDIUM: "HC", CONNECTIONS: [] }],
} as unknown as ProceduralDoc;

test("the websocket capability serves the document the GLB carried", async () => {
  const cap = new WSProceduralModelCapability();

  assert.equal((await cap.fetchModel({})).available, false, "nothing adopted yet");

  assert.equal(cap.adoptEmbeddedModel(DOC), true, "the WS transport must accept the embedded doc");
  const result = await cap.fetchModel({});

  assert.equal(result.available, true);
  assert.equal(result.doc, DOC);
});

test("loading a model without a document clears the previous one", async () => {
  // Otherwise the panels go on describing the equipment of the model before this one, which is
  // worse than showing nothing.
  const cap = new WSProceduralModelCapability();
  cap.adoptEmbeddedModel(DOC);

  cap.adoptEmbeddedModel(null);

  assert.equal((await cap.fetchModel({})).available, false);
});

test("the websocket model is read-only", () => {
  // It came out of a GLB; there is no backend to commit an edit to.
  assert.equal(new WSProceduralModelCapability().canEdit, false);
});

test("the REST capability refuses an embedded document", () => {
  // The stored model is editable and committable, so it stays the authority — an embedded copy
  // must not race it. This is what keeps the hosted viewer unchanged by the seam.
  const cap = new RESTProceduralModelCapability();

  assert.equal(cap.adoptEmbeddedModel(DOC), false);
  assert.equal(cap.canEdit, true);
});

test("the REST capability reports absent rather than throwing without an address", async () => {
  // The panels degrade to empty for a model with no procedural provenance; they never see an error.
  const cap = new RESTProceduralModelCapability();

  assert.deepEqual(await cap.fetchModel({}), { available: false });
  assert.deepEqual(await cap.fetchModel({ scope: "s" }), { available: false });
  assert.deepEqual(await cap.fetchModel({ modelId: "m" }), { available: false });
});
