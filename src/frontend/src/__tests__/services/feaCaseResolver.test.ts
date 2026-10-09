import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import {
  caseStatus,
  clearCaseCache,
  fieldAtStep,
  peekCase,
  resolveCase,
  resolveEnvelope,
  serverCaseEngine,
  subscribeCaseStatus,
  type CaseEngine,
} from "../../services/fea/feaCaseResolver";
import { blobStepIndex, caseFieldView, caseRelativePrefix } from "../../services/fea/feaStepRef";
import { clearFieldBlobCache, fetchFieldStep } from "../../services/feaFieldBlob";
import type {
  ConvertResponse,
  FeaCaseOverlay,
  FeaCombinationStep,
  FeaManifest,
  FeaManifestField,
} from "../../services/viewerApi";

const SRC = "decks/deck.SIN";
const HASH = "ab12cd34".padEnd(64, "0");
const PREFIX = `_derived/${SRC}.fea/cases/101-ab12cd34/`;

const DISP: FeaManifestField = {
  name_canonical: "sesam.nodes.displacement",
  name_native: "d",
  kind: "vector7",
  category: "displacement",
  support: "nodal",
  analysis_kind: "static",
  components: ["X"],
  blob: { url: "fea.sesam.nodes.displacement.bin", header_bytes: 1024, stride_bytes: 8, dtype: "float32", byte_order: "little" },
  n_steps: 2,
  steps: [
    { i: 0, value: 1, label: "1" },
    { i: 1, value: 2, label: "2" },
  ],
  scalar_range: { X: [-1, 1] },
  default_view: { reduction: "X", colormap: "viridis" },
  linear_components: ["X"],
};

const STEP: FeaCombinationStep = {
  n: 101,
  name: "comb101",
  complex: false,
  terms: [
    [1, 1.2000000476837158, 0],
    [2, -0.5, 0],
  ],
  coefficients: [
    [1.2000000476837158, 0],
    [-0.5, 0],
  ],
  needs_raw: false,
  recipe_hash: HASH,
};

const MANIFEST = {
  version: 2,
  bake_version: 4,
  src: SRC,
  mesh: { url: "fea.mesh.glb", n_points: 2, n_cells: 1 },
  fields: [DISP],
  baked_steps: [1, 2],
  combination_steps: [STEP],
  lazy_cases: { version: 1, server: true, client_tier_a: true, cases_prefix: "cases/" },
} as unknown as FeaManifest;

