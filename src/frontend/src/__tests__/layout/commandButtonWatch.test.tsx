// A toolbar button over a toggle redraws when the toggle changes from elsewhere (a menu, a key):
// the command's `watch` subscribes it to the store its tick reads.
//
// Mounted for real with react-dom/client over a minimal DOM (fixtures/miniDom).

import assert from "node:assert/strict";
import { test } from "node:test";

const dom = await import("../fixtures/miniDom");
await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");
const C = await import("@/layout/commands");
const { CommandButton } = await import("@/layout/CommandButton");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { useGroundGridStore } = await import("@/state/groundGridStore");
const { useProjectionStore } = await import("@/state/projectionStore");
const { useSectionStore } = await import("@/state/sectionStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

const flush = () =>
  act(async () => {
    for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
  });

const button = (id: string) => dom.miniDocument.querySelectorAll(`[data-command="${id}"]`)[0];

test("a toggle's button follows its store when the toggle is flipped outside it", async () => {
  C.resetCommands();
  registerCoreCommands();
  const release = setActiveViewport({ kind: "three", unavailable: () => null, modelBounds: () => null } as never);
  useGroundGridStore.getState().setVisible(true);
  const ortho = useProjectionStore.getState().orthographic;
  const root = createRoot(dom.container() as unknown as Element);
  try {
    await act(async () =>
      root.render(
        React.createElement(
          React.Fragment,
          null,
          React.createElement(CommandButton, { id: ID.groundGrid }),
          React.createElement(CommandButton, { id: ID.orthographic }),
          React.createElement(CommandButton, { id: ID.sectionFlip }),
        ),
      ),
    );
    await flush();
    assert.equal(button(ID.groundGrid)?.getAttribute("aria-pressed"), "true");
    await act(async () => useGroundGridStore.getState().setVisible(false));
    await flush();
    assert.equal(button(ID.groundGrid)?.getAttribute("aria-pressed"), "false", "redrawn from the store");

    await act(async () => useProjectionStore.getState().toggleOrthographic());
    await flush();
    assert.equal(button(ID.orthographic)?.getAttribute("aria-pressed"), String(!ortho));

    assert.ok(button(ID.sectionFlip)?.getAttribute("disabled") !== null, "no plane yet: greyed");
    await act(async () => useSectionStore.setState({ planes: [{ id: "p", label: "Z", normal: [0, 0, 1], constant: 0, enabled: true }], activeId: "p" }));
    await flush();
    assert.equal(button(ID.sectionFlip)?.getAttribute("disabled"), null, "a plane added elsewhere enables it");
  } finally {
    await act(async () => root.unmount());
    useSectionStore.setState({ planes: [], activeId: null });
    useGroundGridStore.getState().setVisible(true);
    if (useProjectionStore.getState().orthographic !== ortho) useProjectionStore.getState().toggleOrthographic();
    release();
    C.resetCommands();
  }
});
