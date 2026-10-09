import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, it } from "node:test";

import type { FeaCaseOverlay, FeaCombinationStep, FeaEnvelope, FeaManifest } from "../../services/api/fea";
import {
  chooseCaseEngine,
  clearCaseCache,
  resolveCase,
  resolveEnvelope,
  setLocalCaseEngine,
  type CaseEngine,
} from "../../services/fea/feaCaseResolver";
import type { FeaEngineClient } from "../../services/fea/feaEngineClient";
import {
  clearLocalFeaRoutes,
  defaultStorePath,
  localFeaRoute,
  makeOpfsFetcher,
  makeOpfsRangeFetcher,
  registerLocalFeaRoute,
} from "../../services/fea/feaFetcher";
import { makeLocalCaseEngine, type LocalEngineDeps } from "../../services/fea/feaLocalEngine";
import { makeViewerApiFetcher } from "../../services/feaFieldBlob";

const SRC = "decks/deck.SIN";
const SCOPE = "user:me";
const HASH = "ab12cd34".padEnd(64, "0");
const DIR = "101-ab12cd34";

const STEP: FeaCombinationStep = {
  n: 101,
  complex: false,
  terms: [
    [1, 1.5, 0],
    [2, -0.5, 0],
  ],
  coefficients: [
    [1.5, 0],
    [-0.5, 0],
  ],
  needs_raw: false,
  recipe_hash: HASH,
};
const RAW: FeaCombinationStep = { ...STEP, n: 102, needs_raw: true, recipe_hash: "c".repeat(64) };

const MANIFEST = {
  version: 2,
  bake_version: 4,
  src: "deck",
  mesh: { url: "fea.mesh.glb", n_points: 2, n_cells: 1 },
  fields: [
    {
      name_canonical: "disp",
      name_native: "d",
      kind: "vector",
      category: "displacement",
      support: "nodal",
      analysis_kind: "static",
      components: ["X"],
      blob: { url: "fea.disp.bin", header_bytes: 1024, stride_bytes: 8, dtype: "float32", byte_order: "little" },
      n_steps: 2,
      steps: [
        { i: 0, value: 1, label: "1" },
        { i: 1, value: 2, label: "2" },
      ],
      scalar_range: { X: [0, 1] },
      default_view: { reduction: "X", colormap: "viridis" },
      linear_components: ["X"],
      derived_components: {},
    },
  ],
  combination_steps: [STEP, RAW],
  lazy_cases: { version: 1, server: true, client_tier_a: true, cases_prefix: "cases/" },
} as unknown as FeaManifest;

const OVERLAY: FeaCaseOverlay = {
  version: 1,
  kind: "fea_case",
  bake_version: 4,
  src: "deck",
  prefix: `_derived/${SRC}.fea/cases/${DIR}/`,
  case: { n: 101, recipe_hash: HASH },
  producer: { engine: "adacpp-wasm", version: "adacpp_fea/1", tier: "A" },
  fields: [
    {
      name_canonical: "disp",
      n_steps: 1,
      steps: [{ i: 0, value: 101, label: "101" }],
      scalar_range: { X: [0, 1] },
      blob: { url: "fea.disp.bin", header_bytes: 1024, stride_bytes: 8, dtype: "float32", byte_order: "little" },
    },
  ],
};

interface Calls {
  materialise: number;
  server: number;
  reads: string[];
  uploads: string[];
}

function fakeClient(calls: Calls, opts: { fail?: boolean; probeOk?: boolean } = {}): FeaEngineClient {
  return {
    probe: async () => ({ ok: opts.probeOk ?? true, opfs: true, syncAccessHandle: true, simd: true, version: "adacpp_fea/1" }),
    async materialiseCase(_sid, sourceKey, _m, caseN) {
      calls.materialise++;
      assert.equal(sourceKey, SRC);
      if (opts.fail) throw new Error("OPFS exploded");
      assert.equal(caseN, 101);
      return { overlay: OVERLAY, computed: true, timing: { fetched_bytes: 1, fetch_ms: 1, kernel_ms: 1, total_ms: 2, jobs: 1 } };
    },
    async envelope(_sid, _m, field) {
      return { version: 1, kind: "fea_envelope", field, cases: [101], scalar_range: { X: [-2, 3] } } as FeaEnvelope;
    },
    async readFile(_sid, bv, rel, range) {
      calls.reads.push(`${bv}:${rel}${range ? `@${range.start}-${range.end}` : ""}`);
      return new Uint8Array([7, 7, 7]).buffer;
    },
    usage: async () => ({ total: 0, byKind: { base: 0, case: 0, envelope: 0 }, sources: [] }),
    evict: async () => [],
    clear: async () => {},
  };
}

