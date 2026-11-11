// Overlays whose count changes and images lying in the world, on the three.js viewport: what is held
// on the GPU is reused while the new data fits, and what is replaced is disposed.
import assert from "node:assert/strict";
import {test} from "node:test";

import * as THREE from "three";
import type {LineSegments2} from "three/examples/jsm/lines/LineSegments2";

import {createThreeImageLayer} from "@/viewport/api/threeImages";
import {createThreeOverlayLayer} from "@/viewport/api/threeOverlays";

function scene() {
    const root = new THREE.Group();
    return {root, overlays: createThreeOverlayLayer(() => root, "test"), images: createThreeImageLayer(() => root, "test")};
}

/** Counts the times `geometry` (or a texture) is disposed. */
function disposals(target: THREE.EventDispatcher<{dispose: object}>): {count: number} {
    const seen = {count: 0};
    target.addEventListener("dispose", () => seen.count++);
    return seen;
}

test("points replaced with fewer keep their buffer and draw only what they were given", () => {
    const {root, overlays} = scene();
    const handle = overlays.addPoints({positions: new Float32Array([0, 0, 0, 1, 0, 0, 2, 0, 0]), color: [1, 0, 0]});
    const points = root.children[0] as THREE.Points;
    const geometry = points.geometry;
    const attribute = geometry.getAttribute("position") as THREE.BufferAttribute;
    const version = attribute.version;
    overlays.setGeometry(handle, new Float32Array([5, 5, 5, 6, 6, 6, 9, 9, 9]), 2);
    assert.equal(points.geometry, geometry, "the same geometry");
    assert.equal(geometry.getAttribute("position"), attribute, "the same buffer");
    assert.ok(attribute.version > version, "told to upload");
    assert.deepEqual(geometry.drawRange, {start: 0, count: 2});
    // What lies beyond what is drawn is on the last point drawn: the box around it all is right.
    assert.deepEqual([...(attribute.array as Float32Array)], [5, 5, 5, 6, 6, 6, 6, 6, 6]);
    overlays.setGeometry(handle, new Float32Array(0));
    assert.equal(geometry.drawRange.count, 0);
});

test("more points than the buffer holds make a new geometry and dispose of the old one", () => {
    const {root, overlays} = scene();
    const handle = overlays.addPoints({positions: new Float32Array([0, 0, 0]), colors: new Float32Array([1, 0, 0])});
    const points = root.children[0] as THREE.Points;
    const old = disposals(points.geometry);
    overlays.setGeometry(handle, new Float32Array([1, 1, 1, 2, 2, 2, 3, 3, 3]), 3, new Float32Array([1, 0, 0, 0, 1, 0, 0, 0, 1]));
    assert.equal(old.count, 1, "its buffers leave the GPU with it");
    assert.equal(points.geometry.drawRange.count, 3);
    assert.deepEqual([...(points.geometry.getAttribute("position").array as Float32Array)].slice(0, 9), [1, 1, 1, 2, 2, 2, 3, 3, 3]);
    assert.deepEqual([...(points.geometry.getAttribute("color").array as Float32Array)].slice(3, 6), [0, 1, 0]);
    // The next growth makes room for half as many again: a count that creeps up fits for a while.
    overlays.setGeometry(handle, new Float32Array(12), 4);
    const grown = points.geometry;
    assert.equal(grown.getAttribute("position").count, 5);
    overlays.setGeometry(handle, new Float32Array(15), 5);
    assert.equal(points.geometry, grown);
});

test("segments are replaced in the instance buffer, and only the count given is drawn", () => {
    const {root, overlays} = scene();
    const handle = overlays.addLines({segments: new Float32Array([0, 0, 0, 1, 0, 0, 1, 0, 0, 2, 0, 0])});
    const lines = root.children[0] as LineSegments2;
    const geometry = lines.geometry;
    overlays.setGeometry(handle, new Float32Array([0, 0, 1, 0, 0, 2]), 1);
    assert.equal(lines.geometry, geometry);
    assert.equal(geometry.instanceCount, 1);
    const held = (geometry.attributes.instanceStart as THREE.InterleavedBufferAttribute).data.array as Float32Array;
    assert.deepEqual([...held], [0, 0, 1, 0, 0, 2, 0, 0, 2, 0, 0, 2], "the rest has no length");
    const old = disposals(geometry);
    overlays.setGeometry(handle, new Float32Array(18).fill(1), 3);
    assert.equal(old.count, 1);
    assert.equal(lines.geometry.instanceCount, 3);
    overlays.remove(handle);
    assert.equal(root.children.length, 0);
});

