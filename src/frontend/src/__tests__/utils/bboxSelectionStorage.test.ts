// Selection boxes kept in this browser: the stored shape and its validation, the label counter
// after a restore, and the binding that restores, saves and follows the recentring offset.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

const memory = new Map<string, string>();
const storage = {
    getItem: (k: string) => (memory.has(k) ? memory.get(k)! : null),
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
    clear: () => memory.clear(),
    key: () => null,
    get length() {
        return memory.size;
    },
};
const globals = globalThis as unknown as {sessionStorage: unknown; localStorage: unknown};
globals.sessionStorage = storage;
globals.localStorage = storage;

const THREE = await import("three");
const {
    bboxStorageKey,
    highestBoxNumber,
    parseStoredBoxes,
    readStoredBoxes,
    serialiseStoredBoxes,
    shiftedBoxes,
    writeStoredBoxes,
} = await import("@/utils/bboxSelection/boxStorage");
const {useBBoxSelectionStore} = await import("@/state/bboxSelectionStore");
const {bindBBoxSelectionStorage} = await import("@/utils/bboxSelection/boxPersistence");
const {useModelState} = await import("@/state/modelState");
const {useScopeStore} = await import("@/state/scopeStore");

const box = (over: Record<string, unknown> = {}) => ({
    id: "bbox-a-1",
    label: "Selection box 1",
    min: [0, 0, 0],
    max: [1, 2, 3],
    mode: "partial",
    color: "#33aaff",
    opacity: 0.15,
    visible: true,
    ...over,
});

beforeEach(() => {
    memory.clear();
    useBBoxSelectionStore.getState().clearAll();
    useModelState.setState({translation: null});
    useScopeStore.setState({current: null});
});

test("stored boxes round-trip", () => {
    const raw = serialiseStoredBoxes({offset: [1, 2, 3], boxes: [box() as never], activeId: "bbox-a-1"});
    const s = parseStoredBoxes(raw);
    assert.deepEqual(s.offset, [1, 2, 3]);
    assert.equal(s.boxes.length, 1);
    assert.deepEqual(s.boxes[0], box());
    assert.equal(s.activeId, "bbox-a-1");
});

test("unreadable or foreign data reads as no boxes", () => {
    for (const raw of [null, "", "not json", "[]", "{}", '{"v":2,"boxes":[]}', '{"v":1,"boxes":{}}']) {
        assert.deepEqual(parseStoredBoxes(raw).boxes, [], String(raw));
    }
});

test("malformed boxes are dropped, the rest kept", () => {
    const boxes = [
        box(),
        box({id: ""}),
        box({id: "b2", min: [0, 0]}),
        box({id: "b3", min: [5, 0, 0]}),
        box({id: "b4", mode: "touching"}),
        box({id: "b5", color: "red"}),
        box({id: "b6", opacity: "half"}),
        box({id: "b7", max: [1, Infinity, 1]}),
        box({id: "bbox-a-1"}),
        "box",
        null,
        box({id: "b8", opacity: 7, label: 3, visible: "no"}),
    ];
    const s = parseStoredBoxes(JSON.stringify({v: 1, boxes, activeId: "b4"}));
    assert.deepEqual(s.boxes.map((b) => b.id), ["bbox-a-1", "b8"]);
    const b8 = s.boxes[1];
    assert.equal(b8.opacity, 1);
    assert.equal(b8.label, "b8");
    assert.equal(b8.visible, true);
    assert.equal(s.activeId, null, "an active id naming a dropped box is cleared");
    assert.equal(s.offset, null);
});

test("the label counter continues past restored labels and ids", () => {
    assert.equal(highestBoxNumber([]), 0);
    assert.equal(highestBoxNumber([{id: "bbox-x-2", label: "Selection box 7"}, {id: "bbox-y-9", label: "Mine"}]), 9);

    useBBoxSelectionStore.getState().restore([box({id: "bbox-z-4", label: "Selection box 12"}) as never], null);
    const id = useBBoxSelectionStore.getState().add({min: [0, 0, 0], max: [1, 1, 1]});
    const added = useBBoxSelectionStore.getState().boxes.find((b) => b.id === id)!;
    assert.equal(added.label, "Selection box 13");
    assert.match(id, /-13$/);
});

