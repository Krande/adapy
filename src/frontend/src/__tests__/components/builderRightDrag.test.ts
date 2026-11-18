// A right DRAG in the cell builder moves the camera: it opens no cell menu when it ends over a cell. Only a
// right-click that stayed put does (as the scene's own menu, setupPointerHandler).

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { onContextMenu, onPointerDown } = await import("@/components/viewer/cellbuilder/pointerTools");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

test("a right-click that moved past the drag threshold opens no menu, and no browser menu either", () => {
  const before = useCellBuilderStore.getState().active;
  useCellBuilderStore.setState({ active: { modelId: "m", name: "n", revision: 1 }, contextMenu: null } as never);
  try {
    const ctx = { rightPress: null } as unknown as Parameters<typeof onContextMenu>[0];
    onPointerDown(ctx, { button: 2, clientX: 100, clientY: 100 } as PointerEvent);
    assert.deepEqual((ctx as unknown as { rightPress: unknown }).rightPress, { x: 100, y: 100 });
    let prevented = false;
    onContextMenu(ctx, { clientX: 160, clientY: 130, preventDefault: () => void (prevented = true) } as unknown as MouseEvent);
    assert.ok(prevented, "the browser's menu is kept away too");
    assert.equal(useCellBuilderStore.getState().contextMenu, null, "no cell menu");
    assert.equal((ctx as unknown as { rightPress: unknown }).rightPress, null, "the press is spent");
  } finally {
    useCellBuilderStore.setState({ active: before, contextMenu: null } as never);
  }
});