test("a look changes the object that is there", () => {
    const {root, overlays} = scene();
    const handle = overlays.addPoints({positions: new Float32Array([0, 0, 0]), color: [1, 0, 0]});
    const points = root.children[0] as THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial>;
    overlays.setLook(handle, {visible: false, opacity: 0.5, color: [0, 1, 0], matrix: [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 4, 5, 6, 1]});
    assert.equal(root.children[0], points);
    assert.equal(points.visible, false);
    assert.equal(points.material.opacity, 0.5);
    assert.equal(points.material.transparent, true);
    assert.deepEqual(points.material.color.toArray(), [0, 1, 0]);
    assert.deepEqual(points.matrix.elements.slice(12, 15), [4, 5, 6]);
    overlays.setLook(handle, {visible: true, opacity: 1});
    assert.equal(points.material.transparent, false);
});

test("an image is a quad whose texture is the pixels: sRGB, row 0 along u at the origin", () => {
    const {root, images} = scene();
    const pixels = Uint8Array.from([255, 0, 0, 255, 0, 255, 0, 128]);
    const handle = images.add({origin: [1, 2, 0], u: [4, 0, 0], v: [0, 2, 0], width: 2, height: 1, pixels, nearest: true, opacity: 0.5});
    pixels.fill(9); // read during the call, not kept
    const mesh = root.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    assert.deepEqual([...(mesh.geometry.getAttribute("position").array as Float32Array)], [1, 2, 0, 5, 2, 0, 5, 4, 0, 1, 4, 0]);
    assert.deepEqual([...(mesh.geometry.getAttribute("uv").array as Float32Array)], [0, 0, 1, 0, 1, 1, 0, 1]);
    const texture = mesh.material.map as THREE.DataTexture;
    assert.deepEqual([...(texture.image.data as Uint8Array)], [255, 0, 0, 255, 0, 255, 0, 128]);
    assert.equal(texture.colorSpace, THREE.SRGBColorSpace);
    assert.equal(texture.magFilter, THREE.NearestFilter);
    assert.equal(texture.flipY, false);
    assert.equal(mesh.material.opacity, 0.5);
    assert.equal(mesh.material.transparent, true, "a pixel's own alpha counts");
    assert.equal(mesh.material.depthTest, true);
    assert.equal(images.unavailable(4096, 4096), null);

    // The same size: the texture that is there is written and told to upload.
    const version = texture.version;
    images.setPixels(handle, Uint8Array.from([0, 0, 255, 255, 1, 2, 3, 4]));
    assert.equal(mesh.material.map, texture);
    assert.ok(texture.version > version);
    assert.deepEqual([...(texture.image.data as Uint8Array)].slice(0, 4), [0, 0, 255, 255]);

    // Another size: another texture, and the old one disposed.
    const old = disposals(texture);
    images.setPixels(handle, new Uint8Array(2 * 2 * 4), 2, 2);
    assert.equal(old.count, 1);
    assert.equal((mesh.material.map as THREE.DataTexture).image.height, 2);

    images.setRect(handle, {origin: [0, 0, 1], u: [1, 0, 0], v: [0, 1, 0]});
    assert.deepEqual([...(mesh.geometry.getAttribute("position").array as Float32Array)].slice(0, 3), [0, 0, 1]);
    images.setLook(handle, {visible: false, opacity: 1});
    assert.equal(mesh.visible, false);
    images.remove(handle);
    assert.equal(root.children.length, 0);
});

test("blended sampling is linear without mipmaps, and an image on top is not depth-tested", () => {
    const {root, images} = scene();
    images.add({origin: [0, 0, 0], u: [1, 0, 0], v: [0, 1, 0], width: 1, height: 1, pixels: new Uint8Array(4), onTop: true});
    const mesh = root.children[0] as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
    const texture = mesh.material.map as THREE.DataTexture;
    assert.equal(texture.magFilter, THREE.LinearFilter);
    assert.equal(texture.minFilter, THREE.LinearFilter);
    assert.equal(texture.generateMipmaps, false);
    assert.equal(mesh.material.depthTest, false);
});
