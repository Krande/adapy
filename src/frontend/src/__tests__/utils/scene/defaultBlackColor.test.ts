import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import {DEFAULT_BASE_COLOR, defaultBlackColor} from "@/utils/scene/defaultBlackColor";

test("a pure-black material gets the default colour; a coloured or textured one is left alone", () => {
    const black = new THREE.MeshStandardMaterial({color: 0x000000});
    assert.equal(defaultBlackColor(black), true);
    assert.deepEqual(black.color.toArray().map((c) => c.toFixed(3)), Array(3).fill(DEFAULT_BASE_COLOR.toFixed(3)));

    const red = new THREE.MeshStandardMaterial({color: 0xff0000});
    assert.equal(defaultBlackColor(red), false);
    const textured = new THREE.MeshStandardMaterial({color: 0x000000, map: new THREE.Texture()});
    assert.equal(defaultBlackColor(textured), false);
    const byVertex = new THREE.MeshStandardMaterial({color: 0x000000, vertexColors: true});
    assert.equal(defaultBlackColor(byVertex), false);
});
