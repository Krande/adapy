// The Clashes tab's 3D overlay -- a dot per joint, a convex-contact run's patches, outlines and
// normals -- is drawn through the viewport on screen, not added to a three.js scene: a check of a
// saved group showed its rows and nothing in 3D on the three.js-free viewport, which never draws a
// three.js object and has none of the three.js scene's source groups the overlay looked its models up
// in. Here a viewport that records what it is asked to draw stands in for either one, with the
// group's two models loaded at two different origins.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ContactRunDoc } from "@/services/clash/contactRuns";
import type { OverlayLines, OverlayMesh, OverlayPoints } from "@/viewport/api/overlayLayer";
import type { ViewportApi } from "@/viewport/api/viewportApi";

await import("../fixtures/panelRealm");

const { contactRunToResult, useClashCheckStore } = await import("@/state/clashCheckStore");
const { useModelState } = await import("@/state/modelState");
const markers = await import("@/utils/scene/clashJointMarkers");
const { clashPickAt, takeClashClick } = await import("@/utils/scene/clashOverlayPicks");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

type Vec3 = [number, number, number];

const MODEL_A = "models/a.ifc";
const MODEL_B = "assets:prov/site/S1@r2";
const ORIGINS: Record<string, Vec3> = { [MODEL_A]: [100, 0, 0], [MODEL_B]: [0, 200, 0] };

/** A square patch in the plane x = 0.2, on both members. */
function patch() {
  const sq = (px: number): Vec3[] => [
    [px, 0.05, 2.5],
    [px, 0.15, 2.5],
    [px, 0.15, 2.7],
    [px, 0.05, 2.7],
    [px, 0.05, 2.5],
  ];
  return { normal: [1, 0, 0] as Vec3, points1: sq(0.2), points2: sq(0.198) };
}

function contact(id: string, members: [number, number]) {
  return {
    id,
    members,
    near_points: [
      [0.2, 0.1, 2.6],
      [0.198, 0.1, 2.6],
    ] as [Vec3, Vec3],
    normal: [1, 0, 0] as Vec3,
    distance: -0.002,
    contact_area: 0.02,
    patches: [patch()],
  };
}

/** A saved group's run over two models: contact c0 between members of model A, c1 of model B. */
function groupRun(): ContactRunDoc {
  return {
    schema: "ada.clash/contacts@1",
    run_id: "r1",
    created_at: "2026-11-19T12:00:00Z",
    engine: "wasm",
    checker: "convex-contact",
    target: { kind: "group", group_id: "g1" },
    geometry: {
      key: "group:g1",
      sha256: "00",
      up_axis: "z",
      parts: [
        { key: "_derived/models/a.ifc.glb", sha256: "aa", source: { kind: "file", source_key: MODEL_A } },
        {
          key: "_derived/b/model.glb",
          sha256: "bb",
          source: { kind: "node", provider: "prov", collection: "site", subject: "S1", revision: "r1", node: null },
        },
      ],
    },
    options: { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 },
    members: [
      { id: "0:4", name: "col", element: "4", part: 0 },
      { id: "0:5", name: "beam", element: "5", part: 0 },
      { id: "1:4", name: "col", element: "4", part: 1 },
      { id: "1:9", name: "beam", element: "9", part: 1 },
    ],
    contacts: [contact("c0", [0, 1]), contact("c1", [2, 3])],
    provenance: {},
    group: { id: "g1", name: "two models", members: [] },
  } as ContactRunDoc;
}

/** A viewport that records the overlays it is asked to draw, per layer, and projects the world to the
 *  screen by dropping z (x, y in pixels; depth 0.5). */
function recordingViewport(kind: "three" | "native") {
  const layers = new Map<string, { points: OverlayPoints[]; lines: OverlayLines[]; meshes: OverlayMesh[] }>();
  const layer = (name: string) => {
    let l = layers.get(name);
    if (!l) layers.set(name, (l = { points: [], lines: [], meshes: [] }));
    return l;
  };
  const viewport = {
    kind,
    sourceOrigin: (name: string) => (useModelState.getState().loadedSourceNames.has(name) ? (ORIGINS[name] ?? null) : null),
    modelBounds: () => [0, 0, 0, 10, 10, 10],
    overlays: (name: string) => {
      const l = layer(name);
      return {
        addPoints: (p: OverlayPoints) => l.points.push(p),
        addLines: (s: OverlayLines) => l.lines.push(s),
        addMesh: (m: OverlayMesh) => l.meshes.push(m),
        clear: () => {
          l.points.length = 0;
          l.lines.length = 0;
          l.meshes.length = 0;
        },
        dispose: () => void layers.delete(name),
      };
    },
    projectToScreen: (world: Float32Array, count: number, out: Float32Array) => {
      for (let i = 0; i < count; i++) {
        out[i * 3] = world[i * 3];
        out[i * 3 + 1] = world[i * 3 + 1];
        out[i * 3 + 2] = 0.5;
      }
      return { width: 1000, height: 1000 };
    },
  } as unknown as ViewportApi;
  return { viewport, layers, layer };
}

