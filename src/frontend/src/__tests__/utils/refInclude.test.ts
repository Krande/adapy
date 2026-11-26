// Whether a plugin's ref (a load/modification/support box) is taken by the build -- the ref
// analogue of include.test's itemIncludeState coverage, for `refIncludeState`
// (utils/cellbuilder/refInclude.ts).

import assert from "node:assert/strict";
import { test } from "node:test";

import { refIncludeState } from "@/utils/cellbuilder/refInclude";
import type { CellGroup } from "@/utils/cellbuilder/groups";

const GROUPS: CellGroup[] = [
  { name: "A", blueprint: "steel_stru" }, // included (no `include` key)
  { name: "B", blueprint: "steel_stru", include: false }, // excluded
];

test("a ref whose own include is false is excluded, whatever its structure", () => {
  assert.equal(refIncludeState({ include: false, structure: "A" }, GROUPS), "excluded");
  assert.equal(refIncludeState({ include: false, structure: "B" }, GROUPS), "excluded");
  assert.equal(refIncludeState({ include: false }, GROUPS), "excluded");
});

test('"*" and no structure count as included, regardless of any group', () => {
  assert.equal(refIncludeState({ include: true, structure: "*" }, GROUPS), "included");
  assert.equal(refIncludeState({ include: true }, GROUPS), "included");
  // Even with every group excluded, a ref naming none of them in particular is still included.
  const allExcluded: CellGroup[] = [{ name: "A", blueprint: "steel_stru", include: false }];
  assert.equal(refIncludeState({ include: true, structure: "*" }, allExcluded), "included");
});

test("a ref naming an included structure is included", () => {
  assert.equal(refIncludeState({ include: true, structure: "A" }, GROUPS), "included");
});

test("a ref naming an excluded structure is excluded-by-structure", () => {
  assert.equal(refIncludeState({ include: true, structure: "B" }, GROUPS), "excluded-by-structure");
});

test("a ref naming a structure the groups list does not have is included (no group to exclude it)", () => {
  assert.equal(refIncludeState({ include: true, structure: "Nonexistent" }, GROUPS), "included");
});

test("the ref's own exclusion wins over its structure's", () => {
  // Own include false + an included structure -> still excluded, not excluded-by-structure.
  assert.equal(refIncludeState({ include: false, structure: "A" }, GROUPS), "excluded");
});
