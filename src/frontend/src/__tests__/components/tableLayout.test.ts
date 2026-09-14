import assert from "node:assert/strict";
import {test} from "node:test";

import {
    MAX_COLUMN_WIDTH,
    MIN_COLUMN_WIDTH,
    clampColumnWidth,
    clearColumnWidth,
    isLayoutCustomized,
    parseStoredLayout,
    reconcileLayout,
    resetLayout,
    serializeLayout,
    setColumnWidth,
    type TableLayoutColumnSpec,
    toggleColumn,
    visibleColumnKeys,
} from "@/components/common/tableLayout";

// The rules that decide what a persisted column layout means when it meets a
// different build of the app. They are the part of resizable/hideable columns
// that can go wrong silently and stay wrong: a layout is written once and read
// back for months, across releases that add, remove and reorder columns.
//
// The dragging itself is a pointer gesture against a real layout engine and is
// not exercised here — this harness has no DOM. What IS here is everything the
// gesture reduces to: a clamped number, a keyed record, and the reconciliation
// that decides whether an old record still applies.

const specs: TableLayoutColumnSpec[] = [
    {key: "name", label: "Name", required: true},
    {key: "email", label: "Email"},
    {key: "role", label: "Role"},
    {key: "seen", label: "Last seen"},
];

test("a missing, unparseable or wrong-version entry reads as no layout", () => {
    assert.equal(parseStoredLayout(null), null);
    assert.equal(parseStoredLayout(""), null);
    assert.equal(parseStoredLayout("not json"), null);
    assert.equal(parseStoredLayout("[1,2,3]"), null);
    assert.equal(parseStoredLayout('"a string"'), null);
    // No version, or a version this build does not know, is discarded rather
    // than guessed at — that is the whole point of writing one.
    assert.equal(parseStoredLayout('{"widths":{},"hidden":[]}'), null);
    assert.equal(parseStoredLayout('{"v":2,"widths":{},"hidden":[]}'), null);
});

test("a width that is not a usable number is dropped, not repaired", () => {
    const p = parseStoredLayout(
        '{"v":1,"widths":{"email":"320","role":-5,"seen":null,"name":180.6},"hidden":["role",7]}',
    );
    assert.ok(p);
    // Repairing a bad value would invent a width the user never chose and make
    // it look deliberate; only `name` survives, rounded.
    assert.deepEqual(p.widths, {name: 181});
    assert.deepEqual(p.hidden, ["role"]);
});

test("a stored key the build no longer has is forgotten, and the rest still applies", () => {
    // The reason the record is keyed by column rather than positional: a release
    // that drops "seen" and adds "added" must not reset everything else, and
    // must certainly not shift "email"'s width onto another column.
    const stored = parseStoredLayout(
        '{"v":1,"widths":{"email":320,"gone":700},"hidden":["seen","also-gone"]}',
    );
    const state = reconcileLayout(stored, specs);
    assert.deepEqual(state.widths, {email: 320});
    assert.deepEqual(state.hidden, ["seen"]);
    // Columns the layout says nothing about keep their declared defaults.
    assert.deepEqual(visibleColumnKeys(specs, state), ["name", "email", "role"]);
});

test("a required column cannot be hidden by a stored layout", () => {
    // localStorage is user-editable. The identity column has to survive a
    // hand-written entry, not just the UI refusing to offer it.
    const stored = parseStoredLayout('{"v":1,"widths":{},"hidden":["name","role"]}');
    const state = reconcileLayout(stored, specs);
    assert.deepEqual(state.hidden, ["role"]);
    assert.ok(visibleColumnKeys(specs, state).includes("name"));
});

test("a layout that would hide every column is refused entirely", () => {
    // An empty table has no header row left to re-open the chooser from, so this
    // state is a corner the user cannot get out of. Cheaper to refuse it than to
    // build a rescue path.
    const all: TableLayoutColumnSpec[] = [{key: "a"}, {key: "b"}];
    const stored = parseStoredLayout('{"v":1,"widths":{},"hidden":["a","b"]}');
    const state = reconcileLayout(stored, all);
    assert.deepEqual(state.hidden, []);
    assert.deepEqual(visibleColumnKeys(all, state), ["a", "b"]);
});

