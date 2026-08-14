import assert from "node:assert/strict";
import { test } from "node:test";

import {
  classifyEquipment,
  classifyMedium,
  inputPortNames,
  missingInputs,
} from "../../utils/viewer/typeIconClassify";

const pumpPorts = [
  { name: "suction", direction: "IN" as const, category: "process" as const },
  {
    name: "discharge",
    direction: "OUT" as const,
    category: "process" as const,
  },
  { name: "power", direction: "IN" as const, category: "electrical" as const },
];
const boardPorts = [
  {
    name: "incoming",
    direction: "IN" as const,
    category: "electrical" as const,
  },
  {
    name: "feeder",
    direction: "OUT" as const,
    category: "electrical" as const,
  },
];

test("classifyEquipment: slug + electrical-out heuristics", () => {
  assert.equal(classifyEquipment("pump", pumpPorts), "pump");
  assert.equal(classifyEquipment("tank", []), "tank");
  assert.equal(classifyEquipment("switchboard", boardPorts), "electrical");
  // a custom slug with an OUT electrical port still reads as a producer
  assert.equal(classifyEquipment("mystery", boardPorts), "electrical");
  assert.equal(classifyEquipment("widget", []), "other");
});

test("classifyMedium: kind wins for electrical/duct, else medium keyword", () => {
  assert.equal(classifyMedium("electrical", null), "electrical");
  assert.equal(classifyMedium("cable", "water"), "electrical");
  assert.equal(classifyMedium("duct", null), "duct");
  assert.equal(classifyMedium("piping", "Cooling Water"), "water");
  assert.equal(classifyMedium("piping", "diesel oil"), "oil");
  assert.equal(classifyMedium("piping", null), "generic");
});

test("inputPortNames / missingInputs", () => {
  assert.deepEqual(inputPortNames(pumpPorts), ["suction", "power"]);
  // only "power" is connected -> "suction" is a missing input
  const connected = new Set(["Pump1::power"]);
  assert.deepEqual(missingInputs("Pump1", pumpPorts, connected), ["suction"]);
  // all inputs connected -> none missing
  const all = new Set(["Pump1::power", "Pump1::suction"]);
  assert.deepEqual(missingInputs("Pump1", pumpPorts, all), []);
  // unknown ports -> nothing to miss
  assert.deepEqual(missingInputs("X", undefined, new Set()), []);
});
