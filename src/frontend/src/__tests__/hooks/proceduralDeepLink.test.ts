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
    withoutDeepLinkParams("?ui=asa&scope=shared&file=a%2Fb.SIN&mode=capacity"),
    "?ui=asa&mode=capacity",
  );
  assert.equal(withoutDeepLinkParams("?scope=shared&procedural=abc&mode=build"), "?mode=build");
  assert.equal(withoutDeepLinkParams("?scope=shared&file=x.glb&derived=_derived%2Fx.glb"), "");
});
