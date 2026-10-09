import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { FeaCombinationStep, FeaManifest } from "../../services/api/fea";
import {
  chooseEngineMode,
  DEFAULT_FEA_COMPUTE_POLICY,
  estimateCaseBytes,
  feaSourceId,
  parseFeaComputeSetting,
  withOverride,
  type FeaEngineCapabilities,
} from "../../services/fea/feaEngineChoice";

const STEP = {
  n: 101,
  complex: false,
  terms: [
    [1, 1, 0],
    [2, 1, 0],
  ],
  coefficients: [
    [1, 0],
    [1, 0],
  ],
  needs_raw: false,
  recipe_hash: "a".repeat(64),
} as FeaCombinationStep;

const MANIFEST = {
  version: 2,
  bake_version: 4,
  src: "deck",
  mesh: { url: "m", n_points: 1, n_cells: 1 },
  fields: [
    {
      name_canonical: "disp",
      category: "displacement",
      components: ["X"],
      steps: [{ i: 0, value: 1, label: "1" }],
      blob: { url: "fea.disp.bin", header_bytes: 1024, stride_bytes: 1000, dtype: "float32", byte_order: "little" },
    },
  ],
  combination_steps: [STEP],
  lazy_cases: { version: 1, server: true, client_tier_a: true },
} as unknown as FeaManifest;

const CAPS: FeaEngineCapabilities = { worker: true, module: true, syncAccessHandle: true, simd: true };
const AUTO = DEFAULT_FEA_COMPUTE_POLICY;

describe("public.fea.compute", () => {
  it("parses per-scope modes, objects and the '*' default; anything else is auto", () => {
    const raw = JSON.stringify({ "user:me": "server", shared: { mode: "client", upload: true }, "*": "client" });
    assert.deepEqual(parseFeaComputeSetting(raw, "user:me"), { mode: "server", upload: false });
    assert.deepEqual(parseFeaComputeSetting(raw, "shared"), { mode: "client", upload: true });
    assert.deepEqual(parseFeaComputeSetting(raw, "project:x"), { mode: "client", upload: false });
    assert.deepEqual(parseFeaComputeSetting(null, "shared"), AUTO);
    assert.deepEqual(parseFeaComputeSetting("{not json", "shared"), AUTO);
    assert.deepEqual(parseFeaComputeSetting("server", "any"), { mode: "server", upload: false });
    assert.deepEqual(parseFeaComputeSetting(JSON.stringify({ shared: "bogus" }), "shared"), AUTO);
  });

  it("a per-viewer override chooses within what the scope allows", () => {
    assert.equal(withOverride(AUTO, "client").mode, "client");
    assert.equal(withOverride(AUTO, "nonsense").mode, "auto");
    assert.equal(withOverride({ mode: "server", upload: false }, "client").mode, "server");
  });
});

describe("chooseEngineMode", () => {
  const choose = (over: Partial<Parameters<typeof chooseEngineMode>[0]>) =>
    chooseEngineMode({ policy: AUTO, caps: CAPS, env: {}, manifest: MANIFEST, step: STEP, ...over }).mode;

  it("auto on a capable browser is hybrid; client is local", () => {
    assert.equal(choose({}), "hybrid");
    assert.equal(choose({ policy: { mode: "client", upload: false } }), "local");
    assert.equal(choose({ step: null }), "hybrid");
  });

  it("server for policy, needs_raw, a bake without client combinations, any missing capability", () => {
    assert.equal(choose({ policy: { mode: "server", upload: false } }), "server");
    assert.equal(choose({ step: { ...STEP, needs_raw: true } }), "server");
    assert.equal(choose({ manifest: { ...MANIFEST, lazy_cases: undefined } as FeaManifest }), "server");
    assert.equal(choose({ caps: null }), "server");
    for (const k of ["worker", "module", "syncAccessHandle", "simd"] as const) {
      assert.equal(choose({ caps: { ...CAPS, [k]: false } }), "server", k);
    }
  });

  it("weighs quota for every mode and device memory for auto only", () => {
    const need = estimateCaseBytes(MANIFEST, STEP);
    assert.equal(need, 1000 * 3 + 2048);
    assert.equal(choose({ env: { quota: 10 * need, usage: 9 * need } }), "server");
    assert.equal(choose({ env: { quota: 10 * need, usage: 0 } }), "hybrid");
    assert.equal(choose({ policy: { mode: "client", upload: false }, env: { quota: need, usage: 0 } }), "server");
    assert.equal(choose({ env: { deviceMemory: 2 } }), "server");
    assert.equal(choose({ env: { deviceMemory: 8 } }), "hybrid");
    assert.equal(choose({ policy: { mode: "client", upload: false }, env: { deviceMemory: 2 } }), "local");
  });
});

describe("feaSourceId", () => {
  it("is stable for a bake and changes with scope, key and the bake", async () => {
    const a = await feaSourceId("shared", "/m/deck.SIN", MANIFEST);
    assert.match(a, /^[0-9a-f]{32}$/);
    assert.equal(a, await feaSourceId("shared", "m/deck.SIN", MANIFEST));
    assert.notEqual(a, await feaSourceId("user:me", "m/deck.SIN", MANIFEST));
    assert.notEqual(a, await feaSourceId("shared", "m/other.SIN", MANIFEST));
    assert.notEqual(a, await feaSourceId("shared", "m/deck.SIN", { ...MANIFEST, baked_steps: [1] } as FeaManifest));
  });
});
