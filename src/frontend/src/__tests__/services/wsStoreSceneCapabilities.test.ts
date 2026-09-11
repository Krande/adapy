import assert from "node:assert/strict";
import { test } from "node:test";

// The store- and scene-layer capabilities (files, fea, conversion, catalog,
// components, metrics) over the websocket transport. None of them has a wire
// verb yet, so the contract under test is the honest one: `supports()` says
// no, every verb refuses with the typed `CapabilityUnavailableError`, and the
// stores that used to call `viewerApi` directly now degrade to their own error
// state instead of a REST client's network failure.
//
// Pinned to WS (non-REST) mode by stubbing `window` before the capability index
// is imported -- see statsStore.test.ts for why the imports are dynamic.
(globalThis as any).window = { COMMS_MODE: undefined };

const { capabilities, CapabilityUnavailableError } = await import("@/services/capabilities");
const { useComponentSpecsStore } = await import("@/state/componentSpecsStore");
const { useEngineCatalogStore } = await import("@/state/engineCatalogStore");
const { useEquipmentCatalogStore } = await import("@/state/equipmentCatalogStore");

async function rejectsUnavailable(p: Promise<unknown>, verb: string): Promise<void> {
  await assert.rejects(p, (e: unknown) => {
    assert.ok(e instanceof CapabilityUnavailableError, `expected CapabilityUnavailableError, got ${String(e)}`);
    assert.equal(e.verb, verb);
    assert.equal(e.transport, "ws");
    return true;
  });
}

test("every store/scene capability is selected from the websocket transport", () => {
  assert.equal(capabilities.transport, "ws");
  for (const domain of ["files", "fea", "conversion", "catalog", "components", "metrics"] as const) {
    assert.equal(capabilities[domain].transport, "ws", domain);
  }
});

test("files: no verb is supported and each refuses with the typed error", async () => {
  const files = capabilities.files;
  for (const verb of [
    "fetchBlob",
    "blobUrl",
    "requestDownloadUrl",
    "requestUploadUrl",
    "reportUploadProgress",
    "completeUpload",
    "putBlob",
  ] as const) {
    assert.equal(files.supports(verb), false, verb);
  }
  await rejectsUnavailable(files.fetchBlob("user:me", "a.glb"), "fetchBlob");
  await rejectsUnavailable(files.requestDownloadUrl("user:me", "a.glb"), "requestDownloadUrl");
  await rejectsUnavailable(files.requestUploadUrl("user:me", "a.glb", 1), "requestUploadUrl");
  await rejectsUnavailable(files.reportUploadProgress("user:me", "a.glb", 1, 2), "reportUploadProgress");
  await rejectsUnavailable(files.completeUpload("user:me", "a.glb"), "completeUpload");
  await rejectsUnavailable(files.putBlob("user:me", "a.glb", new Uint8Array(0)), "putBlob");
  // Synchronous by contract, so it throws rather than rejects.
  assert.throws(() => files.blobUrl("user:me", "a.glb"), (e: unknown) => e instanceof CapabilityUnavailableError);
});

test("fea, conversion, components and metrics refuse the same way", async () => {
  assert.equal(capabilities.fea.supports("fetchManifest"), false);
  await rejectsUnavailable(capabilities.fea.fetchManifest("user:me", "a.sif"), "fetchManifest");

  assert.equal(capabilities.conversion.supports("jobStatus"), false);
  await rejectsUnavailable(capabilities.conversion.jobStatus("job-1"), "jobStatus");

  assert.equal(capabilities.components.supports("fetchSpecs"), false);
  await rejectsUnavailable(capabilities.components.fetchSpecs("user:me"), "fetchSpecs");

  assert.equal(capabilities.metrics.supports("recordViewLoad"), false);
  assert.equal(capabilities.metrics.supports("recordRenderProfile"), false);
  await rejectsUnavailable(capabilities.metrics.recordViewLoad("user:me", { key: "a.glb" }), "recordViewLoad");
  await rejectsUnavailable(capabilities.metrics.recordRenderProfile("user:me", { key: "a.glb" }), "recordRenderProfile");
});

test("catalog: every CRUD verb is unsupported and refuses by name", async () => {
  const catalog = capabilities.catalog;
  for (const verb of [
    "listEquipmentTypes",
    "createEquipmentType",
    "getEquipmentType",
    "updateEquipmentType",
    "deleteEquipmentType",
    "uploadEquipmentCad",
    "copyEquipmentCadFromScope",
    "inferEquipmentBbox",
    "listSystemTemplates",
    "createSystemTemplate",
    "getSystemTemplate",
    "updateSystemTemplate",
    "deleteSystemTemplate",
    "createEngine",
    "getEngine",
    "updateEngine",
    "deleteEngine",
  ] as const) {
    assert.equal(catalog.supports(verb), false, verb);
  }
  await rejectsUnavailable(catalog.listEquipmentTypes("user:me"), "listEquipmentTypes");
  await rejectsUnavailable(catalog.inferEquipmentBbox("user:me", "t1"), "inferEquipmentBbox");
  await rejectsUnavailable(catalog.listSystemTemplates("user:me"), "listSystemTemplates");
  await rejectsUnavailable(catalog.getEngine("user:me", "e1"), "getEngine");
});

test("componentSpecsStore degrades to its own error state, not a network failure", async () => {
  await useComponentSpecsStore.getState().refresh("user:me");
  const s = useComponentSpecsStore.getState();
  assert.equal(s.specs, null);
  assert.equal(s.hasSpecs, false);
  assert.equal(s.loading, false);
  assert.match(s.loadError ?? "", /fetchSpecs is not available over the ws transport/);
});

test("engineCatalogStore reports the refused list verb and keeps an empty catalog", async () => {
  await useEngineCatalogStore.getState().refresh();
  const s = useEngineCatalogStore.getState();
  assert.deepEqual(s.engines, []);
  assert.equal(s.busy, false);
  assert.match(s.error ?? "", /listCatalog\(engines\) is not available over the ws transport/);
});

test("equipmentCatalogStore refresh sets the error and empties the code-archetype union", async () => {
  await useEquipmentCatalogStore.getState().refreshEquipment();
  const s = useEquipmentCatalogStore.getState();
  assert.deepEqual(s.equipmentTypes, []);
  assert.deepEqual(s.availableEquipment, []);
  assert.match(s.equipmentError ?? "", /listEquipmentTypes is not available over the ws transport/);
});
