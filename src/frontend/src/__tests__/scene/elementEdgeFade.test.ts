// Zoomed out, the element grid fades so the result colours under it stay readable.
import {test} from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";

import {EDGE_FADE_FULL_PX_DEFAULT, migratePerfState, usePerfStore} from "@/state/perfStore";
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
    assert.equal(opacityAt(100), 1); // 5 px apart: the default's full strength
    assert.ok(Math.abs(opacityAt(200) - (2.5 - 1.875) / (5 - 1.875)) < 0.02); // 2.5 px apart: partly faded
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
    // 200 units either side of the target: one camera is 140 from the model's centre, the other 240.
    const fromPlus = opacityFrom(210);
    const fromMinus = opacityFrom(-190);
    assert.ok(fromPlus > 0 && fromPlus < 1);
    assert.equal(fromPlus, fromMinus);
});

test("the fade can be turned off, and where it starts adjusted", () => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    const mesh = new THREE.Mesh(g);
    const segments = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({transparent: true}));
    fadeWithDensity(segments, mesh, new Uint32Array([0, 1, 0, 2]), () => null);
    const renderer = {
        getDrawingBufferSize: (v: THREE.Vector2) => v.set(1000, 1000),
        getPixelRatio: () => 1,
    } as unknown as THREE.WebGLRenderer;
    const camera = new THREE.PerspectiveCamera(90, 1);
    camera.position.set(0, 0, 200); // unit edges 2.5 px apart
    const opacity = () => {
        (segments.material as THREE.Material).onBeforeRender(renderer, new THREE.Scene(), camera, g, segments, null as never);
        return (segments.material as THREE.LineBasicMaterial).opacity;
    };
    try {
        assert.ok(opacity() > 0 && opacity() < 1);
        usePerfStore.getState().setEdgeFadeFullPx(2); // full strength from 2 px: 2.5 px apart is full
        assert.equal(opacity(), 1);
        usePerfStore.getState().setEdgeFadeFullPx(40); // gone below 15 px
        assert.equal(opacity(), 0);
        usePerfStore.getState().setEdgeFade(false);
        assert.equal(opacity(), 1);
    } finally {
        usePerfStore.getState().setEdgeFade(true);
        usePerfStore.getState().setEdgeFadeFullPx(EDGE_FADE_FULL_PX_DEFAULT);
    }
});

test("the default is 5 px, and a stored old default of 8 moves with it", () => {
    assert.equal(EDGE_FADE_FULL_PX_DEFAULT, 5);
    assert.equal(migratePerfState({edgeFadeFullPx: 8}, 0).edgeFadeFullPx, 5);
    assert.equal(migratePerfState({edgeFadeFullPx: 12}, 0).edgeFadeFullPx, 12);
    assert.equal(migratePerfState({edgeFadeFullPx: 8}, 1).edgeFadeFullPx, 8, "a choice made after the change stays");
});
