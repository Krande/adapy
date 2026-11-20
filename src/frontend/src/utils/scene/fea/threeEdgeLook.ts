// The three.js viewport's element-edge colours, kept in step with the appearance settings
// (edgeLook.ts): every edge material of a kind holds the same THREE.Color, set again when the
// setting changes, so a change shows on the next frame without finding the materials.

import * as THREE from "three";

import {requestRender} from "@/state/perfStore";
import {feaEdgeLinear, onFeaEdgeLookChange} from "./edgeLook";

/** One colour per base colour (edgeColors.ts), in linear RGB as three.js draws with. */
const live = new Map<number, THREE.Color>();

onFeaEdgeLookChange(() => {
    for (const [base, color] of live) color.setRGB(...feaEdgeLinear(base));
    requestRender();
});

/** The colour edge lines drawn as `base` (an edgeColors.ts colour) share. */
function liveColor(base: number): THREE.Color {
    let color = live.get(base);
    if (!color) {
        color = new THREE.Color().setRGB(...feaEdgeLinear(base));
        live.set(base, color);
    }
    return color;
}

/** Give an element-edge line material the setting's colour for `base`, live; returns it. */
export function withFeaEdgeColor<M extends THREE.LineBasicMaterial>(material: M, base: number): M {
    material.color = liveColor(base);
    return material;
}
