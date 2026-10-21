// The record of loaded models a reload restores: its stored shape and validation, keeping it in
// step with loads and unloads, and what a restore still has to load.
import assert from "node:assert/strict";
import {test} from "node:test";

import {assetSourceName} from "@/assets/delivery";
import {
    EMPTY_SCENE_RECORD,
    parseSceneRecord,
    readSceneRecord,
    recordedAsset,
    recordedSourceName,
    removedNames,
    restorePlan,
    sceneRecordKey,
    serialiseSceneRecord,
    updateSceneRecord,
    withModel,
    withoutNames,
    writeSceneRecord,
    type RecordedModel,
    type SceneRecord,
} from "@/utils/scene/sceneRecord";

function memoryStorage() {
    const m = new Map<string, string>();
    return {
        m,
        getItem: (k: string) => m.get(k) ?? null,
        setItem: (k: string, v: string) => void m.set(k, String(v)),
        removeItem: (k: string) => void m.delete(k),
    };
}

const file: RecordedModel = {kind: "file", name: "models/frame.ifc"};
const streamed: RecordedModel = {kind: "file", name: "big.step", streamer: true};
const asset: RecordedModel = {kind: "asset", provider: "p", collection: "c", subject: "s", revision: "r1"};
const covered: RecordedModel = {kind: "asset", provider: "p", collection: "c", subject: "s", revision: "r1", node: "n"};

test("a record round-trips, camera included", () => {
    const rec: SceneRecord = {
        v: 1,
        models: [file, streamed, asset, covered],
        camera: {v: 1, translation: null, position: [1, 2, 3], target: [0, 0, 0], up: [0, 0, 1]},
    };
    assert.deepEqual(parseSceneRecord(serialiseSceneRecord(rec)), rec);
});

test("an asset is recorded under its scene name", () => {
    const ref = {provider: "p", collection: "c", subject: "s", revision: "r1", node: "n"};
    assert.equal(recordedSourceName(recordedAsset(ref)), assetSourceName(ref));
    assert.equal(recordedSourceName(recordedAsset({...ref, node: "s"})), assetSourceName({...ref, node: "s"}));
    assert.equal(recordedSourceName(file), "models/frame.ifc");
});

test("an asset keeps the name it was shown under, which does not change its scene name", () => {
    const ref = {provider: "p", collection: "c", subject: "s", revision: "r1"};
    const named = recordedAsset(ref, "/SITE-A");
    assert.deepEqual(parseSceneRecord(serialiseSceneRecord({v: 1, models: [named], camera: null})).models, [named]);
    assert.equal(recordedSourceName(named), assetSourceName(ref));
    assert.equal("label" in recordedAsset(ref, ""), false);
});

test("unreadable data reads as an empty record", () => {
    for (const raw of [null, "", "{", "[]", "{}", '{"v":2,"models":[]}', '{"v":1,"models":"x"}']) {
        assert.deepEqual(parseSceneRecord(raw), EMPTY_SCENE_RECORD, String(raw));
    }
});

test("malformed models are dropped, duplicates kept once at their first place", () => {
    const raw = JSON.stringify({
        v: 1,
        models: [
            file,
            {kind: "file"},
            {kind: "file", name: 3},
            {kind: "file", name: "assets:p/c/s@r1"},
            {kind: "file", name: "https://example.invalid/x.glb"},
            {kind: "asset", provider: "p", collection: "c", subject: "s"},
            {kind: "asset", provider: "p", collection: "c", subject: "s", revision: "r1", node: 5},
            {kind: "mesh", name: "x"},
            null,
            asset,
            {...file},
            {kind: "file", name: "a.glb", streamer: "yes"},
        ],
        camera: {v: 1, position: [1, 2], target: [0, 0, 0], up: [0, 0, 1]},
    });
    const rec = parseSceneRecord(raw);
    assert.deepEqual(rec.models, [file, asset, {kind: "file", name: "a.glb"}]);
    assert.equal(rec.camera, null, "a malformed camera is dropped");
});

test("loads append once; unloads remove by scene name", () => {
    let models = withModel([], file);
    models = withModel(models, asset);
    models = withModel(models, {kind: "file", name: "models/frame.ifc", streamer: true});
    assert.deepEqual(models, [file, asset]);
    assert.deepEqual(withoutNames(models, [assetSourceName({provider: "p", collection: "c", subject: "s", revision: "r1"})]), [file]);
    assert.deepEqual(removedNames(new Set(["a", "b", "c"]), new Set(["b", "d"])), ["a", "c"]);
});

test("a restore loads, in order, what is not in the scene and not loaded by someone else", () => {
    const rec: SceneRecord = {v: 1, models: [file, streamed, asset, covered], camera: null};
    const live = new Set(["big.step"]);
    assert.deepEqual(restorePlan(rec, live), [file, asset, covered]);
    assert.deepEqual(restorePlan(rec, live, new Set(["models/frame.ifc"])), [asset, covered]);
    assert.deepEqual(restorePlan(EMPTY_SCENE_RECORD, live), []);
});

test("records are kept per scope; an empty one removes its entry; failures are swallowed", () => {
    const s = memoryStorage();
    updateSceneRecord("project:a", (r) => ({...r, models: withModel(r.models, file)}), s);
    updateSceneRecord("user:me", (r) => ({...r, models: withModel(r.models, asset)}), s);
    assert.deepEqual(readSceneRecord("project:a", s).models, [file]);
    assert.deepEqual(readSceneRecord("user:me", s).models, [asset]);

    writeSceneRecord("project:a", EMPTY_SCENE_RECORD, s);
    assert.ok(!s.m.has(sceneRecordKey("project:a")));

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
    assert.deepEqual(readSceneRecord("x", throwing), EMPTY_SCENE_RECORD);
    writeSceneRecord("x", {v: 1, models: [file], camera: null}, throwing);
});
