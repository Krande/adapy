import assert from "node:assert/strict";
import { test } from "node:test";

// A page opened by a run's link restores no job of an earlier session. The link's params
// are stripped from the URL once read, so the answer is captured at module load.

const globals = globalThis as unknown as { location?: { search: string } };
const savedLocation = globals.location;
globals.location = { search: "?procedural=m1&mode=build" };
const { isRunLinkSearch, openedByRunLink } = await import("@/utils/openedByRunLink");
// Stripped the way useUrlParamLoad strips it: the captured answer stands.
globals.location = { search: "?mode=build" };

test("the answer is captured at load and survives the params being stripped", () => {
  assert.equal(openedByRunLink(), true);
});

test("a procedural or file link is a run's link; a plain or scope-only page is not", () => {
  assert.equal(isRunLinkSearch("?procedural=abc"), true);
  assert.equal(isRunLinkSearch("?file=a%2Fb.glb&scope=shared"), true);
  assert.equal(isRunLinkSearch("?scope=shared"), false);
  assert.equal(isRunLinkSearch("?procedural="), false);
  assert.equal(isRunLinkSearch(""), false);
});

test.after(() => {
  globals.location = savedLocation;
});
