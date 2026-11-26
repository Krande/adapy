// The Spaces sheet mirrored in the space form: FUNCTION / GIRDER_AS_PLATE /
// DESCRIPTION added, the exclude checkboxes reordered -X X -Y Y -Z Z and reading the SE list (not
// only SE0..SE5), GRID_*_CREATE's uncheck sticking, and Room / plated walls hidden for a
// non-built-in engine. The pure helpers first (no render needed), then the render-level checks
// that need the real store (checked state, the engine gate).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { ProceduralDoc } from "@/services/viewerApi";

await import("../fixtures/panelRealm");

const { checkboxParamValue, isDatalistPick, ParamCombo } = await import("@/components/info_box_selected_object/cellBuilderSelection/fields");
const { spaceSideExcluded, nextSpaceExcludeParams } = await import("@/utils/cellbuilder/snap");

// ── pure helpers ────────────────────────────────────────────────────────────────────────

test("checkboxParamValue: checked is always true; unchecked is null, or false when defaultOn", () => {
  assert.equal(checkboxParamValue(true), true);
  assert.equal(checkboxParamValue(true, true), true);
  assert.equal(checkboxParamValue(false), null, "default-false (the common case): remove the key");
  assert.equal(checkboxParamValue(false, true), false, "default-true: an explicit false sticks");
});

// A datalist suggestion commits only on an actual PICK, never mid-keystroke.
test("isDatalistPick: true only without an inputType, and only for an exact suggestion", () => {
  const suggestions = ["A1", "A10", "B2"];
  // Picking "A1" from the list: the browser's input event carries no inputType.
  assert.equal(isDatalistPick("A1", suggestions, undefined), true);
  assert.equal(isDatalistPick("A1", suggestions, null), true);
  // Typing "A1" on the way to "A10": a real keystroke always carries an inputType.
  assert.equal(isDatalistPick("A1", suggestions, "insertText"), false, "typing, even though 'A1' matches a suggestion");
  assert.equal(isDatalistPick("A1", suggestions, "deleteContentBackward"), false);
  // Free text that isn't a suggestion never commits early, picked or typed.
  assert.equal(isDatalistPick("A2", suggestions, undefined), false);
});

// Firefox: unlike Chromium's plain Event with no inputType, Firefox fires an InputEvent
// whose inputType is "insertReplacementText" for a datalist pick -- that counts as a pick too,
// but only together with an exact suggestion match; it never turns an ordinary keystroke into one.
test("isDatalistPick: Firefox's insertReplacementText is also a pick", () => {
  const suggestions = ["A1", "A10", "B2"];
  assert.equal(isDatalistPick("A1", suggestions, "insertReplacementText"), true);
  assert.equal(isDatalistPick("A2", suggestions, "insertReplacementText"), false, "not a suggestion, even with that inputType");
});

test("spaceSideExcluded: SE{se} === true, or the SE list names it", () => {
  assert.equal(spaceSideExcluded({ params: {} }, 0), false);
  assert.equal(spaceSideExcluded({ params: { SE0: true } }, 0), true);
  assert.equal(spaceSideExcluded({ params: { SE0: false } }, 0), false);
  // The acceptance example: SE: [0, 2] excludes -Z (0) and -Y (2), nothing else.
  const row = { params: { SE: [0, 2] } };
  assert.deepEqual([0, 1, 2, 3, 4, 5].map((se) => spaceSideExcluded(row, se)), [true, false, true, false, false, false]);
});

// A fresh doc's own SE column is adapy's comma-separated string, not an array yet --
// nextSpaceExcludeParams only starts writing a number array once the user edits a checkbox.
test("spaceSideExcluded also reads SE as a comma string, or an array of string items", () => {
  const commaRow = { params: { SE: "0,2" } };
  assert.deepEqual([0, 1, 2, 3, 4, 5].map((se) => spaceSideExcluded(commaRow, se)), [true, false, true, false, false, false]);
  const spacedRow = { params: { SE: "0, 2" } };
  assert.deepEqual([0, 1, 2, 3, 4, 5].map((se) => spaceSideExcluded(spacedRow, se)), [true, false, true, false, false, false]);
  const stringItemsRow = { params: { SE: ["0", "2"] } };
  assert.deepEqual([0, 1, 2, 3, 4, 5].map((se) => spaceSideExcluded(stringItemsRow, se)), [true, false, true, false, false, false]);
  assert.equal(spaceSideExcluded({ params: { SE: "" } }, 0), false, "an empty string excludes nothing");
});