function points(l: { points: OverlayPoints[] }): number[][] {
  const out: number[][] = [];
  for (const p of l.points) for (let i = 0; i < p.positions.length; i += 3) out.push([...p.positions.slice(i, i + 3)]);
  return out;
}

for (const kind of ["three", "native"] as const) {
  test(`a saved group's run is drawn on the ${kind} viewport, each contact at the origin of its own model`, () => {
    const rec = recordingViewport(kind);
    const withdraw = setActiveViewport(rec.viewport);
    const ms = useModelState.getState();
    try {
      // Loaded as the Scene panel's saved group loads them: model names in the model store only --
      // no three.js scene groups (the three.js-free viewport has none).
      ms.registerLoadedSourceName(MODEL_A);
      ms.registerLoadedSourceName(MODEL_B);
      useClashCheckStore.setState({ result: contactRunToResult(groupRun(), null), sourceName: 'group "two models"', showMarkers: true, showContacts: true });
      markers.startClashMarkerSync();

      const dots = points(rec.layer(markers.CLASH_MARKER_LAYER));
      assert.equal(dots.length, 2, "a dot per joint");
      // A joint's centre (the patch's) plus the origin of the model its members are in.
      const [c0, c1] = dots;
      assert.ok(Math.abs(c0[0] - 100.2) < 0.01 && Math.abs(c0[1] - 0.1) < 0.01, `c0 at model A's origin: ${c0}`);
      assert.ok(Math.abs(c1[0] - 0.2) < 0.01 && Math.abs(c1[1] - 200.1) < 0.01, `c1 at model B's origin: ${c1}`);
      assert.ok(rec.layer(markers.CLASH_MARKER_LAYER).points.every((p) => p.onTop && p.fitted === false));

      const contacts = rec.layer(markers.CLASH_CONTACT_LAYER);
      assert.ok(contacts.meshes.length >= 2, "the patch fills and the arrow heads");
      assert.equal(contacts.lines.length, 1, "the outlines and the normals");
      // Every fill vertex is in one model's frame or the other's, moved by its origin.
      const fill = contacts.meshes[0];
      for (let i = 0; i < fill.positions.length; i += 3) {
        const x = fill.positions[i], y = fill.positions[i + 1];
        assert.ok((Math.abs(x - 100.2) < 0.01 && y < 1) || (Math.abs(x - 0.2) < 0.01 && y > 199), `${x},${y}`);
      }

      // A click on a dot names its joint; one off the overlay names nothing.
      assert.equal(clashPickAt(rec.viewport, c1[0] + 1, c1[1]), "c1");
      assert.equal(clashPickAt(rec.viewport, 500, 500), null);
      assert.equal(takeClashClick(rec.viewport, 500, 500), false);

      // A model unloaded takes its contacts' marks with it.
      ms.unregisterLoadedSource(MODEL_B);
      assert.equal(points(rec.layer(markers.CLASH_MARKER_LAYER)).length, 1, "only model A's joint is left");
      ms.unregisterLoadedSource(MODEL_A);
      assert.equal(points(rec.layer(markers.CLASH_MARKER_LAYER)).length, 0);
      assert.equal(rec.layer(markers.CLASH_CONTACT_LAYER).meshes.length, 0);
    } finally {
      useClashCheckStore.setState({ result: null });
      ms.clearLoadedSources?.();
      withdraw();
    }
  });
}

test("the overlay draws no three.js object of its own", async () => {
  const { readFile } = await import("node:fs/promises");
  for (const file of ["clashJointMarkers.ts", "clashContactOverlay.ts", "clashOverlayPicks.ts"]) {
    const src = await readFile(new URL(`../../utils/scene/${file}`, import.meta.url), "utf8");
    assert.ok(!/from "three"/.test(src), `${file} imports no three.js`);
  }
});
