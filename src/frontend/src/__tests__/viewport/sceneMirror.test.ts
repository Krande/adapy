// A three.js scene drawn through the viewport interface (viewport/sceneMirror.ts): what the cell builder
// uses on the three.js-free viewport. Meshes become overlay meshes (one per material group), lines
// overlay lines, sprites billboards; a change of colour, opacity, visibility or place is a change of
// look, never new geometry, and what leaves the scene leaves the viewport.

import assert from "node:assert/strict";
import {test} from "node:test";
import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import type {Billboard, BillboardLayer} from "@/viewport/api/billboards";
import type {OverlayLayer, OverlayLines, OverlayLookChange, OverlayMesh} from "@/viewport/api/overlayLayer";
import {createSceneMirror, lineSegmentsOf, meshParts, spriteHeight} from "@/viewport/sceneMirror";

/** An overlay layer and a billboard layer that record what they are asked. */
function recordingTarget(viewHeight = 800, fovDeg = 50) {
    let next = 1;
    const meshes = new Map<number, OverlayMesh>();
    const lines = new Map<number, OverlayLines>();
    const looks = new Map<number, OverlayLookChange[]>();
    const billboards = new Map<number, Billboard>();
    let updates = 0;
    const overlays: OverlayLayer = {
        addLines: (l) => (lines.set(next, l), next++),
        addPoints: () => next++,
        addMesh: (m) => (meshes.set(next, m), next++),
        setPositions: () => {},
        setGeometry: () => {},
        setLook: (h, look) => void looks.set(h, [...(looks.get(h) ?? []), look]),
        remove: (h) => void (meshes.delete(h), lines.delete(h)),
        clear: () => void (meshes.clear(), lines.clear()),
        dispose: () => {},
    };
    const bb: BillboardLayer = {
        add: (b) => (billboards.set(next, b), next++),
        update: (h, b) => void (updates++, billboards.set(h, b)),
        remove: (h) => void billboards.delete(h),
        clear: () => billboards.clear(),
        onClick: () => () => {},
        dispose: () => {},
    };
    return {
        target: {overlays, billboards: bb, screen: () => ({height: viewHeight, fovDeg})},
        meshes, lines, looks, billboards,
        updates: () => updates,
        lastLook: (h: number) => Object.assign({}, ...(looks.get(h) ?? [])) as OverlayLookChange,
    };
}

const camera = new THREE.PerspectiveCamera();

test("a box with a material per face is six overlay meshes, each in its face's colour and opacity", () => {
    const scene = new THREE.Scene();
    const mats = Array.from({length: 6}, (_, i) => new THREE.MeshBasicMaterial({color: 0x3366ff, transparent: true, opacity: 0.2 + i * 0.1, depthWrite: false}));
    const box = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 3), mats);
    box.position.set(5, 0, 0);
    scene.add(box);
    const rec = recordingTarget();
    const mirror = createSceneMirror(scene, camera, rec.target);
    mirror.sync();
    assert.equal(rec.meshes.size, 6);
    const drawn = [...rec.meshes.values()];
    assert.deepEqual(drawn.map((m) => m.indices.length), [6, 6, 6, 6, 6, 6], "two triangles per face");
    assert.deepEqual(drawn.map((m) => Math.round((m.opacity ?? 1) * 10) / 10), [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]);
    const c = mats[0].color;
    assert.deepEqual(drawn[0].color, [c.r, c.g, c.b], "the material's colour, linear as three.js holds it");
    assert.equal(drawn[0].onTop, false);
    assert.equal(drawn[0].clipped, false, "builder geometry is not cut by the section planes, as on the three.js viewport");
    assert.equal(drawn[0].matrix?.[12], 5, "placed by the object's world matrix");
    assert.equal(drawn[0].fitted, false);
});

test("a restyle, a move and a hide change the look; nothing is drawn again", () => {
    const scene = new THREE.Scene();
    const mat = new THREE.MeshBasicMaterial({color: 0xffffff});
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), mat);
    scene.add(mesh);
    const rec = recordingTarget();
    const mirror = createSceneMirror(scene, camera, rec.target);
    mirror.sync();
    const handles = [...rec.meshes.keys()];
    mirror.sync();
    assert.equal(rec.looks.size, 0, "nothing changed: nothing is asked");

    mat.color.setRGB(1, 0, 0);
    mat.transparent = true;
    mat.opacity = 0.5;
    mesh.position.set(0, 0, 2);
    mirror.sync();
    assert.deepEqual([...rec.meshes.keys()], handles, "the same overlays");
    const look = rec.lastLook(handles[0]);
    assert.deepEqual(look.color, [1, 0, 0]);
    assert.equal(look.opacity, 0.5);
    assert.equal((look.matrix as ArrayLike<number>)[14], 2);

    mesh.visible = false;
    mirror.sync();
    assert.equal(rec.lastLook(handles[0]).visible, false);
    mesh.visible = true;
    mirror.sync();
    assert.equal(rec.lastLook(handles[0]).visible, true);

    scene.remove(mesh);
    mirror.sync();
    assert.equal(rec.meshes.size, 0, "what leaves the scene leaves the viewport");
});

test("a parent that is hidden hides what is under it; new geometry is drawn anew", () => {
    const scene = new THREE.Scene();
    const group = new THREE.Group();
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial());
    group.add(mesh);
    scene.add(group);
    const rec = recordingTarget();
    const mirror = createSceneMirror(scene, camera, rec.target);
    mirror.sync();
    const [first] = rec.meshes.keys();
    group.visible = false;
    mirror.sync();
    assert.equal(rec.lastLook(first).visible, false);
    group.visible = true;
    mesh.geometry = new THREE.BoxGeometry(2, 2, 2);
    mirror.sync();
    assert.equal(rec.meshes.has(first), false, "the old geometry is gone");
    assert.equal(rec.meshes.size, 1);
});