const OVERLAY: FeaCaseOverlay = {
  version: 1,
  kind: "fea_case",
  bake_version: 4,
  src: SRC,
  prefix: PREFIX,
  case: { n: 101, name: "comb101", complex: false, recipe_hash: HASH },
  producer: { engine: "numpy", version: "0", tier: "A" },
  fields: [
    {
      name_canonical: DISP.name_canonical,
      components: ["X"],
      n_steps: 1,
      steps: [{ i: 0, value: 101, label: "101", name: "comb101" }],
      scalar_range: { X: [-0.25, 0.75] },
      blob: { url: "fea.sesam.nodes.displacement.bin", header_bytes: 1024, stride_bytes: 8, dtype: "float32", byte_order: "little" },
    },
  ],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function status(overrides: Partial<ConvertResponse>): ConvertResponse {
  return {
    job_id: "job-c",
    source_key: SRC,
    derived_key: `${PREFIX}fea.case.json`,
    target_format: "fea_case",
    status: "running",
    progress: 0.5,
    stage: "combining",
    error: null,
    cached: false,
    ...overrides,
  } as ConvertResponse;
}

function engine(fetcher: (url: string) => Promise<Response>, convertStatus = async (): Promise<ConvertResponse> => status({ status: "done" })): CaseEngine {
  return serverCaseEngine({ fetcher, convertStatus, apiBase: "/api", pollMs: 1, timeoutMs: 5_000, sleep: async () => {} });
}

const source = { scope: "user:me", sourceKey: SRC };

beforeEach(() => clearCaseCache());

describe("resolveCase (server engine)", () => {
  it("returns a cached overlay on 200, with the case prefix relative to the bake", async () => {
    const urls: string[] = [];
    const r = await resolveCase(MANIFEST, source, 101, {
      field: DISP.name_canonical,
      engine: engine(async (url) => {
        urls.push(url);
        return json(200, OVERLAY);
      }),
    });
    assert.equal(urls.length, 1);
    assert.equal(
      urls[0],
      `/api/scopes/user%3Ame/fea/case?key=${encodeURIComponent(SRC)}&case=101&field=${encodeURIComponent(DISP.name_canonical)}`,
    );
    assert.equal(r.relPrefix, "cases/101-ab12cd34/");
    assert.equal(r.computed, false);
    assert.equal(r.engine, "server");
    assert.deepEqual(caseStatus(SRC, STEP), { status: "ready", computed: false });
    assert.equal(peekCase(MANIFEST, SRC, 101)?.overlay, r.overlay);
  });

  it("202 -> poll -> 200, reporting computing then ready", async () => {
    let gets = 0;
    const seen: string[] = [];
    const off = subscribeCaseStatus(() => seen.push(caseStatus(SRC, STEP).status));
    const polls = [status({ status: "queued", progress: 0 }), status({ status: "running", progress: 0.5 }), status({ status: "done", progress: 1 })];
    const r = await resolveCase(MANIFEST, source, 101, {
      engine: engine(
        async () => (gets++ === 0 ? json(202, { job_id: "job-c", status: "queued", progress: 0, stage: "queued" }) : json(200, OVERLAY)),
        async () => polls.shift()!,
      ),
    });
    off();
    assert.equal(gets, 2);
    assert.equal(r.computed, true);
    assert.ok(seen.includes("computing"));
    assert.equal(seen[seen.length - 1], "ready");
    assert.deepEqual(caseStatus(SRC, STEP), { status: "ready", computed: true });
  });

  it("dedupes concurrent requests for the same case and caches the result", async () => {
    let gets = 0;
    let release!: () => void;
    const gate = new Promise<void>((res) => (release = res));
    const eng = engine(async () => {
      gets++;
      await gate;
      return json(200, OVERLAY);
    });
    const a = resolveCase(MANIFEST, source, 101, { engine: eng });
    const b = resolveCase(MANIFEST, source, 101, { engine: eng });
    release();
    const [ra, rb] = await Promise.all([a, b]);
    assert.equal(gets, 1);
    assert.equal(ra, rb);
    const rc = await resolveCase(MANIFEST, source, 101, { engine: eng });
    assert.equal(rc, ra);
    assert.equal(gets, 1);
  });

  it("an error is the case's status, with the HTTP status (409: stale base)", async () => {
    await assert.rejects(
      resolveCase(MANIFEST, source, 101, { engine: engine(async () => json(409, { detail: "stale" })) }),
      /feaCase/,
    );
    const st = caseStatus(SRC, STEP);
    assert.equal(st.status, "error");
    assert.equal(st.httpStatus, 409);
    // Not cached: the next request asks again.
    const r = await resolveCase(MANIFEST, source, 101, { engine: engine(async () => json(200, OVERLAY)) });
    assert.equal(r.overlay.case.n, 101);
  });

  it("refuses a case the manifest does not list", async () => {
    await assert.rejects(resolveCase(MANIFEST, source, 7, { engine: engine(async () => json(200, OVERLAY)) }), /not a combination/);
    assert.equal(caseStatus(SRC, { n: 7, recipe_hash: "x" }).status, "on-request");
  });

  it("an envelope the server does not offer is null, not an error", async () => {
    const env = await resolveEnvelope(MANIFEST, source, DISP.name_canonical, {
      fetcher: async () => json(404, { detail: "no" }),
      convertStatus: async () => status({}),
      apiBase: "/api",
    });
    assert.equal(env, null);
  });
});

describe("case step refs", () => {
  it("a case view moves the blob under the case prefix, one step, its own range", () => {
    const view = caseFieldView(DISP, OVERLAY, caseRelativePrefix(OVERLAY, SRC))!;
    assert.equal(view.blob!.url, "cases/101-ab12cd34/fea.sesam.nodes.displacement.bin");
    assert.equal(view.n_steps, 1);
    assert.equal(view.steps[0].value, 101);
    assert.deepEqual(view.scalar_range, { X: [-0.25, 0.75] });
    assert.deepEqual(view.linear_components, ["X"]);
    assert.equal(blobStepIndex({ case: 101 }), 0);
    assert.equal(blobStepIndex({ stored: 1 }), 1);
    assert.equal(blobStepIndex(1), 1);
    // Prefix outside the bake dir: fall back to the convention.
    assert.equal(caseRelativePrefix({ ...OVERLAY, prefix: "" }, SRC, MANIFEST), "cases/101-ab12cd34/");
  });

  it("fetchFieldStep reads a case at step 0 of its own blob, a stored step at its index", async () => {
    clearFieldBlobCache();
    const asked: Array<[string, number, number]> = [];
    const range = async (url: string, start: number, end: number) => {
      asked.push([url, start, end]);
      return { buf: new Float32Array([7, 8]).buffer, ranged: true };
    };
    const whole = async () => {
      throw new Error("no whole-blob fetch expected");
    };
    await resolveCase(MANIFEST, source, 101, { engine: engine(async () => json(200, OVERLAY)) });
    const at = await fieldAtStep(MANIFEST, DISP, { case: 101 }, source);
    assert.ok(at);
    const v = await fetchFieldStep(range, whole, at!.field, { case: 101 }, "k");
    assert.deepEqual(Array.from(v), [7, 8]);
    await fetchFieldStep(range, whole, DISP, { stored: 1 }, "k");
    assert.deepEqual(asked, [
      ["cases/101-ab12cd34/fea.sesam.nodes.displacement.bin", 1024, 1031],
      ["fea.sesam.nodes.displacement.bin", 1032, 1039],
    ]);
  });
});
