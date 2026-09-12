import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import * as THREE from "three";

import type { FeaManifestField } from "../../services/viewerApi";
import { applyElemFieldToMesh } from "../../utils/scene/fea/applyElemField";

// The warp scale must survive every repaint.
//
// The morph influence on the FEA mesh is the slider position times the warp
// scale. A repaint -- the warp toggle, a component, a step, a colormap -- goes
// back through load_fea_streaming, and every field paint in there writes the
// influence it is handed. One of them was handed nothing: the beam-solid paint
// of an element field. Its default of 1 landed on the MAIN mesh too, because
// once a displacement has been installed the beam solids share the main mesh's
// influence array. On a deck that needed an auto-derived scale, a warp at 1 is
// indistinguishable from no warp, so turning warp on or off changed nothing
// visible.

const basePositions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);

function makeMesh(): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(basePositions.slice(), 3),
  );
  geometry.setIndex([0, 1, 2]);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  Object.assign(mesh, { drawRanges: new Map([["E7", [0, 3]]]) });
  return mesh;
}

function makeField(): FeaManifestField {
  return {
    name_canonical: "sesam.elements.g_stress",
    name_native: "sesam.elements.g_stress",
    kind: "scalar",
    category: "stress",
    support: "element_nodal",
    analysis_kind: "static",
    components: ["SIGXX"],
    per_type: [
      {
        elem_type: "triangle",
        n_elements: 1,
        n_ips: 3,
        ip_layout: [],
        element_labels: [7],
        blob: {
          url: "field.bin",
          header_bytes: 1024,
          stride_bytes: 12,
          dtype: "float32",
          byte_order: "little",
        },
        scalar_range: { SIGXX: [0, 2] },
      },
    ],
    n_steps: 1,
    steps: [{ i: 0, value: 1, label: "1" }],
    scalar_range: { SIGXX: [0, 2] },
    default_view: { reduction: "SIGXX", colormap: "viridis" },
  };
}

function paint(mesh: THREE.Mesh, displacementScale?: number): void {
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: makeField(),
    perTypeStepValues: [new Float32Array([0, 1, 2])],
    layer: "all",
    ipReduction: "mean",
    reduction: "SIGXX",
    colormap: "viridis",
    ...(displacementScale === undefined ? {} : { displacementScale }),
  });
}

test("a paint of a mesh that shares its influences keeps the caller's scale", () => {
  const main = makeMesh();
  const solids = makeMesh();
  paint(main, 50);
  // What installBeamSolidWarp does, so one slider write moves both meshes.
  solids.morphTargetInfluences = main.morphTargetInfluences;
  paint(solids, 50);
  assert.equal(main.morphTargetInfluences![0], 50);
});

test("left to its default, that paint drops the shared influence to 1", () => {
  // The mechanism, pinned so nobody reads the default as harmless again.
  const main = makeMesh();
  const solids = makeMesh();
  paint(main, 50);
  solids.morphTargetInfluences = main.morphTargetInfluences;
  paint(solids);
  assert.equal(main.morphTargetInfluences![0], 1);
});

// The loader and its fea/streaming package: the paints live in the package's
// modules, the step callback in the loader that orchestrates them.
const streamingDir = fileURLToPath(
  new URL("../../utils/scene/fea/streaming/", import.meta.url),
);
const loaderSource = [
  fileURLToPath(
    new URL("../../utils/scene/handlers/load_fea_streaming.ts", import.meta.url),
  ),
  ...readdirSync(streamingDir)
    .filter((name) => name.endsWith(".ts"))
    .sort()
    .map((name) => join(streamingDir, name)),
]
  .map((path) => readFileSync(path, "utf8"))
  .join("\n");

/** The text of every `fn({ ... })` call in the loader package, braces balanced. */
function callsOf(fn: string): string[] {
  const out: string[] = [];
  const needle = `${fn}({`;
  let at = loaderSource.indexOf(needle);
  while (at >= 0) {
    let depth = 0;
    let end = at + fn.length + 1;
    for (; end < loaderSource.length; end++) {
      const c = loaderSource[end];
      if (c === "{") depth++;
      else if (c === "}" && --depth === 0) break;
    }
    out.push(loaderSource.slice(at, end + 1));
    at = loaderSource.indexOf(needle, end);
  }
  return out;
}

test("every field paint in the loader passes the influence it was asked for", () => {
  const calls = [
    ...callsOf("applyElemFieldToMesh"),
    ...callsOf("applyFieldToMesh"),
  ];
  // The two element-field paints (main mesh, beam solids) and the nodal one.
  assert.ok(calls.length >= 3, `found ${calls.length} paint calls`);
  for (const call of calls) {
    assert.match(call, /\bdisplacementScale\b/, call.split("\n").slice(0, 3).join(" "));
  }
});

test("a step change repaints at the scale on screen, not at the default", () => {
  const at = loaderSource.indexOf("setApplyStep(");
  assert.ok(at >= 0, "the loader registers a step callback");
  const closure = loaderSource.slice(at, loaderSource.indexOf("});", at));
  assert.match(closure, /displacementScale:\s*factor\s*\*\s*scaleFactor/);
});
