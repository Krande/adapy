// A model handed to the three.js-free viewport, and what is read from a GLB before it is loaded.
import assert from "node:assert/strict";
import {test} from "node:test";

import {glbNamesAnimations} from "@/utils/glb/glbBufferView";
import {showInNativeViewport, useNativeViewport} from "@/viewport/native/nativeModel";

/** A GLB with `json` as its JSON chunk and no binary chunk. */
function glb(json: object): Uint8Array {
    let text = JSON.stringify(json);
    text += " ".repeat((4 - (text.length % 4)) % 4);
    const bytes = new Uint8Array(20 + text.length);
    const view = new DataView(bytes.buffer);
    view.setUint32(0, 0x46546c67, true);
    view.setUint32(4, 2, true);
    view.setUint32(8, bytes.length, true);
    view.setUint32(12, text.length, true);
    view.setUint32(16, 0x4e4f534a, true);
    bytes.set(new TextEncoder().encode(text), 20);
    return bytes;
}

test("a GLB says whether it has animations without being parsed", () => {
    assert.equal(glbNamesAnimations(glb({asset: {version: "2.0"}, animations: [{name: "slide", channels: [], samplers: []}]})), true);
    assert.equal(glbNamesAnimations(glb({asset: {version: "2.0"}, nodes: [{name: "a"}]})), false);
    assert.equal(glbNamesAnimations(glb({asset: {version: "2.0"}, animations: []})), false, "an empty list is none");
    assert.equal(glbNamesAnimations(new TextEncoder().encode('{"animations":[{}]}')), false, "not a GLB");
    assert.equal(glbNamesAnimations(new Uint8Array(4)), false);
});

test("the name a model is shown by goes with it to the viewport, beside the name it is known by", async () => {
    const realFetch = globalThis.fetch;
    globalThis.fetch = (async () => new Response(new Uint8Array([1, 2, 3]))) as typeof fetch;
    try {
        await showInNativeViewport("blob:model", undefined, "runs/42/model.glb", "replace", "Run 42");
        const shown = useNativeViewport.getState().model!;
        assert.deepEqual([shown.sourceName, shown.displayName, shown.placement, [...shown.bytes]], ["runs/42/model.glb", "Run 42", "replace", [1, 2, 3]]);
        await showInNativeViewport("blob:model", undefined, "other.glb");
        assert.equal(useNativeViewport.getState().model!.displayName, undefined);
    } finally {
        globalThis.fetch = realFetch;
        useNativeViewport.setState({model: null});
    }
});
