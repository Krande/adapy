import assert from "node:assert/strict";
import { test } from "node:test";

import { eitherClaimed } from "@/components/conversion/claimedLoads";

test("a host's claims add to core's instead of replacing them", () => {
  const core = (name: string) => name === "a.ifc";
  const host = (name: string) => name === "b.ifc";
  const both = eitherClaimed(core, host);
  assert.equal(both("a.ifc"), true, "still claimed by core's rows");
  assert.equal(both("b.ifc"), true);
  assert.equal(both("c.ifc"), false);
  assert.equal(eitherClaimed(core), core, "no host predicate: core's alone");
});
