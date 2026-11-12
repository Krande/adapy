// The loads a switch between the three.js viewport and the three.js-free one loads again.
import assert from "node:assert/strict";
import {test} from "node:test";

import {clearModelJournal, loadedModelJournal, recordModelLoad} from "@/components/viewer/sceneHelpers/loadedModelJournal";
import {useModelState} from "@/state/modelState";

const names = () => loadedModelJournal().entries.map((e) => e.sourceName);

test("a replace starts the list over, an overlay joins it and replaces an earlier load of its source", () => {
    clearModelJournal();
    recordModelLoad({sourceName: "a.glb", bytes: {from: "url", url: "blob:a"}});
    recordModelLoad({sourceName: "b.glb", bytes: {from: "url", url: "blob:b"}, placement: "overlay"});
    recordModelLoad({sourceName: "c.glb", bytes: {from: "storage", scope: "s", glbKey: "c.glb"}, placement: "overlay"});
    assert.deepEqual(names(), ["a.glb", "b.glb", "c.glb"]);
    recordModelLoad({sourceName: "b.glb", bytes: {from: "url", url: "blob:b2"}, placement: "overlay"});
    assert.deepEqual(names(), ["a.glb", "c.glb", "b.glb"]);
    assert.deepEqual(loadedModelJournal().entries[2].bytes, {from: "url", url: "blob:b2"});
    recordModelLoad({sourceName: "d.glb", bytes: {from: "bytes", bytes: new Uint8Array([1])}});
    assert.deepEqual(names(), ["d.glb"]);
    clearModelJournal();
});

test("a replay records no admin metrics", () => {
    clearModelJournal();
    recordModelLoad({sourceName: "a.glb", bytes: {from: "storage", scope: "s", glbKey: "a.glb"}, metrics: true});
    assert.equal(loadedModelJournal().entries[0].metrics, false);
    clearModelJournal();
});

test("a model unloaded from the viewer leaves the list; with nothing loaded the list is empty", () => {
    clearModelJournal();
    const ms = useModelState.getState();
    ms.clearLoadedSources();
    recordModelLoad({sourceName: "a.glb", bytes: {from: "url", url: "blob:a"}});
    ms.setLoadedSourceName("a.glb");
    recordModelLoad({sourceName: "b.glb", bytes: {from: "url", url: "blob:b"}, placement: "overlay"});
    ms.registerLoadedSourceName("b.glb");
    recordModelLoad({sourceName: "", bytes: {from: "url", url: "blob:unnamed"}, placement: "overlay"});
    assert.deepEqual(names(), ["a.glb", "b.glb", ""]);
    ms.unregisterLoadedSource("a.glb");
    assert.deepEqual(names(), ["b.glb", ""], "an unnamed load stays while something is loaded");
    ms.clearLoadedSources();
    assert.deepEqual(names(), []);
});