test("restore keeps the active box only when it is among the boxes; clearAll empties", () => {
    const st = useBBoxSelectionStore.getState();
    st.restore([box() as never], "bbox-a-1");
    assert.equal(useBBoxSelectionStore.getState().activeId, "bbox-a-1");
    st.restore([box() as never], "gone");
    assert.equal(useBBoxSelectionStore.getState().activeId, null);
    st.clearAll();
    assert.deepEqual(useBBoxSelectionStore.getState().boxes, []);
});

test("shifting moves both corners", () => {
    const [b] = shiftedBoxes([{min: [0, 0, 0], max: [1, 1, 1]}], [1, -2, 0.5]);
    assert.deepEqual(b, {min: [1, -2, 0.5], max: [2, -1, 1.5]});
});

test("storage failures are swallowed; no boxes removes the entry", () => {
    const throwing = {
        getItem: () => {
            throw new Error("denied");
        },
        setItem: () => {
            throw new Error("quota");
        },
        removeItem: () => {
            throw new Error("denied");
        },
    };
    assert.deepEqual(readStoredBoxes("shared", throwing).boxes, []);
    writeStoredBoxes("shared", {offset: null, boxes: [box() as never], activeId: null}, throwing);

    writeStoredBoxes("shared", {offset: null, boxes: [box() as never], activeId: null});
    assert.ok(memory.has(bboxStorageKey("shared")));
    writeStoredBoxes("shared", {offset: null, boxes: [], activeId: null});
    assert.ok(!memory.has(bboxStorageKey("shared")));
});

test("binding restores the scope's boxes, saves changes, and keeps scopes apart", () => {
    memory.set(bboxStorageKey("shared"), serialiseStoredBoxes({offset: null, boxes: [box() as never], activeId: "bbox-a-1"}));
    const unbind = bindBBoxSelectionStorage();
    try {
        assert.deepEqual(useBBoxSelectionStore.getState().boxes.map((b) => b.id), ["bbox-a-1"]);
        assert.equal(useBBoxSelectionStore.getState().activeId, "bbox-a-1");

        useBBoxSelectionStore.getState().update("bbox-a-1", {color: "#ff0000"});
        assert.equal(readStoredBoxes("shared").boxes[0].color, "#ff0000");

        useScopeStore.setState({current: {kind: "project", id: "p1", name: "P1"}});
        assert.deepEqual(useBBoxSelectionStore.getState().boxes, [], "another scope has its own boxes");
        useBBoxSelectionStore.getState().add({min: [0, 0, 0], max: [1, 1, 1]});
        assert.equal(readStoredBoxes("project:p1").boxes.length, 1);
        assert.equal(readStoredBoxes("shared").boxes.length, 1);

        useBBoxSelectionStore.getState().clearAll();
        assert.ok(!memory.has(bboxStorageKey("project:p1")), "Clear all removes the stored copy");
    } finally {
        unbind();
    }
});

test("restored boxes follow a changed recentring offset without applying a selection", () => {
    memory.set(
        bboxStorageKey("shared"),
        serialiseStoredBoxes({offset: [10, 0, 0], boxes: [box() as never], activeId: "bbox-a-1"}),
    );
    const unbind = bindBBoxSelectionStorage();
    const seen: {active: string | null; min: number[]}[] = [];
    const unsub = useBBoxSelectionStore.subscribe((s) => seen.push({active: s.activeId, min: s.boxes[0]?.min ?? []}));
    try {
        useModelState.getState().setTranslation(new THREE.Vector3(12, 0, 0));
        const b = useBBoxSelectionStore.getState().boxes[0];
        assert.deepEqual(b.min, [2, 0, 0]);
        assert.deepEqual(b.max, [3, 2, 3]);
        assert.equal(useBBoxSelectionStore.getState().activeId, "bbox-a-1");
        // The active box never changes bounds while active, which is what re-applies a selection.
        for (let i = 1; i < seen.length; i++) {
            if (seen[i].active && seen[i - 1].active === seen[i].active) assert.deepEqual(seen[i].min, seen[i - 1].min);
        }
        assert.deepEqual(readStoredBoxes("shared").offset, [12, 0, 0]);
    } finally {
        unsub();
        unbind();
    }
});