test("nextSpaceExcludeParams: checking writes SE{se}=true; unchecking clears it AND the list, in one object", () => {
  assert.deepEqual(nextSpaceExcludeParams({}, 4, true), { SE4: true });
  // Already excluded only through the list: unchecking removes it from the list, no stray SE4 key.
  const next = nextSpaceExcludeParams({ SE: [0, 2, 4] }, 4, false);
  assert.deepEqual(next, { SE: [0, 2] });
  assert.equal("SE4" in next, false);
  // The last entry removed: the list itself goes, not an empty array.
  const empty = nextSpaceExcludeParams({ SE: [4] }, 4, false);
  assert.equal("SE" in empty, false);
  // Excluded only through its own boolean (not in the list at all): unchecking just drops it.
  assert.deepEqual(nextSpaceExcludeParams({ SE0: true, OTHER: 1 }, 0, false), { OTHER: 1 });
  // A side not present either way: checking adds it, the rest of params untouched.
  assert.deepEqual(nextSpaceExcludeParams({ AREA: "x" }, 1, true), { AREA: "x", SE1: true });
});

// A fresh doc's own SE column is adapy's comma-separated string ("0,2"), or
// (a server round-trip) an array of STRING items ("0") -- not only a number array. Unchecking a
// side excluded through either of those used to leave it checked (nothing matched `list.includes(se)`
// when `se` is a number and the list holds strings, or isn't an array at all).
test("nextSpaceExcludeParams reads the list as a comma string or string items too, not only a number array", () => {
  // Comma string: unchecking SE2 drops it from the string list, written back as a number array.
  const fromString = nextSpaceExcludeParams({ SE: "0,2" }, 2, false);
  assert.deepEqual(fromString, { SE: [0] });
  // Comma string, the last entry removed: the key itself goes.
  const fromStringEmpty = nextSpaceExcludeParams({ SE: "2" }, 2, false);
  assert.equal("SE" in fromStringEmpty, false);
  // An array of string items.
  const fromStringItems = nextSpaceExcludeParams({ SE: ["0", "2"] }, 0, false);
  assert.deepEqual(fromStringItems, { SE: [2] });
});

// A non-numeric SE item (a stray blank, a non-numeric string, ...) must be dropped, not
// rewritten as NaN -- which JSON has no way to hold, so it serializes back into the doc as a null.
test("nextSpaceExcludeParams drops a non-finite SE item instead of writing it back as NaN/null", () => {
  const withJunk = nextSpaceExcludeParams({ SE: [0, 2, "x", 4] }, 4, false);
  assert.deepEqual(withJunk, { SE: [0, 2] }, "the junk entry is gone, not kept as NaN");
  assert.ok((withJunk.SE as number[]).every((n) => Number.isFinite(n)));
  // Only junk left after unchecking the one real entry: the key itself goes, same as an empty list.
  const onlyJunk = nextSpaceExcludeParams({ SE: ["abc", 4] }, 4, false);
  assert.equal("SE" in onlyJunk, false);
});

// ── render-level: checked state and the engine gate ────────────────────────────────────

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { ObjectInfoBody } = await import("@/components/info_box_selected_object/ObjectInfoBoxComponent");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");

const st = () => useCellBuilderStore.getState();
const pristine = { ...useCellBuilderStore.getInitialState() };
/** A server render reads getInitialState(); sync the live state in, render, restore. */
function inViewer(el: React.ReactElement): string {
  Object.assign(useCellBuilderStore.getInitialState(), st());
  try {
    return renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));
  } finally {
    Object.assign(useCellBuilderStore.getInitialState(), pristine);
  }
}

const doc = {
  schema_version: "1.1",
  grid: {},
  blueprint: {},
  spaces: [{ NAME: "room", X: 0, Y: 0, Z: 0, DX: 4, DY: 4, DZ: 3, SE: [0, 2] }],
  equipments: [],
} as unknown as ProceduralDoc;

