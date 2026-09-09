import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PluginJobOption,
  buildOptionValues,
  choicesFor,
  declaredOptions,
  optionApplies,
  pickUndeclaredOptions,
  seedOptionValues,
  visibleOptions,
} from "@/components/admin/pluginOptionFields";

// A plugin's job options used to be a JSON textarea, because core passes them
// through verbatim and only the plugin knows what they are. The plugin now
// declares them and the panel renders controls. These are the rules that turn a
// declaration into form state and back into the document the API is sent.

const ACTION: PluginJobOption = {
  name: "action",
  type: "enum",
  title: "Action",
  enum: ["export", "scan", "status"],
  default: "export",
  required: true,
};

const DECLS: readonly PluginJobOption[] = [
  ACTION,
  { name: "target", type: "string", title: "Target", depends_on: "action", applies_to: ["export"] },
  { name: "depth", type: "int", title: "Depth", default: 2, depends_on: "action", applies_to: ["scan"] },
  { name: "ratio", type: "float", title: "Ratio" },
  { name: "deep", type: "bool", title: "Deep", default: false },
  { name: "skip_users", type: "string_list", title: "Skip users" },
];

test("an option with no dependency always applies", () => {
  assert.ok(optionApplies(ACTION, {}));
  assert.ok(optionApplies({ name: "x", type: "string" }, {}));
});

test("a dependent option applies only to the values it names", () => {
  assert.ok(optionApplies(DECLS[1], { action: "export" }));
  assert.ok(!optionApplies(DECLS[1], { action: "scan" }));
});

test("a dependency on an unset option leaves it out", () => {
  // The alternative is showing every branch of every action at once, which is
  // the JSON box with extra steps.
  assert.ok(!optionApplies(DECLS[1], {}));
});

test("the visible set follows the chosen action", () => {
  const forExport = visibleOptions(DECLS, { action: "export" }).map((d) => d.name);
  assert.deepEqual(forExport, ["action", "target", "ratio", "deep", "skip_users"]);
  const forScan = visibleOptions(DECLS, { action: "scan" }).map((d) => d.name);
  assert.deepEqual(forScan, ["action", "depth", "ratio", "deep", "skip_users"]);
});

test("a dependent enum narrows its choices, and falls back to the flat list", () => {
  const decl: PluginJobOption = {
    name: "mode",
    type: "enum",
    enum: ["a", "b", "c"],
    depends_on: "action",
    enum_by: { export: ["a", "b"] },
  };
  assert.deepEqual(choicesFor(decl, { action: "export" }), ["a", "b"]);
  // A value with no entry gets the whole enum rather than an empty dropdown,
  // which would be a control the admin cannot use and cannot explain.
  assert.deepEqual(choicesFor(decl, { action: "scan" }), ["a", "b", "c"]);
  assert.deepEqual(choicesFor(decl, {}), ["a", "b", "c"]);
});

test("seeding uses the declared defaults, and an absent default is empty", () => {
  const v = seedOptionValues(DECLS);
  assert.equal(v.action, "export");
  assert.equal(v.depth, "2", "numbers are edited as text");
  assert.equal(v.deep, false);
  assert.equal(v.ratio, "", "no default means an empty field, not a guess");
});

test("seeding from an existing document wins over the defaults", () => {
  const v = seedOptionValues(DECLS, { action: "scan", depth: 7, deep: true });
  assert.equal(v.action, "scan");
  assert.equal(v.depth, "7");
  assert.equal(v.deep, true);
});

test("a stored list arrives as an array and is edited as text", () => {
  const v = seedOptionValues(DECLS, { skip_users: ["a", "b"] });
  assert.deepEqual(v.skip_users, ["a", "b"]);
  // A string where a list was expected still loads: a hand-written schedule is
  // allowed to be sloppy, and refusing to show it would make it uneditable.
  assert.deepEqual(seedOptionValues(DECLS, { skip_users: "a, b" }).skip_users, ["a", "b"]);
});

test("an explicit null in the document is not rendered as the string null", () => {
  const v = seedOptionValues(DECLS, { ratio: null });
  assert.equal(v.ratio, "");
});

test("options nothing declares are kept, with their values", () => {
  // A plugin accepts keys it has not declared, and the panel's plugin list only
  // covers workers online right now. Dropping them would turn "I changed the
  // cron" into "I also removed two options".
  const extras = pickUndeclaredOptions(DECLS, { action: "scan", future_flag: true, note: "x" });
  assert.deepEqual(extras, { future_flag: true, note: "x" });
});

test("building sends the defaults rather than leaving them implicit", () => {
  // For a schedule the stored document is the record of what it runs. An implicit
  // default means the schedule's behaviour changes when the plugin's default
  // does, with no edit to point at.
  const built = buildOptionValues(DECLS, seedOptionValues(DECLS));
  assert.ok(!("error" in built));
  assert.equal(built.options.action, "export");
  assert.equal(built.options.deep, false, "a false checkbox is a value, not an omission");
});