test("widths are clamped into the usable range on the way in", () => {
    const stored = parseStoredLayout('{"v":1,"widths":{"email":2,"role":99999},"hidden":[]}');
    const state = reconcileLayout(stored, specs);
    assert.equal(state.widths.email, MIN_COLUMN_WIDTH);
    assert.equal(state.widths.role, MAX_COLUMN_WIDTH);
    assert.equal(clampColumnWidth(Number.NaN), MIN_COLUMN_WIDTH);
    assert.equal(clampColumnWidth(Number.POSITIVE_INFINITY), MAX_COLUMN_WIDTH);
    assert.equal(clampColumnWidth(123.4), 123);
});

test("no stored layout means no overrides at all", () => {
    const state = reconcileLayout(null, specs);
    assert.deepEqual(state.hidden, []);
    assert.deepEqual(state.widths, {});
    assert.equal(isLayoutCustomized(state), false);
    // Every column starts visible: there is no hidden-by-default flag, because a
    // column introduced as hidden in a later release could never reach a user
    // who already has a stored layout, and the inconsistency would be invisible.
    assert.deepEqual(visibleColumnKeys(specs, state), ["name", "email", "role", "seen"]);
});

test("toggling hides and shows, and refuses the two cases that break the table", () => {
    let s = reconcileLayout(null, specs);
    s = toggleColumn(specs, s, "email");
    assert.deepEqual(s.hidden, ["email"]);
    s = toggleColumn(specs, s, "email");
    assert.deepEqual(s.hidden, []);

    // Required is refused, and refused inertly — the same object comes back, so
    // a click that somehow got past the disabled checkbox costs one no-op render
    // rather than throwing.
    const before = s;
    assert.equal(toggleColumn(specs, s, "name"), before);
    // An unknown key is equally inert.
    assert.equal(toggleColumn(specs, s, "nope"), before);

    s = toggleColumn(specs, s, "email");
    s = toggleColumn(specs, s, "role");
    s = toggleColumn(specs, s, "seen");
    assert.deepEqual(visibleColumnKeys(specs, s), ["name"]);
    // "name" is required AND last; either alone is enough to refuse.
    assert.equal(toggleColumn(specs, s, "name"), s);

    const plain: TableLayoutColumnSpec[] = [{key: "a"}, {key: "b"}];
    const one = toggleColumn(plain, {widths: {}, hidden: []}, "b");
    assert.deepEqual(one.hidden, ["b"]);
    assert.equal(toggleColumn(plain, one, "a"), one); // last one standing
});

test("widths are set, cleared and reset, and an unchanged write is a no-op", () => {
    let s = setColumnWidth({widths: {}, hidden: []}, "email", 320);
    assert.deepEqual(s.widths, {email: 320});
    assert.equal(setColumnWidth(s, "email", 320), s); // identical → same object
    assert.equal(setColumnWidth(s, "email", 320.4), s); // rounds to the same px
    s = setColumnWidth(s, "email", 10);
    assert.equal(s.widths.email, MIN_COLUMN_WIDTH);

    // Home / double-click: forget the override and fall back to whatever the
    // column's own `col` declaration says, rather than to some remembered number.
    const cleared = clearColumnWidth(s, "email");
    assert.deepEqual(cleared.widths, {});
    assert.equal(clearColumnWidth(cleared, "email"), cleared);

    const custom = toggleColumn(specs, setColumnWidth(cleared, "role", 200), "seen");
    assert.equal(isLayoutCustomized(custom), true);
    const reset = resetLayout(custom);
    assert.deepEqual(reset, {widths: {}, hidden: []});
    assert.equal(isLayoutCustomized(reset), false);
    assert.equal(resetLayout(reset), reset);
});

test("serialize and read back is a round trip through the same reconciliation", () => {
    const written = toggleColumn(specs, setColumnWidth({widths: {}, hidden: []}, "email", 320), "seen");
    const read = reconcileLayout(parseStoredLayout(serializeLayout(written)), specs);
    assert.deepEqual(read, written);
});
