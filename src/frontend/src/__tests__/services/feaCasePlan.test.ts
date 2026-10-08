import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { FeaCombinationStep, FeaManifest, FeaManifestField } from "../../services/api/fea";
import {
  buildCaseOverlay,
  caseDirName,
  formatG,
  LocalCaseUnsupported,
  planCase,
  planOutputs,
  stringifyJson,
  type KernelStepStats,
} from "../../services/fea/feaCasePlan";

const HASH = "0123abcd".padEnd(64, "e");
const blob = (url: string, stride: number) => ({ url, header_bytes: 1024, stride_bytes: stride, dtype: "float32", byte_order: "little" as const });
const steps = [
  { i: 0, value: 1, label: "1" },
  { i: 1, value: 2, label: "2" },
  { i: 2, value: 3, label: "3" },
];

function field(name: string, extra: Partial<FeaManifestField>): FeaManifestField {
  return {
    name_canonical: name,
    name_native: name,
    kind: "vectorN",
    category: "stress",
    support: "nodal",
    analysis_kind: "static",
    components: [],
    n_steps: 3,
    steps,
    scalar_range: {},
    default_view: { reduction: "magnitude", colormap: "viridis" },
    ...extra,
  } as FeaManifestField;
}

const DISP = field("disp", {
  category: "displacement",
  components: ["ALL", "X", "Y", "Z"],
  blob: blob("fea.disp.bin", 10 * 16),
  linear_components: ["X", "Y", "Z"],
  derived_components: { ALL: { op: "magnitude3", args: ["X", "Y", "Z"] } },
});
const pt = (t: string, n: number, comps: number) => ({
  elem_type: t,
  n_elements: n,
  n_ips: 2,
  ip_layout: [],
  element_labels: [],
  blob: blob(`fea.${comps}.${t}.elements.bin`, n * 2 * comps * 4),
  scalar_range: {},
});
const G = field("g", {
  support: "gauss",
  components: ["SIGXX", "SIGYY", "TAUXY", "VONMISES"],
  per_type: [pt("quad", 5, 4), pt("triangle", 3, 4)],
  linear_components: ["SIGXX", "SIGYY", "TAUXY"],
  derived_components: { VONMISES: { op: "plane_von_mises", args: ["SIGXX", "SIGYY", "TAUXY"] } },
});
const P = field("p", {
  support: "gauss",
  components: ["P1", "P2"],
  per_type: [pt("triangle", 3, 2), pt("quad", 5, 2)],
  linear_components: [],
  derived_components: {
    P1: { op: "plane_principal_1", args: ["SIGXX", "SIGYY", "TAUXY"], field: "g" },
    P2: { op: "plane_principal_2", args: ["SIGXX", "SIGYY", "TAUXY"], field: "g" },
  },
});
const PROP = field("thickness", { category: "property", components: ["TH"], blob: blob("fea.th.bin", 40), steps: [{ i: 0, value: 0, label: "0" }] });

const STEP: FeaCombinationStep = {
  n: 101,
  name: "lcc1",
  complex: false,
  terms: [
    [3, 1.2000000476837158, 0],
    [1, -0.5, 0],
  ],
  coefficients: [
    [1.2000000476837158, 0],
    [-0.5, -0],
  ],
  needs_raw: false,
  recipe_hash: HASH,
};

const MANIFEST = {
  version: 2,
  bake_version: 4,
  src: "deck",
  mesh: { url: "fea.mesh.glb", n_points: 10, n_cells: 8 },
  fields: [DISP, G, P, PROP],
  combination_steps: [STEP],
  lazy_cases: { version: 1, server: true, client_tier_a: true, cases_prefix: "cases/" },
} as unknown as FeaManifest;

