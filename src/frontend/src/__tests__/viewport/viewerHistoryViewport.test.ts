// The undo / redo history against the viewport interface: it asks the viewport on screen for its state
// before an action, for what the action changed, and hands back one end of the step on undo and redo.
// A stand-in viewport here; the three.js viewport's own state is tested against a real scene
// (scene/viewerHistory.scene.test.ts) and the three.js-free viewport's in nativeViewportHistory.test.ts.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

import {useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {recordViewerAction, redoViewerAction, undoViewerAction, type StateSlice} from "@/utils/scene/viewerHistory";
import {setActiveViewport, type ViewportApi} from "@/viewport/api/viewportApi";
import type {ViewportState, ViewportStateParts} from "@/viewport/api/viewportState";

interface Snapshot extends ViewportState {
    selection: string[];
    hidden: string[] | null;
}

/** A viewport that is two sets, and a log of what the history asked it. */
function fakeViewport() {
    const selection = new Set<string>();
    const hidden = new Set<string>();
    const asked: string[] = [];
    const snapshot = (what: ViewportStateParts): Snapshot => ({
        selected: selection.size,
        selection: [...selection],
        hidden: what.visibility ? [...hidden] : null,
    });
    const api = {
        kind: "native",
        captureState: (what: ViewportStateParts) => {
            asked.push(`capture${what.selection ? " selection" : ""}${what.visibility ? " visibility" : ""}`);
            return snapshot(what);
        },
        stateChange: (state: ViewportState) => {
            const before = state as Snapshot;
            const hid = before.hidden ? [...hidden].filter((id) => !before.hidden!.includes(id)) : [];
            const showed = before.hidden ? before.hidden.filter((id) => !hidden.has(id)) : [];
            return {
                selection: before.selection.join() !== [...selection].join(),
                visibility: hid.length + showed.length > 0,
                hidden: hid.length,
                shown: showed.length,
                after: () => (asked.push("after"), snapshot({visibility: before.hidden !== null})),
            };
        },
        restoreState: (state: ViewportState) => {
            const to = state as Snapshot;
            asked.push("restore");
            selection.clear();
            to.selection.forEach((id) => selection.add(id));
            if (to.hidden) {
                hidden.clear();
                to.hidden.forEach((id) => hidden.add(id));
            }
        },
    } as unknown as ViewportApi;
    return {api, selection, hidden, asked};
}

const hist = () => useViewerHistoryStore.getState();
let v: ReturnType<typeof fakeViewport>;
let withdraw: () => void = () => {};

beforeEach(() => {
    withdraw();
    v = fakeViewport();
    withdraw = setActiveViewport(v.api);
    hist().reset();
});

const select = (ids: string[], coalesce?: string) =>
    recordViewerAction("select", () => {
        v.selection.clear();
        ids.forEach((id) => v.selection.add(id));
    }, {coalesce});

test("a selection asks for the selection only; an action that can hide asks for visibility too", () => {
    select(["a"]);
    assert.deepEqual(v.asked, ["capture selection", "after"]);
    v.asked.length = 0;
    recordViewerAction("hide", () => void v.hidden.add("a"));
    assert.deepEqual(v.asked, ["capture selection visibility", "after"]);
    v.asked.length = 0;
    recordViewerAction("box-edit", () => {});
    recordViewerAction("plane-edit", () => {});
    recordViewerAction("clear-selection", () => {});
    assert.deepEqual(v.asked, ["capture selection", "capture selection", "capture selection"], "nothing changed: no state after, no entry");
    assert.equal(hist().past.length, 2);
});

test("each action is one step, and undo and redo hand its two ends back to the viewport", () => {
    select(["a", "b"]);
    recordViewerAction("hide", () => {
        v.selection.forEach((id) => v.hidden.add(id));
        v.selection.clear();
    });
    assert.deepEqual(hist().past.map((e) => e.label), ["select 2 objects", "hide 2 objects"]);

    assert.equal(undoViewerAction(), "hide 2 objects");
    assert.deepEqual([[...v.selection], [...v.hidden]], [["a", "b"], []]);
    assert.equal(undoViewerAction(), "select 2 objects");
    assert.deepEqual([[...v.selection], [...v.hidden]], [[], []]);
    assert.equal(undoViewerAction(), null);

    redoViewerAction();
    redoViewerAction();
    assert.deepEqual([[...v.selection], [...v.hidden]], [[], ["a", "b"]]);
    assert.equal(redoViewerAction(), null);
});

test("an action inside another is part of it, and nothing records while a step is applied", () => {
    recordViewerAction("isolate", () => {
        select(["a"]);
        recordViewerAction("hide", () => void v.hidden.add("b"));
    });
    assert.deepEqual(hist().past.map((e) => e.label), ["isolate (hide 1 object)"]);
    // A viewport whose restore runs recorded operations of its own must not grow the history.
    const restore = v.api.restoreState;
    (v.api as {restoreState: ViewportApi["restoreState"]}).restoreState = (state) => {
        select(["z"]);
        restore(state);
    };
    undoViewerAction();
    assert.deepEqual([hist().past.length, hist().future.length], [0, 1]);
    assert.deepEqual([[...v.selection], [...v.hidden]], [[], []]);
});

test("selections with one coalesce key in quick succession are one step, ended by anything else", () => {
    select(["a"], "box:1");
    select(["a", "b"], "box:1");
    select(["a", "b", "c"], "box:1");
    assert.deepEqual(hist().past.map((e) => e.label), ["select 3 objects"]);
    select(["d"], "box:2");
    select(["d", "e"]);
    select(["d"], "box:2");
    assert.equal(hist().past.length, 4, "another key, no key, and a key after another entry each start a step");
    hist().reset();
    v.selection.clear();

    select(["a"], "box:1");
    select(["a", "b"], "box:1");
    undoViewerAction();
    assert.deepEqual([...v.selection], [], "undo goes back to before the first of them");
    redoViewerAction();
    assert.deepEqual([...v.selection], ["a", "b"], "redo to after the last");
    undoViewerAction();
    select(["c"], "box:1");
    assert.deepEqual([hist().past.length, hist().future.length], [1, 0], "an undone entry is not joined: this is a new step");
    undoViewerAction();
    assert.deepEqual([...v.selection], []);
});

test("store slices are part of the step, restored before the viewport's state", () => {
    let boxes = ["box 1"];
    const order: string[] = [];
    const slice: StateSlice = {
        get: () => boxes,
        set: (value) => {
            boxes = value as string[];
            order.push("slice");
            // What a restored selection box does: it selects what it takes.
            v.selection.add("taken by the box");
        },
    };
    select(["a"]);
    recordViewerAction("box-add", () => {
        boxes = [...boxes, "box 2"];
        v.selection.clear();
        v.selection.add("b");
    }, {slices: [slice]});
    assert.equal(hist().past[1].label, "add selection box");
    v.asked.length = 0;
    undoViewerAction();
    assert.deepEqual(boxes, ["box 1"]);
    assert.deepEqual([...v.selection], ["a"], "the viewport's state is the last word");
    assert.deepEqual([...order, ...v.asked], ["slice", "restore"]);
    redoViewerAction();
    assert.deepEqual([boxes, [...v.selection]], [["box 1", "box 2"], ["b"]]);
});

test("with no viewport on screen the slices alone are a step", () => {
    withdraw();
    let planes = 1;
    const slice: StateSlice = {get: () => planes, set: (value) => void (planes = value as number)};
    recordViewerAction("plane-add", () => void planes++, {slices: [slice]});
    recordViewerAction("hide", () => {});
    assert.deepEqual(hist().past.map((e) => e.label), ["add section plane"]);
    undoViewerAction();
    assert.equal(planes, 1);
    redoViewerAction();
    assert.equal(planes, 2);
});

test("a step is handed to the viewport on screen when it is undone", () => {
    select(["a"]);
    withdraw();
    const other = fakeViewport();
    withdraw = setActiveViewport(other.api);
    undoViewerAction();
    assert.deepEqual(v.asked.filter((a) => a === "restore"), [], "the viewport that left is not touched");
    assert.deepEqual(other.asked, ["restore"], "the one on screen decides whether the state is its own");
});
