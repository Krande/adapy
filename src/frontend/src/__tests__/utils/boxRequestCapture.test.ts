// What a selection box holds, as members a source can be asked for: the objects come from the
// viewport on screen as object refs (model key and object id), whichever viewport that is, and are
// folded up to whole levels of the scene tree.
import assert from "node:assert/strict";
import {test} from "node:test";

const memory = new Map<string, string>();
const storage = {
    getItem: (k: string) => memory.get(k) ?? null,
    setItem: (k: string, v: string) => void memory.set(k, String(v)),
    removeItem: (k: string) => void memory.delete(k),
};
const globals = globalThis as unknown as Record<string, unknown>;
globals.sessionStorage = storage;
globals.localStorage = storage;

const {useTreeViewStore} = await import("@/state/treeViewStore");
const {captureObjects} = await import("@/utils/groups/groupScene");
type TreeNodeData = import("@/components/tree_view/CustomNode").TreeNodeData;
type ObjectRef = import("@/viewport/api/viewportApi").ObjectRef;

const leaf = (id: string, name: string, model: string, rangeId: string): TreeNodeData => ({id, name, children: [], model_key: model, node_name: `mesh_${model}`, rangeId});
const level = (id: string, name: string, model: string, children: TreeNodeData[]): TreeNodeData => ({id, name, children, model_key: model});

/** A file model: Site > Deck A {P1, P2}, Deck B {P3}. */
function scene(): void {
    const deckA = level("deckA", "Deck A", "k", [leaf("p1", "P1", "k", "10"), leaf("p2", "P2", "k", "11")]);
    const deckB = level("deckB", "Deck B", "k", [leaf("p3", "P3", "k", "12")]);
    const root: TreeNodeData = {id: "root", name: "a.ifc", children: [level("site", "Site", "k", [deckA, deckB])], model_key: "k", source_name: "proj/a.ifc"};
    useTreeViewStore.getState().setTreeData({id: "__roots__", name: "", children: [root]});
}

/** Objects as a viewport hands them over: one ref, yielded again and again with new values. */
function* objects(model: string, ids: string[]): Generator<ObjectRef> {
    const ref: ObjectRef = {model: "", id: ""};
    for (const id of ids) {
        ref.model = model;
        ref.id = id;
        yield ref;
    }
}

const FILE = {kind: "file", source_key: "proj/a.ifc"};

test("the objects a box takes become members, folded to the levels they fill", () => {
    scene();
    const some = captureObjects(objects("k", ["10", "11"]));
    assert.deepEqual(some.members, [{target: FILE, element: "Deck A", path: ["Site", "Deck A"]}]);
    assert.equal(some.reason, null);
    const one = captureObjects(objects("k", ["10"]));
    assert.deepEqual(one.members.map((m) => m.element), ["P1"]);
    const all = captureObjects(objects("k", ["10", "11", "12"]));
    assert.deepEqual(all.members, [{target: FILE, element: null, path: []}], "everything of a model is the model");
});

test("objects the scene tree does not list, and an unbuilt tree, are said so", () => {
    scene();
    const unknown = captureObjects(objects("another model", ["10"]));
    assert.deepEqual(unknown.members, []);
    assert.match(unknown.reason ?? "", /resolves to a row/);
    useTreeViewStore.getState().clearTreeData();
    assert.match(captureObjects(objects("k", ["10"])).reason ?? "", /not built yet/);
});
