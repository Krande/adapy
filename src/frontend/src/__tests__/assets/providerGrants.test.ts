import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  SHARED_TARGET,
  collectionLabel,
  grantInput,
  projectTarget,
  targetLabel,
  userTarget,
  type ProviderGrant,
} from "../../assets/providerGrants";

const grant: ProviderGrant = {
  id: "g1",
  target_scope: "projects/p1",
  provider: "vendor",
  collection: null,
  attributes: true,
  geometry: false,
  request: true,
  granted_at: null,
  granted_by: "admin",
  note: "  kept  ",
};

test("a grant is saved with request pinned off, whatever was stored", () => {
  const body = grantInput(grant, { geometry: true });
  assert.deepEqual(body, {
    target_scope: "projects/p1",
    provider: "vendor",
    collection: null,
    attributes: true,
    geometry: true,
    request: false,
    note: "kept",
  });
});

test("a new grant defaults to attributes and geometry, and sends no empty note", () => {
  const body = grantInput({ target_scope: SHARED_TARGET, provider: "vendor", collection: "alpha" }, { note: "  " });
  assert.deepEqual(body, {
    target_scope: "shared",
    provider: "vendor",
    collection: "alpha",
    attributes: true,
    geometry: true,
    request: false,
  });
});

test("targets are named in words, falling back to the id", () => {
  const names = new Map([["p1", "Pipe rack"]]);
  assert.equal(targetLabel(SHARED_TARGET, names), "Shared");
  assert.equal(targetLabel(projectTarget("p1"), names), "Project · Pipe rack");
  assert.equal(targetLabel(projectTarget("gone"), names), "Project · gone");
  assert.equal(targetLabel(userTarget("u1"), names), "User · u1");
  assert.equal(collectionLabel(null), "All collections");
  assert.equal(collectionLabel("alpha"), "alpha");
});