test("depthTest off draws over the model; clipping planes cut it", () => {
    const scene = new THREE.Scene();
    const onTop = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({depthTest: false}));
    const clipped = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({clippingPlanes: [new THREE.Plane()]}));
    scene.add(onTop, clipped);
    const rec = recordingTarget();
    createSceneMirror(scene, camera, rec.target).sync();
    const [a, b] = [...rec.meshes.values()];
    assert.equal(a.onTop, true);
    assert.equal(b.clipped, true);
});

test("a lit material is drawn with its light baked into its colours", () => {
    const scene = new THREE.Scene();
    scene.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial({color: 0xffffff})));
    const rec = recordingTarget();
    createSceneMirror(scene, camera, rec.target).sync();
    const [mesh] = rec.meshes.values();
    assert.ok(mesh.colors, "a colour per vertex");
    const shades = new Set(Array.from(mesh.colors!).map((v) => v.toFixed(3)));
    assert.ok(shades.size > 1, "faces turned differently are shaded differently");
    assert.ok(Math.max(...mesh.colors!) <= 1);
});

test("lines: segments as three.js joins them, dashes, vertex colours and fat-line width", () => {
    const pts = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(1, 0, 0), new THREE.Vector3(1, 1, 0)];
    const strip = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial());
    const loop = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial());
    assert.deepEqual(Array.from(lineSegmentsOf(strip)!), [0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 0]);
    assert.equal(lineSegmentsOf(loop)!.length, 18, "a loop closes");

    const scene = new THREE.Scene();
    const dashed = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)), new THREE.LineDashedMaterial({dashSize: 0.3, gapSize: 0.2, scale: 2}));
    const grid = new THREE.GridHelper(4, 4, 0xff0000, 0x00ff00);
    const fatGeo = new LineSegmentsGeometry();
    fatGeo.setPositions([0, 0, 0, 2, 0, 0]);
    const fat = new LineSegments2(fatGeo, new LineMaterial({color: 0xffaa00, linewidth: 4, depthTest: false}));
    scene.add(dashed, grid, fat);
    const rec = recordingTarget();
    createSceneMirror(scene, camera, rec.target).sync();
    const [d, g, f] = [...rec.lines.values()];
    assert.equal(d.segments.length, 12 * 6, "a box's twelve edges");
    assert.deepEqual(d.dashed, {dash: 0.15, gap: 0.1}, "the material's scale divides the lengths");
    assert.equal(d.widthPx, 1, "as WebGL draws a plain line");
    assert.ok(g.colors && g.colors.length === g.segments.length / 2, "a colour per segment");
    assert.deepEqual(Array.from(f.segments), [0, 0, 0, 2, 0, 0]);
    assert.equal(f.widthPx, 4);
    assert.equal(f.onTop, true);
});

test("a sprite is a billboard: screen-sized as three.js' sprite shader sizes it, its texture's picture keyed by version", () => {
    // A canvas stands in: the mirror hands the viewport the texture's image as it is.
    const image = {width: 64, height: 16} as unknown as HTMLCanvasElement;
    const tex = new THREE.Texture(image as never);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, sizeAttenuation: false, depthTest: false}));
    sprite.scale.set(0.2, 0.05, 1);
    sprite.position.set(1, 2, 3);
    sprite.center.set(0, 1);
    sprite.renderOrder = 6;
    const scene = new THREE.Scene();
    scene.add(sprite);
    const rec = recordingTarget(800, 50);
    const mirror = createSceneMirror(scene, camera, rec.target);
    mirror.sync();
    const [b] = rec.billboards.values();
    assert.deepEqual(b.position, [1, 2, 3]);
    assert.equal(b.sizing, "screen");
    assert.ok(Math.abs(b.height - (0.05 * 800) / (2 * Math.tan((50 * Math.PI) / 360))) < 1e-9);
    assert.deepEqual(b.anchor, [0, 1]);
    assert.equal(b.order, 9995 + 6);
    assert.equal(b.image.untagged, true, "a texture without a colour space reads as linear, as three.js reads it");
    const key = b.image.key;

    mirror.sync();
    assert.equal(rec.updates(), 0, "unchanged: not drawn again");
    tex.needsUpdate = true; // a relabelled canvas
    mirror.sync();
    assert.equal(rec.updates(), 1);
    assert.notEqual([...rec.billboards.values()][0].image.key, key);

    sprite.visible = false;
    mirror.sync();
    assert.equal(rec.billboards.size, 0, "a hidden sprite is taken off");
});

test("spriteHeight: world units with size attenuation, pixels without", () => {
    assert.equal(spriteHeight(2, true, 800, 50), 2);
    assert.ok(Math.abs(spriteHeight(1, false, 600, 90) - 300) < 1e-9, "scale 1 at 90 degrees is half the view");
});

test("meshParts: a geometry without groups is one part of all its triangles", () => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
    const parts = meshParts(g, [{}]);
    assert.equal(parts.length, 1);
    assert.deepEqual(Array.from(parts[0].indices), [0, 1, 2]);
});

test("an object's onBeforeRender runs before it is mirrored, as three.js runs it before drawing", () => {
    const scene = new THREE.Scene();
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(1, 0, 0)]), new THREE.LineBasicMaterial());
    let calls = 0;
    line.onBeforeRender = () => void calls++;
    scene.add(line);
    const rec = recordingTarget();
    const mirror = createSceneMirror(scene, camera, rec.target);
    mirror.sync();
    mirror.sync();
    assert.equal(calls, 2);
});
