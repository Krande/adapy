// The G / R / S gizmo commands: which builder components each takes, and the reason the
// resize gizmo gives for a component it refuses (a loft band, a CAD-fitted equipment).

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const C = await import("@/layout/commands");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { capabilities } = await import("@/services/capabilities");
const { CAD_RESIZE_REFUSAL, LOFT_RESIZE_REFUSAL } = await import("@/utils/cellbuilder/cellResize");

const reason = (id: string) => C.getCommand(id)!.disabledReason?.() ?? null;

const box = (kind: string, extra: object = {}) => ({
  id: kind,
  name: kind,
  kind,
  origin: [0, 0, 0],
  size: [1, 1, 1],
  ...extra,
});

function open(kind: string, extra: object = {}, state: object = {}): void {
  useCellBuilderStore.setState({
    active: { modelId: "m", name: "m", revision: 1 },
    cells: { [kind]: box(kind, extra) as never },
    selection: { kind: "cell", cellId: kind } as never,
    equipmentCad: false,
    equipmentTypes: [],
    ...state,
  });
}

test("the gizmos per component kind, and why resize refuses a loft band or a CAD-fitted box", () => {
  // An editing connection (the desktop websocket one in a plain Node test cannot edit).
  const proc = capabilities.procedural;
  const had = Object.getOwnPropertyDescriptor(proc, "canEdit");
  Object.defineProperty(proc, "canEdit", { value: true, configurable: true });
  C.resetCommands();
  registerCoreCommands();
  try {
    for (const kind of ["cell", "opening", "equipment"]) {
      open(kind);
      assert.equal(reason(ID.builderResize), null, `${kind} resizes`);
      assert.equal(reason(ID.builderTranslate), null, `${kind} moves`);
    }
    open("equipment");
    assert.equal(reason(ID.builderRotate), null, "equipment rotates");
    open("opening");
    assert.match(reason(ID.builderRotate) ?? "", /equipment/, "an opening does not rotate");
    // A rotated equipment still resizes (its handles follow its rotation).
    open("equipment", { rotation: [0, 0, 30] });
    assert.equal(reason(ID.builderResize), null);

    open("loft", { loft: { member: "L1", bay: 0 } });
    assert.equal(reason(ID.builderResize), LOFT_RESIZE_REFUSAL);
    assert.equal(reason(ID.builderTranslate), null, "a loft band moves (its whole member)");

    const types = [{ slug: "pump", name: "Pump", origin: "catalog", has_cad: true }];
    open("equipment", { equipmentType: "pump" }, { equipmentCad: true, equipmentTypes: types });
    assert.equal(reason(ID.builderResize), CAD_RESIZE_REFUSAL);
    open("equipment", { equipmentType: "pump" }, { equipmentCad: false, equipmentTypes: types });
    assert.equal(reason(ID.builderResize), null, "with CAD models off the declared box resizes");

    useCellBuilderStore.setState({ selection: null });
    assert.match(reason(ID.builderResize) ?? "", /to resize/);
  } finally {
    useCellBuilderStore.getState().close();
    C.resetCommands();
    if (had) Object.defineProperty(proc, "canEdit", had);
    else delete (proc as { canEdit?: boolean }).canEdit;
  }
});
