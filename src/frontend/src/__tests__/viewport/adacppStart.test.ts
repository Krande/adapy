// Starting the renderer module must end one way or the other: a failed wasm instantiation never settles
// the Emscripten factory's promise, and a viewport left "starting" has neither a picture nor a reason.
import {test} from "node:test";
import assert from "node:assert/strict";

import {startRendererModule} from "@/viewport/adacpp/adacppViewport";
import type {AdacppModule} from "@/viewport/adacpp/types";

const never = () => new Promise<AdacppModule>(() => {});

test("a failed instantiation rejects with its reason instead of hanging", async () => {
    const failed = Promise.reject<never>(new Error("instantiating the inlined renderer failed: bad magic"));
    await assert.rejects(startRendererModule(never, {}, failed), /bad magic/);
});

test("a module that starts is returned, with or without an instantiation to watch", async () => {
    const module = {} as AdacppModule;
    const pending = new Promise<never>(() => {});
    assert.equal(await startRendererModule(async () => module, {}, pending), module);
    assert.equal(await startRendererModule(async () => module, {}, null), module);
});