function serverEngine(calls: Calls): CaseEngine {
  return {
    kind: "server",
    async resolve(req) {
      calls.server++;
      return { overlay: { ...OVERLAY, case: { n: req.step.n, recipe_hash: req.step.recipe_hash }, producer: { engine: "numpy" } }, computed: true };
    },
  };
}

function deps(calls: Calls, over: Partial<LocalEngineDeps> = {}, client = fakeClient(calls)): LocalEngineDeps {
  return {
    client: async () => client,
    policy: async () => ({ mode: "auto", upload: false }),
    env: async () => ({}),
    serverRangeFetcher: () => async () => ({ buf: new ArrayBuffer(0), ranged: true }),
    server: serverEngine(calls),
    upload: async (_scope, _src, name) => void calls.uploads.push(name),
    warn: () => {},
    ...over,
  };
}

const newCalls = (): Calls => ({ materialise: 0, server: 0, reads: [], uploads: [] });
const source = { scope: SCOPE, sourceKey: SRC };

// node has no Worker; the resolver's own wiring therefore never makes one.
const g = globalThis as { Worker?: unknown };
beforeEach(() => {
  clearCaseCache();
  clearLocalFeaRoutes();
  g.Worker = class {};
});
afterEach(() => {
  delete g.Worker;
  setLocalCaseEngine(undefined);
});

describe("local case engine", () => {
  it("materialises in the browser and serves the case through the viewer's own fetcher", async () => {
    const calls = newCalls();
    const engine = makeLocalCaseEngine(deps(calls));
    const r = await resolveCase(MANIFEST, source, 101, { engine });
    assert.equal(r.engine, "local");
    assert.equal(r.relPrefix, `cases/${DIR}/`);
    assert.equal(r.overlay.producer?.engine, "adacpp-wasm");
    assert.equal(calls.server, 0);
    assert.equal(engine.lastDecision(SRC, 101)?.mode, "hybrid");

    const { rangeFetcher, fetcher } = makeViewerApiFetcher(SCOPE, `/${SRC}`);
    const ranged = await rangeFetcher(`cases/${DIR}/fea.disp.bin`, 1024, 1031);
    assert.equal(ranged.ranged, true);
    assert.deepEqual([...new Uint8Array(ranged.buf)], [7, 7, 7]);
    await fetcher(`cases/${DIR}/fea.disp.bin`);
    assert.deepEqual(calls.reads, [`4:cases/${DIR}/fea.disp.bin@1024-1031`, `4:cases/${DIR}/fea.disp.bin`]);
    // Hybrid: base blobs are not routed to the store.
    assert.equal(localFeaRoute(SCOPE, SRC, "fea.disp.bin"), null);
  });

  it("falls back to the server on any browser failure", async () => {
    const calls = newCalls();
    const engine = makeLocalCaseEngine(deps(calls, {}, fakeClient(calls, { fail: true })));
    const r = await resolveCase(MANIFEST, source, 101, { engine });
    assert.equal(r.engine, "server");
    assert.equal(r.overlay.producer?.engine, "numpy");
    assert.equal(calls.materialise, 1);
    assert.equal(calls.server, 1);
    assert.match(engine.lastDecision(SRC, 101)!.reason, /OPFS exploded/);
    assert.equal(localFeaRoute(SCOPE, SRC, `cases/${DIR}/fea.disp.bin`), null);
  });

  it("leaves needs_raw, a server policy and an unloadable module to the server", async () => {
    for (const [d, n] of [
      [{}, 102],
      [{ policy: async () => ({ mode: "server" as const, upload: false }) }, 101],
      [{ client: async () => null }, 101],
    ] as const) {
      clearCaseCache();
      const calls = newCalls();
      const r = await resolveCase(MANIFEST, source, n, { engine: makeLocalCaseEngine(deps(calls, d)) });
      assert.equal(r.engine, "server");
      assert.equal(calls.materialise, 0);
    }
    clearCaseCache();
    const calls = newCalls();
    const engine = makeLocalCaseEngine(deps(calls, {}, fakeClient(calls, { probeOk: false })));
    assert.equal((await resolveCase(MANIFEST, source, 101, { engine })).engine, "server");
    assert.equal(engine.lastDecision(SRC, 101)?.reason, "adacpp_fea module unavailable");
  });

  it("local mode also routes base blobs the store holds; upload sends blobs, then the overlay", async () => {
    const calls = newCalls();
    const engine = makeLocalCaseEngine(
      deps(calls, { policy: async () => ({ mode: "client", upload: true }) }),
    );
    const r = await resolveCase(MANIFEST, source, 101, { engine });
    assert.equal(r.engine, "local");
    assert.ok(localFeaRoute(SCOPE, SRC, "fea.disp.bin"));
    await new Promise((res) => setTimeout(res, 0));
    assert.deepEqual(calls.uploads, [`cases/${DIR}/fea.disp.bin`, `cases/${DIR}/fea.case.json`]);
  });

  it("envelopes: the browser's when it can", async () => {
    const calls = newCalls();
    setLocalCaseEngine(makeLocalCaseEngine(deps(calls)));
    const env = await resolveEnvelope(MANIFEST, source, "disp");
    assert.deepEqual(env?.scalar_range, { X: [-2, 3] });
  });

  it("chooseCaseEngine: the server for needs_raw, the local engine for a Tier-A recipe", () => {
    assert.equal(chooseCaseEngine(MANIFEST, RAW).kind, "server");
    assert.equal(chooseCaseEngine({ ...MANIFEST, lazy_cases: undefined } as FeaManifest, STEP).kind, "server");
    assert.equal(chooseCaseEngine(MANIFEST, STEP).kind, "local");
  });
});

