/**
 * The Scene tree's click: Ctrl (Windows, Linux) adds a row to the selection or takes it out, as Cmd
 * does on a Mac. react-arborist reads Cmd alone, so a Ctrl-click replaced the selection.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { clickRow } from "../../components/tree_view/SceneTreeRow";

function fakeNode(selected: boolean, disableMultiSelection = false) {
  const calls: string[] = [];
  const node = {
    isSelected: selected,
    tree: { props: { disableMultiSelection } },
    select: () => calls.push("select"),
    selectMulti: () => calls.push("selectMulti"),
    deselect: () => calls.push("deselect"),
    handleClick: () => calls.push("handleClick"),
  };
  return { node: node as unknown as Parameters<typeof clickRow>[0], calls };
}

const click = (mods: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }) =>
  ({ ctrlKey: false, metaKey: false, shiftKey: false, ...mods }) as unknown as Parameters<typeof clickRow>[1];

test("Ctrl-click adds an unselected row to the selection", () => {
  const { node, calls } = fakeNode(false);
  clickRow(node, click({ ctrlKey: true }));
  assert.deepEqual(calls, ["selectMulti"]);
});

test("Ctrl-click on a selected row takes it out", () => {
  const { node, calls } = fakeNode(true);
  clickRow(node, click({ ctrlKey: true }));
  assert.deepEqual(calls, ["deselect"]);
});

test("Cmd behaves the same, and a plain or Shift click is the library's own", () => {
  const cmd = fakeNode(false);
  clickRow(cmd.node, click({ metaKey: true }));
  assert.deepEqual(cmd.calls, ["selectMulti"]);
  for (const mods of [{}, { shiftKey: true }]) {
    const plain = fakeNode(false);
    clickRow(plain.node, click(mods));
    assert.deepEqual(plain.calls, ["handleClick"]);
  }
});

test("with multi-select off, Ctrl-click is an ordinary click", () => {
  const { node, calls } = fakeNode(false, true);
  clickRow(node, click({ ctrlKey: true }));
  assert.deepEqual(calls, ["handleClick"]);
});
