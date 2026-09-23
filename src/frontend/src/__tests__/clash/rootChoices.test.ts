// `ClashOptions.root` is the NAME OF A PART, and a name the source does not carry is refused by
// the check. The picker therefore offers the loaded hierarchy's own container nodes -- these
// tests pin what counts as one.

import assert from "node:assert/strict";
import { test } from "node:test";

const { rootChoices } = await import("@/components/info_box_scene/ClashRootPicker");

type Node = { id: string; name: string; children: Node[] };
const node = (id: string, name: string, children: Node[] = []): Node => ({ id, name, children });

const tree = node("0", "model", [
  node("1", "deck", [node("2", "bm1"), node("3", "bm2")]),
  node("4", "roof", [node("5", "frame", [node("6", "bm3")])]),
  node("7", "loose-beam"),
]);

test("offers containers, not leaves", () => {
  const names = rootChoices(tree as never).map((c) => c.name);
  assert.deepEqual(names, ["model", "deck", "roof", "frame"]);
});

test("depth is carried for indenting, root first", () => {
  const byName = new Map(rootChoices(tree as never).map((c) => [c.name, c.depth]));
  assert.equal(byName.get("model"), 0);
  assert.equal(byName.get("deck"), 1);
  assert.equal(byName.get("frame"), 2);
});

test("a repeated name is offered once -- the check matches on the name, so twice is the same answer", () => {
  const dup = node("0", "model", [node("1", "deck", [node("2", "bm")]), node("3", "deck", [node("4", "bm")])]);
  assert.deepEqual(
    rootChoices(dup as never).map((c) => c.name),
    ["model", "deck"],
  );
});

test("no hierarchy loaded is an empty list, never a throw", () => {
  assert.deepEqual(rootChoices(null), []);
});

test("a pathological hierarchy is capped rather than rendering thousands of rows", () => {
  const wide = node(
    "0",
    "model",
    Array.from({ length: 50 }, (_, i) => node(`c${i}`, `part${i}`, [node(`l${i}`, `bm${i}`)])),
  );
  assert.equal(rootChoices(wide as never, 10).length, 10);
});
