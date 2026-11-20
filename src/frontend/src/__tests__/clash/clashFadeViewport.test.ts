// The Clashes tab's "fade rest" / "hide rest" goes through the viewport on screen
// (`ViewportApi.setObjectFade`), not into the three.js scene's own meshes: on the three.js-free
// viewport it faded nothing. Here a viewport that records what it is asked to fade stands in for
// either one, with a saved group's two models loaded: the focused joint's members stay solid in
// their own model, the other model is faded whole, and every way the isolation ends -- the option
// turned off, the focus moved, the run cleared, the scene cleared, another viewport -- puts it back.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ContactRunDoc } from "@/services/clash/contactRuns";
import type { ObjectFade } from "@/viewport/api/objectFade";
import type { ViewportApi } from "@/viewport/api/viewportApi";

await import("../fixtures/panelRealm");

const { contactRunToResult, useClashCheckStore } = await import("@/state/clashCheckStore");
const { useModelState } = await import("@/state/modelState");
const markers = await import("@/utils/scene/clashJointMarkers");
const { clearScene } = await import("@/utils/scene/handlers/clear_scene");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

type Vec3 = [number, number, number];

const MODEL_A = "models/a.ifc";
const MODEL_B = "assets:prov/site/S1@r2";

function contact(id: string, members: [number, number]) {
  const sq = (px: number): Vec3[] => [
    [px, 0.05, 2.5],
    [px, 0.15, 2.5],
    [px, 0.15, 2.7],
    [px, 0.05, 2.7],
    [px, 0.05, 2.5],
  ];
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
    patches: [{ normal: [1, 0, 0] as Vec3, points1: sq(0.2), points2: sq(0.198) }],
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

/** A viewport that records every fade it is asked for (and draws its overlays nowhere). */
function fadingViewport(kind: "three" | "native") {
  const fades: (ObjectFade | null)[] = [];
  const layer = { addPoints: () => {}, addLines: () => {}, addMesh: () => {}, clear: () => {}, dispose: () => {} };
  const viewport = {
    kind,
    sourceOrigin: (name: string) => (useModelState.getState().loadedSourceNames.has(name) ? [0, 0, 0] : null),
    modelBounds: () => [0, 0, 0, 10, 10, 10],
    overlays: () => layer,
    clearModels: () => {},
    setObjectFade: (fade: ObjectFade | null) => void fades.push(fade),
  } as unknown as ViewportApi;
  /** The last fade, as {source: kept ids} and its opacity; null when nothing is faded. */
  const now = () => {
    const f = fades.at(-1) ?? null;
    return f && { keep: Object.fromEntries([...f.keep].map(([s, ids]) => [s, [...ids].sort()])), opacity: Math.round(f.opacity * 100) / 100 };
  };
  return { viewport, fades, now };
}

for (const kind of ["three", "native"] as const) {
  test(`"fade rest" fades the other members of a group's run on the ${kind} viewport, and every way out puts them back`, async () => {
    const rec = fadingViewport(kind);
    let withdraw = setActiveViewport(rec.viewport);
    const ms = useModelState.getState();
    const store = useClashCheckStore;
    try {
      ms.registerLoadedSourceName(MODEL_A);
      ms.registerLoadedSourceName(MODEL_B);
      store.setState({
        result: contactRunToResult(groupRun(), null),
        sourceName: 'group "two models"',
        isolate: "ghost",
        isolateOpacity: 0.2,
        selectedJoint: null,
        selectedGroup: null,
      });
      markers.startClashMarkerSync();
      assert.equal(rec.now(), null, "no joint focused: nothing faded");

      // A joint of model A: its two members solid, the rest of A and the whole of B faded.
      store.setState({ selectedJoint: "c0" });
      assert.deepEqual(rec.now(), { keep: { [MODEL_A]: ["4", "5"], [MODEL_B]: [] }, opacity: 0.2 });

      // The slider: the same fade, fainter.
      store.getState().setIsolateOpacity(0.05);
      assert.deepEqual(rec.now(), { keep: { [MODEL_A]: ["4", "5"], [MODEL_B]: [] }, opacity: 0.05 });

      // The focus moves to a joint of model B: element "4" there is not A's "4".
      store.setState({ selectedJoint: "c1" });
      assert.deepEqual(rec.now(), { keep: { [MODEL_A]: [], [MODEL_B]: ["4", "9"] }, opacity: 0.05 });

      // "Hide rest" is the same at opacity 0; "show all" takes it away.
      store.getState().setIsolate("hidden");
      assert.deepEqual(rec.now()?.opacity, 0);
      store.getState().setIsolate("off");
      assert.equal(rec.now(), null);

      // The run cleared.
      store.getState().setIsolate("ghost");
      assert.ok(rec.now());
      store.setState({ result: null, selectedJoint: null });
      assert.equal(rec.now(), null);

      // Another viewport taking over: the fade is put back on the one that had it and made on the new one.
      store.setState({ result: contactRunToResult(groupRun(), null), selectedJoint: "c0" });
      assert.ok(rec.now());
      const next = fadingViewport(kind);
      withdraw = setActiveViewport(next.viewport);
      assert.equal(rec.now(), null, "the old viewport faded nothing any more");
      assert.deepEqual(next.now()?.keep, { [MODEL_A]: ["4", "5"], [MODEL_B]: [] });

      // The scene cleared (the one `clearScene` path): nothing faded is left behind.
      await clearScene({ reason: "test" });
      assert.equal(next.now(), null);
    } finally {
      store.setState({ result: null, selectedJoint: null, isolate: "ghost", isolateOpacity: 0.15 });
      ms.clearLoadedSources?.();
      withdraw();
    }
  });
}

test("fading the rest reaches into no three.js scene of its own", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("../../utils/scene/clashIsolation.ts", import.meta.url), "utf8");
  assert.ok(!/from "three"/.test(src), "clashIsolation imports no three.js");
  assert.ok(!/CustomBatchedMesh|loadedSourceGroups/.test(src.replace(/^\s*\/\/.*$/gm, "")), "nor the three.js scene's meshes and groups");
});
