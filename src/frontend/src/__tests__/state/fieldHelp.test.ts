// The builder's field help: read from the engine catalog by doc list and column, for the engine
// the builder compiles with now (the server has put adapy's text under the engine's own).

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { engineFieldHelp, fieldHelp, fieldHelpText, fieldLabel, fieldLabelText } = await import("@/state/cellbuilder/fieldHelp");
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
  assert.match(
    readFileSync(new URL("../../viewer-core/app.ts", import.meta.url), "utf8"),
    /export { engineFieldHelp, fieldHelp, fieldHelpText, fieldLabel, useFieldHelp, useFieldLabel }/,
  );
  useCellBuilderStore.setState({ engines, selectedEngine: "adapy-default" } as never);
  try {
    assert.equal(fieldHelp("spaces", "NAME"), "Name of space");
    useCellBuilderStore.setState({ selectedEngine: "other" } as never);
    assert.equal(fieldHelp("spaces", "NAME"), "The space's name.\nUnique.");
  } finally {
    useCellBuilderStore.setState({ engines: [], selectedEngine: "adapy-default" } as never);
  }
});

// ── UI labels: a sibling "<list>.labels" entry in the same FieldHelp shape ─────────────────

const labelEngines = [
  {
    slug: "adapy-default",
    field_help: {
      setups: { GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN: "The minimum spacing between primary girders." },
      "setups.labels": { GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN: "Primary girder spacing, min" },
      spaces: { NAME: "Name of space" }, // no "spaces.labels" at all
    },
  },
];

test("fieldLabelText reads the '<list>.labels' sibling; blank, missing, or a list with none is undefined", () => {
  const help = engineFieldHelp(labelEngines as never, "adapy-default");
  assert.equal(fieldLabelText(help, "setups", "GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN"), "Primary girder spacing, min");
  assert.equal(fieldLabelText(help, "setups", "NO_SUCH_COLUMN"), undefined);
  assert.equal(fieldLabelText(help, "spaces", "NAME"), undefined, "spaces has help text but no .labels list");
  assert.equal(fieldLabelText({ "x.labels": { A: "  " } }, "x", "A"), undefined, "blank label");
});

test("fieldLabel reads the selected engine's labels, same as fieldHelp does for help text", () => {
  useCellBuilderStore.setState({ engines: labelEngines, selectedEngine: "adapy-default" } as never);
  try {
    assert.equal(fieldLabel("setups", "GRID_CONFIG_GIRDER_PRIMARY_SPACING_MIN"), "Primary girder spacing, min");
    assert.equal(fieldLabel("spaces", "NAME"), undefined);
  } finally {
    useCellBuilderStore.setState({ engines: [], selectedEngine: "adapy-default" } as never);
  }
});
