import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  SHARED_TARGET,
  collectionLabel,
  grantChoices,
  grantDelta,
  grantInput,
  projectTarget,
  scopeProviderAccess,
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

const g = (target_scope: string, provider: string, collection: string | null, attributes = true, geometry = true): ProviderGrant => ({
  ...grant,
  id: `${target_scope}/${provider}/${collection}`,
  target_scope,
  provider,
  collection,
  attributes,
  geometry,
  request: false,
});

test("a scope's access to a provider is its read grants: provider-wide and per collection", () => {
  const grants = [
    g("projects/p1", "vendor", "alpha"),
    g("projects/p1", "vendor", "beta", true, false), // attributes only still reads
    g("projects/p1", "vendor", null),
    g("projects/p1", "other", "gamma"),
    g("shared", "vendor", "delta"),
    { ...g("projects/p1", "vendor", "zeta"), attributes: false, geometry: false, request: true }, // reads nothing
  ];
  const access = scopeProviderAccess(grants, "projects/p1", "vendor");
  assert.equal(access.all, true);
  assert.deepEqual([...access.collections].sort(), ["alpha", "beta"]);
  assert.deepEqual(scopeProviderAccess(grants, "shared", "vendor"), { all: false, collections: new Set(["delta"]) });
});

test("the checkboxes are every advertised collection plus every granted one gone offline", () => {
  const choices = grantChoices(["ALPHA", "BETA", "alpha"], { all: false, collections: new Set(["alpha", "gone"]) });
  assert.deepEqual(choices, [
    { collection: "ALPHA", granted: true, advertised: true },
    { collection: "BETA", granted: false, advertised: true },
    { collection: "gone", granted: true, advertised: false },
  ]);
});

test("select all and untick send only what changes", () => {
  const choices = grantChoices(["ALPHA", "BETA", "GAMMA"], { all: false, collections: new Set(["alpha"]) });
  assert.deepEqual(grantDelta(choices, ["ALPHA", "BETA", "GAMMA"], true), { grant: ["BETA", "GAMMA"], revoke: [] });
  assert.deepEqual(grantDelta(choices, ["alpha", "beta"], false), { grant: [], revoke: ["ALPHA"] });
});
