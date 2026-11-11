// The Scene panel's group picker on either viewport: a group's members become object refs the
// viewport on screen selects, and the groups of a model a viewport loaded without a three.js scene
// are read from the extension text it announces.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

import {useSceneInfoStore, type GroupInfo} from "@/state/sceneInfoStore";
import {announcedGroups, groupsFromExtension, groupsFromExtensionText, listAnnouncedGroups} from "@/utils/groups/extensionGroups";
import {elementRefs, modelKeyOfSource, selectGroup, type ModelRow, type SelectGroupDeps} from "@/utils/groups/selectGroup";
import {setActiveViewport, type ObjectRef, type ViewportApi} from "@/viewport/api/viewportApi";

const root = (model_key: string, source_name: string): ModelRow => ({children: [], model_key, source_name});
const TREE: ModelRow = {model_key: null, children: [root("native-1", "a.glb"), root("fea-result:run.sif", "run.sif")]};

/** A viewport that notes what it was asked to select, and what is on screen. */
function fakeViewport(kind: "three" | "native", sources: string[] = []) {
    const calls: {refs: ObjectRef[]; distinct: number}[] = [];
    let cleared = 0;
    const api = {
        kind,
        setSelection: (refs: Iterable<ObjectRef>, options?: {within?: () => void}) => {
            const seen = new Set<ObjectRef>();
            const copies: ObjectRef[] = [];
            for (const ref of refs) {
                seen.add(ref);
                copies.push({...ref});
            }
            calls.push({refs: copies, distinct: seen.size});
            options?.within?.();
            return copies.length;
        },
        clearSelection: () => void cleared++,
        sourceOrigin: (source: string) => (sources.includes(source) ? [0, 0, 0] : null),
    } as unknown as ViewportApi;
    return {api, calls, cleared: () => cleared, sources};
}

let names: string[] = [];
let selected: (GroupInfo | null)[] = [];
let asked: [string, string[]][] = [];
const deps = (over: Partial<SelectGroupDeps> = {}): SelectGroupDeps => ({
    setSelectedGroup: (g) => void selected.push(g),
    setName: (n) => void names.push(n),
    tree: () => TREE,
    objectsNamed: async (model, members) => {
        asked.push([model, members]);
        return members.filter((m) => m !== "missing").map((m) => ["node0", `id-${m}`] as [string, string]);
    },
    ...over,
});

let withdraw: () => void = () => {};
beforeEach(() => {
    withdraw();
    withdraw = () => {};
    names = [];
    selected = [];
    asked = [];
    useSceneInfoStore.setState({availableGroups: [], selectedGroup: null});
});

test("a model is found by the source it was loaded from, in the tree's roots", () => {
    assert.equal(modelKeyOfSource(TREE, "a.glb"), "native-1");
    assert.equal(modelKeyOfSource(TREE, "run.sif"), "fea-result:run.sif");
    assert.equal(modelKeyOfSource(TREE, "other.glb"), null);
    assert.equal(modelKeyOfSource(TREE, undefined), "native-1", "a group with no source belongs to the model there is");
    assert.equal(modelKeyOfSource(root("only", "b.glb"), "b.glb"), "only");
    assert.equal(modelKeyOfSource(null, "b.glb"), null);
});

test("a GLB's group selects the objects its members name, through the viewport on screen", async () => {
    const v = fakeViewport("native");
    withdraw = setActiveViewport(v.api);
    const group: GroupInfo = {name: "deck", members: ["p1", "missing", "p2"], type: "design", parent_name: "hull", source: "a.glb"};
    assert.equal(await selectGroup(group, deps()), 2);
    assert.deepEqual(asked, [["native-1", ["p1", "missing", "p2"]]]);
    assert.deepEqual(v.calls[0].refs, [
        {model: "native-1", id: "id-p1", node: "node0"},
        {model: "native-1", id: "id-p2", node: "node0"},
    ]);
    assert.equal(v.calls[0].distinct, 1, "one ref is reused for every member");
    assert.deepEqual(selected, [group]);
    assert.deepEqual(names, ["Group: deck"]);
});

test("a streamed result's set selects its elements by id, without a lookup; nodes are left out", async () => {
    const v = fakeViewport("native");
    withdraw = setActiveViewport(v.api);
    const group: GroupInfo = {name: "tank", members: ["EL7", "P3", "EL12"], type: "simulation", parent_name: "run.sif", fe_object_type: "element", streamed: true};
    assert.equal(await selectGroup(group, deps()), 2);
    assert.deepEqual(asked, []);
    assert.deepEqual(v.calls[0].refs, [{model: "fea-result:run.sif", id: "E7"}, {model: "fea-result:run.sif", id: "E12"}]);
    assert.equal(v.calls[0].distinct, 1);
    assert.deepEqual([...elementRefs("m", ["P1", "P2"])], []);
});