describe("planCase", () => {
  it("maps terms to the field's own steps, coefficients to float32 factors, derivations to column ops", () => {
    const plan = planCase(MANIFEST, STEP);
    assert.equal(plan.caseDir, "101-0123abcd");
    assert.deepEqual(plan.fields, ["disp", "g", "p"]);
    const disp = plan.jobs.find((j) => j.field === "disp")!;
    assert.deepEqual(disp.steps, [2, 0]);
    assert.deepEqual(disp.factors, [Math.fround(1.2000000476837158), -0.5]);
    assert.deepEqual(disp.derive, { name: "disp", ops: [{ op: "magnitude3", args: [1, 2, 3], out: [0] }] });
    assert.equal(disp.inUrl, disp.outUrl);
    assert.equal(plan.jobs.filter((j) => j.field === "g").length, 2);
    assert.deepEqual(plan.jobs.find((j) => j.field === "g")!.derive.ops, [{ op: "plane_von_mises", args: [0, 1, 2], out: [3] }]);
  });

  it("derives a field from another as a new layout over the source's blob of the same element type", () => {
    const plan = planCase(MANIFEST, STEP);
    const tri = plan.jobs.find((j) => j.field === "p" && j.elemType === "triangle")!;
    assert.equal(tri.inUrl, "fea.4.triangle.elements.bin");
    assert.equal(tri.outUrl, "fea.2.triangle.elements.bin");
    assert.deepEqual(tri.derive, {
      name: "p",
      n_components: 2,
      ops: [
        { op: "plane_principal_1", args: [0, 1, 2], out: [0] },
        { op: "plane_principal_2", args: [0, 1, 2], out: [1] },
      ],
    });
    // Strides needed: each source blob's steps once, sorted; the P blobs are never read.
    const need = plan.needs.find((n) => n.url === "fea.4.quad.elements.bin")!;
    assert.deepEqual(need.steps, [0, 2]);
    assert.ok(!plan.needs.some((n) => n.url.startsWith("fea.2.")));
    assert.deepEqual(planOutputs(plan).at(-1), "fea.case.json");
  });

  it("refuses what only the server can do", () => {
    const refuse = (m: FeaManifest, s: FeaCombinationStep = STEP, re?: RegExp) =>
      assert.throws(() => planCase(m, s), (e: unknown) => e instanceof LocalCaseUnsupported && (!re || re.test((e as Error).message)));
    refuse(MANIFEST, { ...STEP, needs_raw: true, raw_reason: "complex basic case 7 at a non-zero phase" }, /complex/);
    refuse(MANIFEST, { ...STEP, terms: [[9, 1, 0]], coefficients: [[1, 0]] }, /no baked step 9/);
    refuse({ ...MANIFEST, lazy_cases: { version: 1, server: true, client_tier_a: false } } as FeaManifest);
    const { linear_components: _l, ...noRule } = DISP;
    refuse({ ...MANIFEST, fields: [noRule as FeaManifestField] } as FeaManifest, STEP, /no superposition rule/);
    const mixed = { ...P, components: ["P1", "P2", "ANGLE"] };
    refuse({ ...MANIFEST, fields: [G, mixed] } as FeaManifest, STEP, /mixes/);
    refuse({ ...MANIFEST, fields: [P] } as FeaManifest, STEP, /source g/);
  });
});

describe("buildCaseOverlay", () => {
  const stats = (rows: Array<[number | null, number | null]>, mag: [number | null, number | null] = [0, 0]): KernelStepStats => ({
    scalar_range_per_component: rows,
    scalar_range_magnitude: mag,
  });

  it("writes combine.write_case's overlay: per-step ranges, per-type roll-up, magnitude", () => {
    const plan = planCase(MANIFEST, STEP, ["disp", "p"]);
    const s = new Map<string, KernelStepStats>([
      ["fea.disp.bin", stats([[0, 2], [-1, 1], [null, null], [-0, 0]], [0.5, 2])],
      ["fea.2.triangle.elements.bin", stats([[1, 5], [-3, 0]])],
      ["fea.2.quad.elements.bin", stats([[0, 4], [-4, -1]])],
    ]);
    const o = buildCaseOverlay(MANIFEST, plan, s, { engine: "adacpp-wasm", version: "1", tier: "A" }, "_derived/deck.fea/cases/101-0123abcd/");
    assert.deepEqual(Object.keys(o.case), ["n", "name", "complex", "terms", "coefficients", "recipe_hash"]);
    const disp = o.fields[0];
    assert.deepEqual(disp.steps, [{ i: 0, value: 101, label: "101", name: "lcc1" }]);
    assert.deepEqual(disp.scalar_range, { ALL: [0, 2], X: [-1, 1], Y: [0, 0], Z: [-0, 0], magnitude: [0.5, 2] });
    assert.equal(disp.blob!.url, "fea.disp.bin");
    const p = o.fields[1];
    assert.deepEqual(p.per_type!.map((b) => b.elem_type), ["triangle", "quad"]);
    assert.deepEqual(p.scalar_range, { P1: [0, 5], P2: [-4, 0], magnitude: [0, 0] });
    assert.deepEqual(p.per_type![1].scalar_range, { P1: [0, 4], P2: [-4, -1] });
    assert.equal(o.prefix, "_derived/deck.fea/cases/101-0123abcd/");
    // -0 survives the trip through the store's JSON, as Python writes it.
    const text = stringifyJson(o);
    assert.ok(text.includes("[-0.5,-0.0]"));
    assert.ok(Object.is(JSON.parse(text).fields[0].scalar_range.Z[0], -0));
  });

  it("formats step labels like Python's :g", () => {
    assert.equal(formatG(101), "101");
    assert.equal(formatG(1.5), "1.5");
    assert.equal(formatG(1234567), "1.23457e+06");
    assert.equal(formatG(0.0001), "0.0001");
    assert.equal(caseDirName({ n: 7, recipe_hash: HASH }), "7-0123abcd");
  });
});