describe("OPFS fetchers", () => {
  it("map filenames to the store and fall back when it does not hold them", async () => {
    assert.equal(defaultStorePath("fea.disp.bin"), "base/fea.disp.bin");
    assert.equal(defaultStorePath(`/cases/${DIR}/fea.disp.bin`), `cases/${DIR}/fea.disp.bin`);
    assert.equal(defaultStorePath("envelopes/disp/fea.disp.bin"), "envelopes/disp/fea.disp.bin");
    const seen: string[] = [];
    const read = async (rel: string, range?: { start: number; end: number }) => {
      seen.push(`${rel}${range ? `@${range.start}` : ""}`);
      return rel.includes("present") ? new Uint8Array([1]).buffer : null;
    };
    const f = makeOpfsFetcher(read, { fallback: async () => new Uint8Array([9]).buffer });
    assert.deepEqual([...new Uint8Array(await f("present.bin"))], [1]);
    assert.deepEqual([...new Uint8Array(await f("absent.bin"))], [9]);
    const rf = makeOpfsRangeFetcher(read);
    assert.equal((await rf("present.bin", 4, 8)).ranged, true);
    await assert.rejects(rf("absent.bin", 0, 1), (e: { status?: number }) => e.status === 404);
    assert.deepEqual(seen, ["base/present.bin", "base/absent.bin", "base/present.bin@4", "base/absent.bin@0"]);
  });

  it("route lookup takes the longest registered prefix and unregisters", () => {
    const a = async () => null;
    const b = async () => null;
    const offA = registerLocalFeaRoute(SCOPE, SRC, "", a);
    const offB = registerLocalFeaRoute(SCOPE, `/${SRC}`, "cases/1-x/", b);
    assert.equal(localFeaRoute(SCOPE, SRC, "cases/1-x/fea.a.bin"), b);
    assert.equal(localFeaRoute(SCOPE, SRC, "fea.a.bin"), a);
    assert.equal(localFeaRoute("other", SRC, "fea.a.bin"), null);
    offB();
    assert.equal(localFeaRoute(SCOPE, SRC, "cases/1-x/fea.a.bin"), a);
    offA();
    assert.equal(localFeaRoute(SCOPE, SRC, "fea.a.bin"), null);
  });
});
