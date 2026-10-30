import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import type { FeaManifest, FeaManifestField } from "../../services/api/fea";
import {
  clearFeaSidecarCache,
  elementNodes,
  fieldNeedsDetail,
  manifestGroups,
  manifestNodeLabels,
  withFieldDetail,
} from "../../services/fea/feaFieldDetail";
import { caseFieldView } from "../../services/fea/feaStepRef";

// A split manifest (bake_version 6) keeps each element field's element labels /
// node indices, the sets and the node ids in JSON sidecars; these resolve them
// lazily, once, and leave an inline (older) manifest untouched.

const DETAIL = "fea.detail.0123456789abcdef.json";

const enc = (v: unknown) => new TextEncoder().encode(JSON.stringify(v)).buffer as ArrayBuffer;

function fakeFetcher(files: Record<string, unknown>) {
  const calls: string[] = [];
  const fetcher = async (name: string) => {
    calls.push(name);
    if (!(name in files)) throw new Error(`404 ${name}`);
    return enc(files[name]);
  };
  return { fetcher, calls };
}

const bucket = (elem_type: string, n: number) => ({
  elem_type,
  n_elements: n,
  n_ips: 1,
  ip_layout: [],
  blob: { url: `fea.S.${elem_type}.bin`, header_bytes: 1024, stride_bytes: 4 * n, dtype: "float32", byte_order: "little" as const },
  scalar_range: { S: [0, 1] as [number, number] },
});

function splitField(name: string, category: FeaManifestField["category"] = "stress"): FeaManifestField {
  return {
    name_canonical: name,
    name_native: name,
    kind: "scalar",
    category,
    support: "element_average",
    analysis_kind: "static",
    components: ["S"],
    per_type: [bucket("quad", 2), bucket("line", 1)],
    detail: { url: DETAIL, version: 1 },
    n_steps: 1,
    steps: [{ i: 0, value: 1, label: "1" }],
    scalar_range: { S: [0, 1] },
    default_view: { reduction: "S", colormap: "viridis" },
  };
}

const DETAIL_DOC = {
  version: 1,
  per_type: [
    { elem_type: "quad", element_labels: [11, 12], element_node_indices: [[0, 1, 2, 3], [1, 2, 4, 5]] },
    { elem_type: "line", element_labels: [20], element_node_indices: [[5, 6]] },
  ],
};

function splitManifest(): FeaManifest {
  return {
    version: 2,
    bake_version: 6,
    src: "deck.sin",
    mesh: { url: "fea.mesh.glb", n_points: 7, n_cells: 3, node_labels_url: "fea.nodelabels.json" },
    fields: [splitField("S"), splitField("props.material", "property")],
    groups_url: "fea.groups.json",
    n_groups: 1,
  };
}

const FILES = {
  [DETAIL]: DETAIL_DOC,
  "fea.nodelabels.json": [100, 101, 102, 103, 104, 105, 106],
  "fea.groups.json": [{ name: "deck", members: ["EL11", "EL12"], fe_object_type: "element" }],
};

describe("feaFieldDetail", () => {
  beforeEach(() => clearFeaSidecarCache());

  it("fills a split field's buckets from its detail sidecar, fetched once and shared", async () => {
    const { fetcher, calls } = fakeFetcher(FILES);
    const [s, props] = splitManifest().fields;
    assert.equal(fieldNeedsDetail(s), true);
    const [a, b] = await Promise.all([withFieldDetail(s, fetcher, "k"), withFieldDetail(props, fetcher, "k")]);
    assert.deepEqual(a.per_type![0].element_labels, [11, 12]);
    assert.deepEqual(a.per_type![1].element_node_indices, [[5, 6]]);
    // Everything the index carried survives.
    assert.equal(a.per_type![0].blob.url, "fea.S.quad.bin");
    assert.deepEqual(b.per_type![1].element_labels, [20]);
    // Both fields name the same content-addressed sidecar: one fetch.
    assert.deepEqual(calls, [DETAIL]);
    await withFieldDetail(s, fetcher, "k");
    assert.deepEqual(calls, [DETAIL]);
    assert.equal(fieldNeedsDetail(a), false);
    // The input field is not mutated.
    assert.equal(s.per_type![0].element_labels, undefined);
  });

  it("passes an inline (pre-split) field through without a fetch", async () => {
    const { fetcher, calls } = fakeFetcher({});
    const inline: FeaManifestField = { ...splitField("S"), detail: undefined };
    inline.per_type = inline.per_type!.map((bk, i) => ({ ...bk, ...DETAIL_DOC.per_type[i] }));
    assert.equal(fieldNeedsDetail(inline), false);
    assert.equal(await withFieldDetail(inline, fetcher, "k"), inline);
    assert.deepEqual(calls, []);
  });

  it("resolves a case view through the base field's detail", async () => {
    const { fetcher } = fakeFetcher(FILES);
    const base = splitField("S");
    const view = caseFieldView(
      base,
      {
        version: 1,
        kind: "fea_case",
        prefix: "_derived/deck.sin.fea/cases/101-abcdef01/",
        case: { n: 101, recipe_hash: "abcdef01".padEnd(64, "0") },
        fields: [{ name_canonical: "S", n_steps: 1, steps: [], scalar_range: { S: [0, 2] } }],
      },
      "cases/101-abcdef01/",
    )!;
    const hydrated = await withFieldDetail(view, fetcher, "k");
    assert.equal(hydrated.per_type![0].blob.url, "cases/101-abcdef01/fea.S.quad.bin");
    assert.deepEqual(hydrated.per_type![0].element_labels, [11, 12]);
  });

  it("retries a sidecar whose fetch failed", async () => {
    let fail = true;
    const fetcher = async (name: string) => {
      if (fail) throw new Error("offline");
      return enc((FILES as Record<string, unknown>)[name]);
    };
    const s = splitManifest().fields[0];
    await assert.rejects(withFieldDetail(s, fetcher, "k"));
    fail = false;
    assert.deepEqual((await withFieldDetail(s, fetcher, "k")).per_type![0].element_labels, [11, 12]);
  });

  it("reads sets and node ids from the sidecars, or inline when the manifest has them", async () => {
    const { fetcher, calls } = fakeFetcher(FILES);
    const m = splitManifest();
    assert.deepEqual((await manifestGroups(m, fetcher, "k")).map((g) => g.name), ["deck"]);
    assert.deepEqual(await manifestNodeLabels(m, fetcher, "k"), FILES["fea.nodelabels.json"]);
    const inline = { ...m, groups: [{ name: "x", members: [] }], mesh: { ...m.mesh, node_labels: [9] } };
    calls.length = 0;
    assert.deepEqual((await manifestGroups(inline, fetcher, "k")).map((g) => g.name), ["x"]);
    assert.deepEqual(await manifestNodeLabels(inline, fetcher, "k"), [9]);
    assert.deepEqual(calls, []);
    assert.deepEqual(await manifestGroups({ ...m, groups_url: undefined }, fetcher, "k"), []);
  });

  it("finds a picked element's type and solver node numbers", async () => {
    const { fetcher } = fakeFetcher(FILES);
    const m = splitManifest();
    assert.deepEqual(await elementNodes(m, 12, fetcher, "k"), { elemType: "quad", nodes: [101, 102, 104, 105] });
    assert.deepEqual(await elementNodes(m, 20, fetcher, "k"), { elemType: "line", nodes: [105, 106] });
    assert.equal(await elementNodes(m, 999, fetcher, "k"), null);
  });
});
