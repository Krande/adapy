// The registry of named layers: what draws a layer joins it and is told its look, the look is kept
// per layer id in the browser's storage, and a layer goes with its last member.
import assert from "node:assert/strict";
import {beforeEach, test} from "node:test";

import {joinLayer, layerCount, listLayers, resetLayerRegistry, setLayerOpacity, setLayerVisible, subscribeLayers} from "@/state/layerRegistry";

const stored = new Map<string, string>();
let failing = false;
(globalThis as {localStorage?: unknown}).localStorage = {
    getItem: (key: string) => {
        if (failing) throw new Error("storage refused");
        return stored.get(key) ?? null;
    },
    setItem: (key: string, value: string) => {
        if (failing) throw new Error("storage refused");
        stored.set(key, value);
    },
};

beforeEach(() => {
    stored.clear();
    failing = false;
    resetLayerRegistry();
});

test("a member is given the layer's look when it joins and when it changes", () => {
    const seen: [boolean, number][] = [];
    const leave = joinLayer({id: "map", label: "Map", group: "Run"}, (visible, opacity) => void seen.push([visible, opacity]));
    assert.deepEqual(seen, [[true, 1]]);
    assert.deepEqual(listLayers(), [{id: "map", label: "Map", group: "Run", visible: true, opacity: 1}]);
    setLayerVisible("map", false);
    setLayerOpacity("map", 0.4);
    assert.deepEqual(seen.slice(1), [[false, 1], [false, 0.4]]);
    setLayerOpacity("map", 0.4);
    assert.equal(seen.length, 3, "the same look again tells nobody");
    setLayerOpacity("map", 7);
    assert.equal(listLayers()[0].opacity, 1, "kept within 0..1");
    leave();
    assert.equal(layerCount(), 0, "the layer goes with its last member");
});

test("several members are one layer, and it stays while one is left", () => {
    const a: boolean[] = [], b: boolean[] = [];
    const leaveA = joinLayer({id: "scan", label: "Scan"}, (visible) => void a.push(visible));
    const leaveB = joinLayer({id: "scan", label: "Scan"}, (visible) => void b.push(visible));
    assert.equal(layerCount(), 1);
    setLayerVisible("scan", false);
    assert.deepEqual([a, b], [[true, false], [true, false]]);
    leaveA();
    assert.equal(layerCount(), 1);
    leaveB();
    leaveB();
    assert.equal(layerCount(), 0);
});

test("the look is remembered per layer id and given to the layer when it is registered again", () => {
    const leave = joinLayer({id: "path", label: "Path"}, () => {});
    setLayerVisible("path", false);
    setLayerOpacity("path", 0.25);
    leave();
    // A new page: what is known is what the browser kept.
    resetLayerRegistry();
    const seen: [boolean, number][] = [];
    joinLayer({id: "path", label: "Path"}, (visible, opacity) => void seen.push([visible, opacity]), {visible: true, opacity: 1});
    assert.deepEqual(seen, [[false, 0.25]]);
    // A layer never changed starts as it is registered.
    const other: [boolean, number][] = [];
    joinLayer({id: "other", label: "Other"}, (visible, opacity) => void other.push([visible, opacity]), {opacity: 0.6});
    assert.deepEqual(other, [[true, 0.6]]);
});

test("storage that refuses or holds something else costs only the remembering", () => {
    failing = true;
    resetLayerRegistry();
    const seen: boolean[] = [];
    joinLayer({id: "a", label: "A"}, (visible) => void seen.push(visible));
    setLayerVisible("a", false);
    assert.deepEqual(seen, [true, false]);
    failing = false;
    stored.set("ada-layer-look", "not json");
    resetLayerRegistry();
    joinLayer({id: "b", label: "B"}, () => {});
    assert.equal(listLayers()[0].visible, true);
});

test("the list is the same array until a layer changes, and listeners hear of each change", () => {
    let heard = 0;
    const off = subscribeLayers(() => heard++);
    const leave = joinLayer({id: "a", label: "A"}, () => {});
    assert.equal(heard, 1);
    const list = listLayers();
    assert.equal(listLayers(), list);
    setLayerOpacity("a", 0.5);
    assert.equal(heard, 2);
    assert.notEqual(listLayers(), list);
    leave();
    assert.equal(heard, 3);
    off();
});
