// The connection builder builds only once a build was asked for.
//
// Opening its panel starts the live loop, and the loop used to build the selected spec as soon as
// the library reported in -- so a panel opened by accident put a joint in the scene. The loop's own
// rebuilds (a reloaded library, another spec, an edited input) now wait for `live`, which the Build
// button sets, and which `ada dev connections` sets at boot because showing joints is all it does.
//
// The store's half is asserted by running it. The session's half is asserted on the source: its
// module loads the scene's loaders, which need a canvas, and the defect is "a build is asked for
// on a path nobody asked on", which is a property of who calls `requestBuild`.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { useConnectionBuilderStore } from "../../state/connectionBuilderStore";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (rel: string) => readFileSync(resolve(root, rel), "utf8");

/** The body of the function declared with `head`, up to the next line that closes it at column 0. */
function bodyOf(src: string, head: string): string {
  const start = src.indexOf(head);
  assert.ok(start >= 0, `${head} must exist`);
  return src.slice(start, src.indexOf("\n}", start));
}

test("the loop is not live until somebody asks", () => {
  assert.equal(useConnectionBuilderStore.getState().live, false);
  useConnectionBuilderStore.getState().setLive(true);
  assert.equal(useConnectionBuilderStore.getState().live, true);
  useConnectionBuilderStore.getState().setLive(false);
});

test("only an asked-for build, or a live loop, reaches requestBuild", () => {
  const src = read("services/devConnections/connectionBuilderSession.ts");
  // Not exported: nothing outside the session can build past the gate.
  assert.ok(!/export function requestBuild/.test(src), "requestBuild must stay private to the session");
  const calls = src.split("requestBuild(").length - 1;
  assert.equal(calls, 3, "requestBuild: its definition, buildNow and rebuildIfLive, and no other caller");

  const now = bodyOf(src, "export function buildNow()");
  assert.ok(now.includes("setLive(true)") && now.includes("requestBuild(0)"), "buildNow sets the loop live and builds");

  const gated = bodyOf(src, "function rebuildIfLive(");
  assert.match(gated, /if \(useConnectionBuilderStore\.getState\(\)\.live\) requestBuild\(delayMs\)/);

  // The loop's three triggers: a reloaded library, another spec, an edited input.
  assert.equal(src.split("rebuildIfLive(").length - 1, 4, "the three triggers, and the definition");
});

test("only the connections dev server starts live", () => {
  const src = read("hooks/useConnectionBuilderBoot.ts");
  assert.equal(src.split("setLive(").length - 1, 1, "one place sets the loop live at boot");
  const deployed = src.slice(src.indexOf("function startDeployed("));
  assert.ok(!deployed.includes("setLive("), "a deployed studio waits for the Build button");
  // The dev branch: after the early return for a deployed studio, before the session starts.
  assert.match(
    src,
    /if \(!runtime\.devConnections\(\)\) return startDeployed\([^\n]*\n\s*useConnectionBuilderStore\.getState\(\)\.setLive\(true\);\s*\n\s*startConnectionBuilderSession\(\);/,
  );
});

test("the panel's button asks for the build", () => {
  const src = read("components/component_view/ConnectionBuilder.tsx");
  assert.ok(src.includes("onClick={() => buildNow()}"), "the Build button calls buildNow");
  assert.ok(!src.includes("requestBuild"), "the panel has no way around the gate");
  assert.ok(src.includes('{live ? "Rebuild" : "Build"}'), "it reads Build until the loop is live");
});