test("the caller's own scene may name the model, and may take a group of points itself", async () => {
    const v = fakeViewport("three");
    withdraw = setActiveViewport(v.api);
    const points: [string, string[]][] = [];
    const own = deps({
        modelKeyOf: () => "hash-1",
        selectPoints: async (model, members) => {
            points.push([model, members]);
            return true;
        },
    });
    const nodes: GroupInfo = {name: "supports", members: ["n1", "n2"], type: "simulation", parent_name: "fem", fe_object_type: "node", source: "a.glb"};
    assert.equal(await selectGroup(nodes, own), 2);
    assert.deepEqual(points, [["hash-1", ["n1", "n2"]]]);
    assert.equal(v.calls.length, 0);
    assert.deepEqual(names, ["Group: supports"]);

    const elements: GroupInfo = {...nodes, name: "plates", fe_object_type: "element"};
    await selectGroup(elements, own);
    assert.equal(v.calls[0].refs[0].model, "hash-1");

    // Where the points are objects like any other, they are selected as objects.
    await selectGroup(nodes, deps({selectPoints: async () => false}));
    assert.deepEqual(v.calls[1].refs.map((r) => r.id), ["id-n1", "id-n2"]);
});

test("no group clears the selection; a group whose model is gone, or with no members, selects nothing", async () => {
    const v = fakeViewport("native");
    withdraw = setActiveViewport(v.api);
    assert.equal(await selectGroup(null, deps()), 0);
    assert.equal(v.cleared(), 1);
    assert.deepEqual([selected, names], [[null], [""]]);

    const gone: GroupInfo = {name: "g", members: ["x"], type: "design", parent_name: "p", source: "unloaded.glb"};
    assert.equal(await selectGroup(gone, deps()), 0);
    assert.equal(await selectGroup({...gone, members: []}, deps()), 0);
    assert.equal(v.calls.length, 0);
});

const EXT = {
    design_objects: [{name: "hull", groups: [{name: "deck", description: "upper", members: ["p1", "p2"]}, {members: []}]}],
    simulation_objects: [{groups: [{name: "supports", members: ["n1"], fe_object_type: "node" as const}]}],
};

test("the groups of an extension keep the member lists as they are, as an object or as JSON text", () => {
    const groups = groupsFromExtension(EXT, "a.glb");
    assert.deepEqual(groups.map((g) => [g.name, g.type, g.parent_name, g.source, g.fe_object_type]), [
        ["deck", "design", "hull", "a.glb", undefined],
        ["Unnamed Group", "design", "hull", "a.glb", undefined],
        ["supports", "simulation", "Unnamed Object", "a.glb", "node"],
    ]);
    assert.equal(groups[0].members, EXT.design_objects[0].groups[0].members, "not copied");
    assert.equal(groupsFromExtensionText(JSON.stringify(EXT), "a.glb").length, 3);
    assert.deepEqual(groupsFromExtensionText("", "a.glb"), []);
    assert.deepEqual(groupsFromExtension(null, undefined), []);
});

test("a model a viewport announces lists its groups; they go with the model", () => {
    const v = fakeViewport("native", ["a.glb"]);
    withdraw = setActiveViewport(v.api);
    const text = JSON.stringify(EXT);
    const listed = () => useSceneInfoStore.getState().availableGroups.map((g) => `${g.source}:${g.name}`);
    listAnnouncedGroups({model: "native-1", sourceName: "a.glb", sceneExtras: "", adaExtData: text});
    assert.deepEqual(listed(), ["a.glb:deck", "a.glb:Unnamed Group", "a.glb:supports"]);

    // One that joins adds its groups; one without the extension adds none.
    v.sources.push("b.glb", "c.glb");
    listAnnouncedGroups({added: true, model: "native-2", sourceName: "b.glb", sceneExtras: "", adaExtData: JSON.stringify({design_objects: [{name: "o", groups: [{name: "extra", members: ["q"]}]}]})});
    listAnnouncedGroups({added: true, model: "native-3", sourceName: "c.glb", sceneExtras: "", adaExtData: ""});
    assert.equal(listed().length, 4);
    assert.equal(listed()[3], "b.glb:extra");

    // Unloaded: no longer among the groups to list.
    v.sources.splice(v.sources.indexOf("a.glb"), 1);
    assert.deepEqual(announcedGroups().map((g) => g.name), ["extra"]);

    // A result that replaces the scene has its sets from its manifest: the store is left to it.
    useSceneInfoStore.getState().setAvailableGroups([{name: "set", type: "simulation", parent_name: "run.sif", streamed: true}]);
    listAnnouncedGroups({model: "fea-result:run.sif", sourceName: "run.sif", sceneExtras: ""});
    assert.deepEqual(useSceneInfoStore.getState().availableGroups.map((g) => g.name), ["set"]);
    assert.deepEqual(announcedGroups(), []);

    // A GLB without groups that replaces one with groups empties the list.
    listAnnouncedGroups({model: "native-4", sourceName: "a.glb", sceneExtras: "", adaExtData: text});
    v.sources.push("a.glb");
    listAnnouncedGroups({model: "native-5", sourceName: "c.glb", sceneExtras: "", adaExtData: ""});
    assert.deepEqual(listed(), []);
});

test("the three.js viewport's groups are read from its scene: none are kept from another viewport", () => {
    const native = fakeViewport("native", ["a.glb"]);
    withdraw = setActiveViewport(native.api);
    listAnnouncedGroups({model: "native-1", sourceName: "a.glb", sceneExtras: "", adaExtData: JSON.stringify(EXT)});
    withdraw();
    withdraw = setActiveViewport(fakeViewport("three", ["a.glb"]).api);
    assert.deepEqual(announcedGroups(), []);
});
