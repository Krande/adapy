// Zoomed out, the element grid fades so the result colours under it stay readable.
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {fadeWithDensity} from "@/utils/scene/fea/streaming/elementEdges";

test("element edges fade as they crowd together on screen", () => {
    // Two unit-length edges.
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    const mesh = new THREE.Mesh(g);
    const edges = new Uint32Array([0, 1, 0, 2]);
    const segments = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({transparent: true}));
    fadeWithDensity(segments, mesh, edges);
    // The warp's hook replaces the object's onBeforeRender when a field is painted: the fade must survive it.
    segments.onBeforeRender = () => {};
    // 1000 CSS px high (2000 device px at DPR 2), fov 90: a unit at distance d spans 500 / d px.
    const renderer = {
        getDrawingBufferSize: (v: THREE.Vector2) => v.set(2000, 2000),
        getPixelRatio: () => 2,
    } as unknown as THREE.WebGLRenderer;
    const camera = new THREE.PerspectiveCamera(90, 1);
    const opacityAt = (d: number) => {
        camera.position.set(0, 0, d);
        (segments.material as THREE.Material).onBeforeRender(renderer, new THREE.Scene(), camera, g, segments, null as never);
        return (segments.material as THREE.LineBasicMaterial).opacity;
    };
    assert.equal(opacityAt(10), 1); // 50 px apart: full strength
    assert.ok(Math.abs(opacityAt(100) - (5 - 3) / 5) < 0.02); // 5 px apart: partly faded
    assert.equal(opacityAt(1000), 0); // half a pixel apart: gone
});

test("the fade is the same from either side of what is looked at, wherever the model's centre is", () => {
    // A long model (y from 0 to 100) with unit edges, looked at near one end.
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 100, 0], 3));
    const mesh = new THREE.Mesh(g);
    const edges = new Uint32Array([0, 1, 0, 1]);
    const segments = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({transparent: true}));
    const target = new THREE.Vector3(0, 10, 0);
    fadeWithDensity(segments, mesh, edges, () => target);
    const renderer = {
        getDrawingBufferSize: (v: THREE.Vector2) => v.set(1000, 1000),
        getPixelRatio: () => 1,
    } as unknown as THREE.WebGLRenderer;
    const camera = new THREE.PerspectiveCamera(90, 1);
    const opacityFrom = (y: number) => {
        camera.position.set(0, y, 0);
        (segments.material as THREE.Material).onBeforeRender(renderer, new THREE.Scene(), camera, g, segments, null as never);
        return (segments.material as THREE.LineBasicMaterial).opacity;
    };
    // 100 units either side of the target: one camera is 40 from the model's centre, the other 140.
    const fromPlus = opacityFrom(110);
    const fromMinus = opacityFrom(-90);
    assert.ok(fromPlus > 0 && fromPlus < 1);
    assert.equal(fromPlus, fromMinus);
});
