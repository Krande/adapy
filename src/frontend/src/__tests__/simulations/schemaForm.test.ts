// The start form generated from a simulation provider's JSON Schema, as data: which fields a
// schema gives, what the form starts as, and how what is typed becomes the value sent.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ParameterSchema } from "@/services/api/simulations";
import {
  defaultParameters,
  fieldInputValue,
  missingRequired,
  parseFieldInput,
  schemaFields,
  withParameter,
} from "@/services/simulations/schemaForm";

const SCHEMA: ParameterSchema = {
  type: "object",
  properties: {
    duration: { type: "number", minimum: 0, default: 2.5, title: "Duration (s)", description: "Simulated time" },
    steps: { type: "integer", minimum: 1, maximum: 1000 },
    name: { type: "string" },
    realtime: { type: "boolean", default: false },
    solver: { enum: ["direct", "iterative"], default: "direct" },
    levels: { enum: [1, 2, 3] },
    gravity: { type: "array", items: { type: "number" }, default: [0, 0, -9.81] },
    extra: {},
    tolerance: { type: ["number", "null"] },
  },
  required: ["steps", "name"],
};

const field = (name: string) => schemaFields(SCHEMA).find((f) => f.name === name)!;

test("each property becomes a field of the plain kind its type gives", () => {
  assert.deepEqual(
    schemaFields(SCHEMA).map((f) => [f.name, f.kind]),
    [
      ["duration", "number"],
      ["steps", "integer"],
      ["name", "string"],
      ["realtime", "boolean"],
      ["solver", "enum"],
      ["levels", "enum"],
      ["gravity", "json"],
      ["extra", "json"],
      ["tolerance", "number"],
    ],
  );
  const duration = field("duration");
  assert.equal(duration.label, "Duration (s)");
  assert.equal(duration.description, "Simulated time");
  assert.equal(duration.minimum, 0);
  assert.equal(field("steps").label, "steps", "no title: the property name");
  assert.deepEqual([field("steps").required, field("duration").required], [true, false]);
  assert.deepEqual(field("solver").options, ["direct", "iterative"]);
});

test("a schema without properties gives no fields", () => {
  assert.deepEqual(schemaFields({}), []);
  assert.deepEqual(schemaFields(null), []);
  assert.deepEqual(defaultParameters(undefined), {});
});

test("the form starts as the schema's defaults", () => {
  assert.deepEqual(defaultParameters(SCHEMA), {
    duration: 2.5,
    realtime: false,
    solver: "direct",
    gravity: [0, 0, -9.81],
  });
});

test("what is typed becomes a value of the field's type", () => {
  assert.equal(parseFieldInput(field("duration"), "0.25"), 0.25);
  assert.equal(parseFieldInput(field("steps"), "12"), 12);
  assert.equal(parseFieldInput(field("name"), " arm "), " arm ");
  assert.equal(parseFieldInput(field("realtime"), true), true);
  assert.equal(parseFieldInput(field("realtime"), false), false);
  // An enum's input holds the option's index, so a numeric option stays a number.
  assert.equal(parseFieldInput(field("solver"), "1"), "iterative");
  assert.equal(parseFieldInput(field("levels"), "2"), 3);
  assert.deepEqual(parseFieldInput(field("gravity"), "[0, 0, -1.62]"), [0, 0, -1.62]);
  assert.deepEqual(parseFieldInput(field("extra"), '{"a": 1}'), { a: 1 });
});

test("an emptied input means the parameter is not sent", () => {
  for (const name of ["duration", "steps", "name", "gravity"]) {
    assert.equal(parseFieldInput(field(name), "  "), undefined, name);
  }
  assert.equal(parseFieldInput(field("solver"), "-1"), undefined, "the (not set) option");
  assert.deepEqual(withParameter({ a: 1, b: 2 }, "a", undefined), { b: 2 });
  assert.deepEqual(withParameter({ a: 1 }, "b", 0), { a: 1, b: 0 });
});

test("text that is not a number or not JSON is kept for the server to refuse by name", () => {
  assert.equal(parseFieldInput(field("steps"), "many"), "many");
  assert.equal(parseFieldInput(field("gravity"), "[0, 0"), "[0, 0");
});

test("an input shows the value it would parse back to", () => {
  for (const [name, value] of [
    ["duration", 0.25],
    ["steps", 12],
    ["name", "arm"],
    ["solver", "iterative"],
    ["levels", 3],
    ["gravity", [0, 0, -1.62]],
  ] as const) {
    const f = field(name);
    assert.deepEqual(parseFieldInput(f, fieldInputValue(f, value)), value, name);
  }
  assert.equal(fieldInputValue(field("steps"), undefined), "");
  assert.equal(fieldInputValue(field("solver"), undefined), "-1");
});

test("required fields without a value are named by their label", () => {
  assert.deepEqual(missingRequired(SCHEMA, {}), ["steps", "name"]);
  assert.deepEqual(missingRequired(SCHEMA, { steps: 3, name: "" }), ["name"]);
  assert.deepEqual(missingRequired(SCHEMA, { steps: 0, name: "x" }), [], "zero is a value");
});
