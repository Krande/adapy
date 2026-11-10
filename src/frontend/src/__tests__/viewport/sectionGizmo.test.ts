// The section plane's handle on the three.js-free viewport: where it is drawn, and what a drag along it means.
import {test} from "node:test";
import assert from "node:assert/strict";

import {dragAlongArrow, gizmoGeometry} from "@/viewport/native/sectionGizmo";

const BOX = [0, 0, 0, 4, 2, 1];
const close = (a: number, b: number) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);

test("the arrow starts on the plane, at the model's centre moved onto it, and points along the normal", () => {
    // The plane x = 1: normal (1, 0, 0), constant -1.
    const g = gizmoGeometry({normal: [1, 0, 0], constant: -1}, BOX);
    assert.deepEqual(g.base.map((v) => +v.toFixed(6)), [1, 1, 0.5]);
    close(g.tip[0] - g.base[0], g.length);
    close(g.tip[1], g.base[1]);
    close(g.tip[2], g.base[2]);
    assert.ok(g.length > 0);
});

test("the outline lies in the plane and encloses the model's box", () => {
    const g = gizmoGeometry({normal: [0, 0, 1], constant: -0.25}, BOX);
    assert.equal(g.outline.length, 4 * 2 * 3, "four segments");
    for (let i = 0; i < g.outline.length; i += 3) close(g.outline[i + 2], 0.25);
    const xs = [...g.outline].filter((_, i) => i % 3 === 0);
    assert.ok(Math.min(...xs) < 0 && Math.max(...xs) > 4, "wider than the box");
});

test("a drag moves the plane by its share along the arrow on screen", () => {
    const base = {x: 100, y: 100}, tip = {x: 200, y: 100}; // the arrow is 100 px long, and 2 long in the world
    close(dragAlongArrow(50, 0, base, tip, 2), 1);
    close(dragAlongArrow(-25, 0, base, tip, 2), -0.5);
    close(dragAlongArrow(0, 80, base, tip, 2), 0);
    close(dragAlongArrow(10, 10, base, base, 2), 0); // seen end-on: nothing to drag along
});
