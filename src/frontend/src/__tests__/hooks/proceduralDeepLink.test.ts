// ``?procedural=<id>`` opens a procedural model in the cell builder, the way a row in
// the Storage browser opens it (useUrlParamLoad reads the parameter; this is the open).

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { PROCEDURAL_PARAM, openProceduralFromUrl } from "@/hooks/proceduralDeepLink";
import { viewerApi } from "@/services/viewerApi";
import { useCellBuilderStore } from "@/state/cellBuilderStore";

const realGet = viewerApi.getProceduralModel;

afterEach(() => {
  viewerApi.getProceduralModel = realGet;
});

test("the parameter is ?procedural=", () => {
  assert.equal(PROCEDURAL_PARAM, "procedural");
});

test("the model is fetched in the scope given and opened in the cell builder", async () => {
  const asked: Array<[string, string]> = [];
  viewerApi.getProceduralModel = (async (scope: string, id: string) => {
    asked.push([scope, id]);
    return {
      id,
      name: "beam-frame",
      revision: 3,
      doc: { grid: {}, spaces: [], equipments: [], openings: [] },
    };
  }) as unknown as typeof viewerApi.getProceduralModel;

  assert.equal(await openProceduralFromUrl("shared", "0b6c7f2e-1111-4222-8333-944455556666"), true);
  assert.deepEqual(asked, [["shared", "0b6c7f2e-1111-4222-8333-944455556666"]]);
  const active = useCellBuilderStore.getState().active;
  assert.equal(active?.modelId, "0b6c7f2e-1111-4222-8333-944455556666");
  assert.equal(active?.revision, 3);
});

test("a model that cannot be fetched opens nothing and throws nothing", async () => {
  viewerApi.getProceduralModel = (async () => {
    throw new Error("404 procedural model not found");
  }) as unknown as typeof viewerApi.getProceduralModel;
  assert.equal(await openProceduralFromUrl("shared", "missing"), false);
});

test("the deep link strips what it read and leaves the rest (the shell's ?mode=)", async () => {
  const { withoutDeepLinkParams } = await import("@/hooks/deepLinkParams");
  assert.equal(
    withoutDeepLinkParams("?ui=demo&scope=shared&file=a%2Fb.SIN&mode=capacity"),
    "?ui=demo&mode=capacity",
  );
  assert.equal(withoutDeepLinkParams("?scope=shared&procedural=abc&mode=build"), "?mode=build");
  assert.equal(withoutDeepLinkParams("?scope=shared&file=x.glb&derived=_derived%2Fx.glb"), "");
});

test("?grid= and ?snap= set the builder's grid step and snap distance", async () => {
  const { applyViewParams } = await import("@/hooks/viewParams");
  const { withoutDeepLinkParams } = await import("@/hooks/deepLinkParams");
  const before = useCellBuilderStore.getState();
  assert.deepEqual(applyViewParams("?scope=shared&grid=0.65&snap=0.65"), { grid: 0.65, snap: 0.65 });
  assert.equal(useCellBuilderStore.getState().gridStep, 0.65);
  assert.equal(useCellBuilderStore.getState().snapThreshold, 0.65);
  // Nonsense is ignored, and what is not given is left alone.
  assert.deepEqual(applyViewParams("?grid=-1&snap=abc"), { grid: null, snap: null });
  assert.equal(useCellBuilderStore.getState().gridStep, 0.65);
  // Kept in the address bar: a reload keeps the model's grid.
  assert.equal(withoutDeepLinkParams("?file=a.glb&grid=0.65"), "?grid=0.65");
  before.setGridStep(0.1);
  before.setSnapThreshold(0.25);
});
