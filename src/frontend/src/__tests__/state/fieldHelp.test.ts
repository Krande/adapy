// The builder's field help: read from the engine catalog by doc list and column, for the engine
// the builder compiles with now (the server has put adapy's text under the engine's own).

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { engineFieldHelp, fieldHelp, fieldHelpText } = await import("@/state/cellbuilder/fieldHelp");
const { readFileSync } = await import("node:fs");

const engines = [
  { slug: "adapy-default", field_help: { spaces: { NAME: "Name of space" } } },
  { slug: "other", field_help: { spaces: { NAME: "The space's name.\nUnique." }, supports: { NODE: "  " } } },
  { slug: "bare" },
];

test("an engine's help is looked up by list and column; blank or missing text is none", () => {
  assert.equal(fieldHelpText(engineFieldHelp(engines as never, "other"), "spaces", "NAME"), "The space's name.\nUnique.");
  assert.equal(fieldHelpText(engineFieldHelp(engines as never, "other"), "supports", "NODE"), undefined, "blank");
  assert.equal(fieldHelpText(engineFieldHelp(engines as never, "other"), "openings", "DEPTH"), undefined);
  assert.deepEqual(engineFieldHelp(engines as never, "bare"), {}, "an older server's engine has none");
  assert.deepEqual(engineFieldHelp(engines as never, "unknown"), {});
});

test("fieldHelp reads the selected engine's, and is on @/viewer-core/app", () => {
  assert.match(readFileSync(new URL("../../viewer-core/app.ts", import.meta.url), "utf8"), /export { engineFieldHelp, fieldHelp, fieldHelpText, useFieldHelp }/);
  useCellBuilderStore.setState({ engines, selectedEngine: "adapy-default" } as never);
  try {
    assert.equal(fieldHelp("spaces", "NAME"), "Name of space");
    useCellBuilderStore.setState({ selectedEngine: "other" } as never);
    assert.equal(fieldHelp("spaces", "NAME"), "The space's name.\nUnique.");
  } finally {
    useCellBuilderStore.setState({ engines: [], selectedEngine: "adapy-default" } as never);
  }
});
