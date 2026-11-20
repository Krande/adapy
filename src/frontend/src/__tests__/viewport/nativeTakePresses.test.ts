// A tool that draws through the three.js-free viewport asks for the presses it makes a drag of
// (`ViewportApi.takePresses`): it is asked first, before whoever had the scene's drags (a selection
// box's handles, the section plane's arrow, the rectangle), and a press it leaves goes on to them.
// And `requestRender` tells whoever listens: what such a tool redraws on.

import assert from "node:assert/strict";
import {test} from "node:test";
import {takePresses} from "@/viewport/native/nativeViewportApi";
import {onRenderRequest, requestRender} from "@/state/perfStore";

type Interceptor = ((e: PointerEvent) => ((m: PointerEvent) => void) | null) | null;

test("the taker is asked first; a press it leaves goes to the drags there were before", () => {
    const asked: string[] = [];
    const boxes = () => (asked.push("boxes"), null);
    const scene: {dragInterceptor: Interceptor} = {dragInterceptor: boxes};
    const mine = () => {};
    const off = takePresses(scene, (e) => (asked.push("taker"), e.button === 0 ? mine : null));
    assert.equal(scene.dragInterceptor!({button: 0} as PointerEvent), mine);
    assert.deepEqual(asked, ["taker"], "a press it takes reaches nobody else");
    asked.length = 0;
    assert.equal(scene.dragInterceptor!({button: 2} as PointerEvent), null);
    assert.deepEqual(asked, ["taker", "boxes"]);
    off();
    assert.equal(scene.dragInterceptor, boxes, "the drags are as they were");
});

test("requestRender tells its listeners until they leave", () => {
    let heard = 0;
    const off = onRenderRequest(() => void heard++);
    requestRender();
    requestRender();
    off();
    requestRender();
    assert.equal(heard, 2);
});