/** Whether the exclude checkbox named `aria` renders checked. */
function excludeChecked(html: string, aria: string): boolean {
  const m = new RegExp(`<input type="checkbox" aria-label="${aria.replace(/[()]/g, "\\$&")}"( checked="")?`).exec(html);
  assert.ok(m, `no such checkbox: ${aria}`);
  return !!m![1];
}

test("a row with SE: [0, 2] shows Bottom and Front checked, the rest not (reading the list, not only SE0..SE5)", () => {
  st().open("m", "Model", 1, doc);
  try {
    const room = Object.values(st().cells)[0];
    st().setSelection({ kind: "cell", cellId: room.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    // Named-view aria-labels: SE0 = Bottom (-Z), SE2 = Front (-Y).
    assert.equal(excludeChecked(html, "Exclude Bottom (SE0)"), true);
    assert.equal(excludeChecked(html, "Exclude Front (SE2)"), true);
    for (const aria of ["Exclude Left (SE4)", "Exclude Right (SE5)", "Exclude Back (SE3)", "Exclude Top (SE1)"]) {
      assert.equal(excludeChecked(html, aria), false, aria);
    }
  } finally {
    st().close();
  }
});

test("GRID_*_CREATE (defaultOn, model default True) reads an absent key as checked, not unchecked", () => {
  st().open("m", "Model", 1, doc);
  try {
    const room = Object.values(st().cells)[0];
    st().setSelection({ kind: "cell", cellId: room.id });
    const html = inViewer(React.createElement(ObjectInfoBody));
    // The doc's row sets no GRID_*_CREATE key at all: the compile's own default (True) applies,
    // so the box must show checked -- Boolean(undefined) alone would show it unchecked while the
    // compile builds the grid anyway.
    assert.equal(excludeChecked(html, "X (GRID_X_CREATE)"), true);
    assert.equal(excludeChecked(html, "Y (GRID_Y_CREATE)"), true);
    assert.equal(excludeChecked(html, "Z (GRID_Z_CREATE)"), true);
    // An explicit false still sticks (checkboxParamValue's own case, read back).
    st().setCellParam(room.id, "GRID_Y_CREATE", false);
    assert.equal(excludeChecked(inViewer(React.createElement(ObjectInfoBody)), "Y (GRID_Y_CREATE)"), false);
  } finally {
    st().close();
  }
});

test("two ParamCombo fields (same column) get distinct datalist ids, each matching its own input", () => {
  const cellA = { id: "a", kind: "cell", params: {} } as unknown as Parameters<typeof ParamCombo>[0]["cell"];
  const cellB = { id: "b", kind: "cell", params: {} } as unknown as Parameters<typeof ParamCombo>[0]["cell"];
  const html = renderToStaticMarkup(
    React.createElement(
      React.Fragment,
      null,
      React.createElement(ParamCombo, { cell: cellA, column: "AREA", label: "Area", suggestions: ["10", "20"] }),
      React.createElement(ParamCombo, { cell: cellB, column: "AREA", label: "Area", suggestions: ["10", "20"] }),
    ),
  );
  const listIds = [...html.matchAll(/<input[^>]*\blist="([^"]+)"/g)].map((m) => m[1]);
  assert.equal(listIds.length, 2);
  assert.notEqual(listIds[0], listIds[1], "the same column on two cells must not share one <datalist>");
  for (const id of listIds) assert.match(html, new RegExp(`<datalist id="${id}">`));
});

test("Room / plated walls shows only for the built-in engine (adapy-default); hidden for a non-built-in engine", () => {
  st().open("m", "Model", 1, doc);
  try {
    const room = Object.values(st().cells)[0];
    st().setSelection({ kind: "cell", cellId: room.id });
    assert.match(inViewer(React.createElement(ObjectInfoBody)), /data-field="Room">/, "adapy-default: offered");
    useCellBuilderStore.setState({ selectedEngine: "steel-structures" } as never);
    assert.doesNotMatch(inViewer(React.createElement(ObjectInfoBody)), /data-field="Room">/, "a non-built-in engine never reads it");
  } finally {
    useCellBuilderStore.setState({ selectedEngine: "adapy-default" } as never);
    st().close();
  }
});