test("building omits an empty optional field so the plugin's default applies", () => {
  const built = buildOptionValues(DECLS, { action: "export", ratio: "  ", target: "" });
  assert.ok(!("error" in built));
  assert.ok(!("ratio" in built.options));
  assert.ok(!("target" in built.options));
});

test("building coerces numbers, and refuses text that is not one", () => {
  const ok = buildOptionValues(DECLS, { action: "scan", depth: "4", ratio: "0.25" });
  assert.ok(!("error" in ok));
  assert.equal(ok.options.depth, 4);
  assert.equal(ok.options.ratio, 0.25);

  const bad = buildOptionValues(DECLS, { action: "scan", depth: "soon" });
  assert.ok("error" in bad);
  assert.match(bad.error, /Depth must be a number/);

  // An int field holding 2.5 would be silently truncated by the backend, or
  // rejected — either way the admin should hear it here.
  const fractional = buildOptionValues(DECLS, { action: "scan", depth: "2.5" });
  assert.ok("error" in fractional);
  assert.match(fractional.error, /whole number/);
});

test("a list splits on commas and drops the gaps", () => {
  const built = buildOptionValues(DECLS, { action: "export", skip_users: "a, b ,, c " });
  assert.ok(!("error" in built));
  assert.deepEqual(built.options.skip_users, ["a", "b", "c"]);
});

test("a required field left empty is refused by name", () => {
  const built = buildOptionValues(DECLS, { action: "" });
  assert.ok("error" in built);
  assert.match(built.error, /Action is required/);
});

test("an option hidden by the chosen action is not sent", () => {
  // Otherwise switching from scan to export would leave `depth` in the document,
  // where the plugin either ignores it or refuses it — and the form shows no
  // field that explains where it came from.
  const built = buildOptionValues(DECLS, { action: "export", depth: "9", target: "t" });
  assert.ok(!("error" in built));
  assert.ok(!("depth" in built.options));
  assert.equal(built.options.target, "t");
});

test("kept options ride along, and a declared field wins over one", () => {
  const built = buildOptionValues(DECLS, { action: "export" }, { future_flag: true, action: "stale" });
  assert.ok(!("error" in built));
  assert.equal(built.options.future_flag, true);
  assert.equal(built.options.action, "export", "a kept key shadowed the field the admin edited");
});

test("a spec with no declaration yields no fields rather than throwing", () => {
  // A spec is advertised BY A WORKER, so the declaration can be absent on an
  // older build. That has to degrade to the raw document, not break the panel.
  assert.deepEqual(declaredOptions(undefined), []);
  assert.deepEqual(declaredOptions(null), []);
  assert.deepEqual(declaredOptions({}), []);
  assert.deepEqual(declaredOptions({ job_options: "nonsense" }), []);
});

test("unusable declarations are dropped, not rendered", () => {
  const decls = declaredOptions({
    job_options: [
      { name: "good", type: "string" },
      { name: "", type: "string" },
      { name: "no_type" },
      { name: "odd", type: "colour" },
      { name: "empty_enum", type: "enum", enum: [] },
      { name: "enum_ok", type: "enum", enum: ["x"] },
      "not an object",
      null,
    ],
  });
  // An enum with no choices would render as an empty dropdown; as free text it
  // would accept values the plugin rejects. Neither is better than absent.
  assert.deepEqual(
    decls.map((d) => d.name),
    ["good", "enum_ok"],
  );
});

test("an enum can take its choices from a spec key, so a fleet-wide list works", () => {
  // A declaration cannot be union-merged across workers — merging two copies
  // would duplicate every option — so an enum covering the whole fleet has to
  // name the key that IS merged rather than listing choices inline, which would
  // only ever show what one worker serves.
  const decls = declaredOptions({
    projects: ["ALPHA", "BETA"],
    job_options: [{ name: "project", type: "enum", enum_from: "projects", enum: ["ONLY_ME"] }],
  });
  assert.deepEqual(decls[0].enum, ["ALPHA", "BETA"], "the inline enum was not replaced");
});

test("an enum_from naming a key the spec has not got is dropped", () => {
  // Rendering it as an empty dropdown would be a control nobody can use, and
  // falling back to free text would accept values the plugin rejects.
  assert.deepEqual(declaredOptions({ job_options: [{ name: "p", type: "enum", enum_from: "nope" }] }), []);
  assert.deepEqual(
    declaredOptions({ projects: [], job_options: [{ name: "p", type: "enum", enum_from: "projects" }] }),
    [],
  );
  assert.deepEqual(
    declaredOptions({ projects: [1, 2], job_options: [{ name: "p", type: "enum", enum_from: "projects" }] }),
    [],
  );
});
