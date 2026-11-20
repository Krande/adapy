// The shortcuts dialog lists every key the cell builder takes. The builder's key handler
// (components/viewer/cellbuilder/keyboard.ts) is driven over every key, bare and with Shift or
// Ctrl, in each kind of selection, against a stubbed store; every press it consumes
// (preventDefault or stopPropagation) must be one of core's listed chords. A key added to the
// handler without a line in layout/coreShortcutTable.ts fails here.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { onKeyDown } = await import("@/components/viewer/cellbuilder/keyboard");
const { CORE_SHORTCUTS } = await import("@/layout/coreShortcutTable");
const { normaliseChord } = await import("@/layout/keyBindings");

const BUILDER_GROUPS = new Set(["Procedural cellbuilder", "Procedural cellbuilder: typed values"]);
const listed = new Set(
  CORE_SHORTCUTS.filter((d) => BUILDER_GROUPS.has(d.group)).flatMap((d) => (d.range ?? [d.keys]).map(normaliseChord)),
);

/** A key as a test presses it: its `key` bare and shifted (a US layout), and its `code`. */
interface Key {
  key: string;
  shifted: string;
  code: string;
}
const letters: Key[] = [..."abcdefghijklmnopqrstuvwxyz"].map((c) => ({ key: c, shifted: c.toUpperCase(), code: `Key${c.toUpperCase()}` }));
const SHIFTED_DIGIT = ")!@#$%^&*(";
const digitKeys: Key[] = [..."0123456789"].map((d) => ({ key: d, shifted: SHIFTED_DIGIT[Number(d)], code: `Digit${d}` }));
const named = (key: string): Key => ({ key, shifted: key, code: key });
const KEYS: Key[] = [
  ...letters,
  ...digitKeys,
  { key: "`", shifted: "~", code: "Backquote" },
  { key: ",", shifted: "<", code: "Comma" },
  { key: ".", shifted: ">", code: "Period" },
  { key: "-", shifted: "_", code: "Minus" },
  { key: "=", shifted: "+", code: "Equal" },
  { key: " ", shifted: " ", code: "Space" },
  ...["Tab", "Enter", "Escape", "Backspace", "Delete", "PageUp", "PageDown", "Home", "End", "Insert"].map(named),
  ...["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "F1", "F2", "F5", "F6"].map(named),
];

/** The chord a press names, as the list spells it. Digits and backquote by their key cap (the handler reads `code` for them). */
function chordOf(k: Key, shift: boolean, ctrl: boolean): string {
  const cap = /^(Digit\d|Backquote)$/.test(k.code) ? k.key : shift ? k.shifted : k.key;
  return normaliseChord([ctrl ? "Ctrl" : "", shift ? "Shift" : "", cap === " " ? "Space" : cap].filter(Boolean).join("+"));
}

const noop = () => {};
const loft = { member: "M1", bay: 0, bandCount: 2, cellName: "M1_bay0", excludeFaces: [] as string[], stationLo: { TYPE: "rectangle" }, stationHi: { TYPE: "rectangle" } };
const station = (z: number) => ({ TYPE: "rectangle", X: 0, Y: 0, Z: z, WIDTH: 1, HEIGHT: 1 });
const cells = {
  s1: { id: "s1", name: "S1", kind: "cell", origin: [0, 0, 0], size: [4, 4, 3] },
  e1: { id: "e1", name: "E1", kind: "equipment", origin: [1, 1, 0], size: [1, 1, 1] },
  l1: { id: "l1", name: "L1", kind: "loft", origin: [0, 0, 0], size: [1, 1, 3], loft },
};
const refs = { r1: { id: "r1", owner: "probe", key: "k", name: "R", edit: { move: true, resize: true, remove: true } } };

/** The store's state the handler reads, every action a no-op: what a press does is not the question, only whether it is taken. */
function stubState(selection: unknown, extra: Record<string, unknown> = {}) {
  const sel = selection as { cellId?: string } | null;
  return {
    active: { modelId: "m", name: "demo/demo", revision: 1 },
    cells,
    refs,
    selection,
    selectedCellIds: sel?.cellId ? [sel.cellId] : [],
    mode: "idle",
    gizmoMode: "none",
    gizmoAxisLock: null,
    repMode: "topology",
    cellTypes: Array.from({ length: 9 }, (_, i) => ({ slug: `t${i}`, name: `T${i}` })),
    equipmentTypes: [{ slug: "pump", name: "Pump" }],
    selectedEquipmentType: "pump",
    loftMembers: [{ NAME: "M1", STATIONS: [station(0), station(3), station(6)] }],
    insertMenu: null,
    portMenu: null,
    portGizmo: null,
    contextMenu: null,
    ...Object.fromEntries(
      [
        "setMode", "cycleEquipmentType", "undo", "redo", "bumpSelectedFloor", "removeRef", "beginTransaction", "endTransaction",
        "removeCell", "removeLoftMember", "removeLoftStation", "setRepMode", "hideCells", "unhideAllCells", "setLoftFaceExcluded",
        "setGizmoMode", "setGizmoAxisLock", "addLoftMember", "cycleSelectMode", "selectAdjacentCell", "setSelection",
        "cycleSelectionElement", "setLoftStationType", "cycleCellType", "setSelectedCellType", "closePortMenu", "stopPortGizmo",
        "closeContextMenu", "insertEquipmentAtLocal", "setToolHint", "setSelectedEquipmentType", "addCell", "setInfoLabel",
      ].map((n) => [n, noop]),
    ),
    ...extra,
  };
}

/** The scene's side: no entry in progress; anything the starters touch is a stand-in that takes any call. */
function stubCtx(): Record<string, unknown> {
  const any: unknown = new Proxy(function () {}, {
    get: (_t, p) => (p === Symbol.toPrimitive ? () => 0 : any),
    apply: () => any,
    set: () => true,
  });
  const base: Record<string, unknown> = { numEntry: null, placeEntry: null, equipEntry: null, openEntry: null, modalMove: null, loftActive: null, ghostBox: null };
  return new Proxy(base, { get: (t, p) => (p in t ? t[p as string] : any), set: (t, p, v) => ((t[p as string] = v), true) });
}

const STATES: Record<string, () => ReturnType<typeof stubState>> = {
  none: () => stubState(null),
  space: () => stubState({ kind: "cell", cellId: "s1" }),
  "space, move gizmo": () => stubState({ kind: "cell", cellId: "s1" }, { gizmoMode: "translate" }),
  face: () => stubState({ kind: "face", cellId: "s1", faceIndex: 0 }),
  equipment: () => stubState({ kind: "cell", cellId: "e1" }),
  "equipment, rotate gizmo": () => stubState({ kind: "cell", cellId: "e1" }, { gizmoMode: "rotate" }),
  loft: () => stubState({ kind: "cell", cellId: "l1" }),
  "loft face": () => stubState({ kind: "face", cellId: "l1", faceIndex: 0 }),
  ref: () => stubState({ kind: "ref", refId: "r1" }),
};

test("every key the cell builder takes is listed in the shortcuts dialog", () => {
  const realGetState = useCellBuilderStore.getState;
  const unlisted = new Map<string, string[]>();
  let consumed = 0;
  try {
    for (const [name, make] of Object.entries(STATES)) {
      for (const k of KEYS) {
        for (const [shift, ctrl] of [[false, false], [true, false], [false, true]] as const) {
          const state = make();
          (useCellBuilderStore as unknown as { getState: () => unknown }).getState = () => state;
          let taken = false;
          const ev = {
            key: shift ? k.shifted : k.key,
            code: k.code,
            shiftKey: shift,
            ctrlKey: ctrl,
            metaKey: false,
            altKey: false,
            repeat: false,
            target: null,
            preventDefault: () => void (taken = true),
            stopPropagation: () => void (taken = true),
          };
          onKeyDown(stubCtx() as never, ev as unknown as KeyboardEvent);
          if (!taken) continue;
          consumed++;
          const chord = chordOf(k, shift, ctrl);
          if (!listed.has(chord)) unlisted.set(chord, [...(unlisted.get(chord) ?? []), name]);
        }
      }
    }
  } finally {
    (useCellBuilderStore as unknown as { getState: typeof realGetState }).getState = realGetState;
  }
  assert.ok(consumed > 40, `the handler ran (${consumed} presses taken)`);
  assert.deepEqual(
    [...unlisted].map(([chord, where]) => `${chord} (${[...new Set(where)].join(", ")})`),
    [],
    "taken by the builder but not in CORE_SHORTCUTS",
  );
});

test("the typed-value keys are listed as their own group, and a run of digits as one line", () => {
  const typed = CORE_SHORTCUTS.filter((d) => d.group === "Procedural cellbuilder: typed values");
  for (const key of [",", "-", ".", "Backspace", "Enter", "Escape"]) assert.ok(typed.some((d) => d.keys === key), key);
  assert.ok(typed.every((d) => d.local));
  const digitsLine = CORE_SHORTCUTS.find((d) => d.id === "core:builder-cell-type")!;
  assert.deepEqual(digitsLine.range, ["1", "2", "3", "4", "5", "6", "7", "8", "9"]);
});
