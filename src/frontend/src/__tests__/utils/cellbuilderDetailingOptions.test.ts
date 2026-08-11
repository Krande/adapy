import assert from "node:assert/strict";
import { test } from "node:test";

import type { DetailingEngineSummary } from "../../services/viewerApi";
import {
  clampField,
  fieldDefault,
  resolveDetailingOptions,
  toDetailingOptionsPayload,
  type DetailingOptions,
} from "../../utils/cellbuilder/detailingOptions";

// A stand-in engine advertising two joint types with mixed field kinds — the
// panel/store are driven ENTIRELY off this, never off hardcoded slugs.
const engine: DetailingEngineSummary = {
  slug: "adapy-default",
  name: "adapy detailing",
  description: "",
  inprocess: true,
  origin: "code",
  joint_types: [
    {
      slug: "girder_gusset",
      name: "Girder–girder gusset",
      default_enabled: true,
      fields: [
        { name: "weld_leg", type: "number", default: 6, min: 3, max: 20, unit: "mm" },
        { name: "gusset_t", type: "number", default: 10, min: 5, max: 40, unit: "mm" },
      ],
    },
    {
      slug: "box_to_box",
      name: "Box-to-box clash cut",
      default_enabled: false,
      fields: [
        { name: "clearance", type: "number", default: 2, min: 0, max: 20, unit: "mm" },
        { name: "bolt", type: "enum", default: "M20", options: ["M16", "M20", "M24"] },
      ],
    },
  ],
};

test("reconcile builds state for EXACTLY the advertised joint types", () => {
  const opts = resolveDetailingOptions(engine, {});
  // Keys mirror the advertised slugs — no hardcoded joint families.
  assert.deepEqual(Object.keys(opts).sort(), ["box_to_box", "girder_gusset"]);
  // default_enabled is honoured per joint type.
  assert.equal(opts.girder_gusset.enabled, true);
  assert.equal(opts.box_to_box.enabled, false);
  // Fields seed from their advertised defaults.
  assert.equal(opts.girder_gusset.fields.weld_leg, 6);
  assert.equal(opts.box_to_box.fields.bolt, "M20");
});

test("reconcile keeps valid prior edits and defaults invalid/missing ones", () => {
  const prev: DetailingOptions = {
    girder_gusset: {
      enabled: false, // user turned it off
      fields: { weld_leg: 12, gusset_t: "oops" as unknown as number }, // one valid, one wrong-typed
    },
  };
  const opts = resolveDetailingOptions(engine, prev);
  assert.equal(opts.girder_gusset.enabled, false); // preserved
  assert.equal(opts.girder_gusset.fields.weld_leg, 12); // preserved
  assert.equal(opts.girder_gusset.fields.gusset_t, 10); // reset to default
});

test("reconcile drops joints the (new) engine no longer advertises", () => {
  const prev: DetailingOptions = {
    girder_gusset: { enabled: true, fields: { weld_leg: 6, gusset_t: 10 } },
    legacy_joint: { enabled: true, fields: { foo: 1 } },
  };
  const opts = resolveDetailingOptions(engine, prev);
  assert.ok(!("legacy_joint" in opts));
});

test("no engine / no joint types yields an empty map (e.g. 'none')", () => {
  assert.deepEqual(resolveDetailingOptions(undefined, {}), {});
  assert.deepEqual(
    resolveDetailingOptions(
      { ...engine, joint_types: [] },
      { girder_gusset: { enabled: true, fields: {} } },
    ),
    {},
  );
});

test("payload flattens to {slug: {enabled, ...fields}} and clamps numbers", () => {
  const opts = resolveDetailingOptions(engine, {});
  // Push a field out of its advertised range.
  opts.girder_gusset.fields.weld_leg = 999;
  const payload = toDetailingOptionsPayload(engine, opts);
  assert.ok(payload);
  assert.equal(payload!.girder_gusset.enabled, true);
  assert.equal(payload!.girder_gusset.weld_leg, 20); // clamped to max
  assert.equal(payload!.box_to_box.bolt, "M20");
});

test("payload is null when nothing is advertised (keeps the plain cache key)", () => {
  assert.equal(toDetailingOptionsPayload(undefined, {}), null);
  assert.equal(toDetailingOptionsPayload({ ...engine, joint_types: [] }, {}), null);
});

test("an option change yields a DISTINCT compile cache key", () => {
  // The server folds a hash of the serialized `detailing_options` into the GLB
  // key, so a knob change must serialize to a DIFFERENT payload (hence a distinct
  // key). Same options must serialize identically (stable key).
  const base = resolveDetailingOptions(engine, {});
  const changed = resolveDetailingOptions(engine, {});
  changed.girder_gusset.fields.weld_leg = 8;

  const keyOf = (o: DetailingOptions) =>
    JSON.stringify(toDetailingOptionsPayload(engine, o));

  assert.notEqual(keyOf(base), keyOf(changed));
  assert.equal(keyOf(base), keyOf(resolveDetailingOptions(engine, {})));
});

test("fieldDefault + clampField coerce defensively", () => {
  assert.equal(fieldDefault({ name: "b", type: "bool", default: 1 as unknown as boolean }), true);
  assert.equal(fieldDefault({ name: "n", type: "number", default: "x" as unknown as number }), 0);
  assert.equal(fieldDefault({ name: "e", type: "enum", default: "A" }), "A");
  assert.equal(clampField({ name: "n", type: "number", default: 0, min: 1, max: 9 }, 20), 9);
  assert.equal(clampField({ name: "n", type: "number", default: 0, min: 1, max: 9 }, -5), 1);
});
