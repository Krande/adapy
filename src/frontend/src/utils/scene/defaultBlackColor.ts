import * as THREE from "three";

import {defaultColorLinear, FILE_DEFAULT_BASE_COLOR, isUncoloured} from "@/state/appearanceStore";

/** The grey a model file gives an object without a colour, as a glTF base colour (linear, light
 *  grey -- `ada.visit.gltf.materials`). What it is shown in is the appearance settings' default
 *  colour, which is this grey until the user picks another. */
export const DEFAULT_BASE_COLOR = FILE_DEFAULT_BASE_COLOR;

const DEFAULT_KEY = "adaDefaultColor";

/** Whether `material` was given the default colour: its file gave it none. Kept on the material's
 *  user data, which a clone carries. */
export function hasDefaultColor(material: THREE.Material | null | undefined): boolean {
    return material?.userData?.[DEFAULT_KEY] === true;
}

/** Give `material` the default colour when it has none of its own: untextured, not coloured by
 *  vertex, and pure black (some exporters write black for "no colour") or the grey ada writes for
 *  an object without one. True when it did. */
export function defaultBlackColor(material: THREE.Material): boolean {
    const m = material as THREE.Material & {color?: THREE.Color; map?: unknown; vertexColors?: boolean};
    if (!m.color || m.map || m.vertexColors || !isUncoloured(m.color.r, m.color.g, m.color.b)) return false;
    const [r, g, b] = defaultColorLinear();
    m.color.setRGB(r, g, b, THREE.LinearSRGBColorSpace);
    m.userData[DEFAULT_KEY] = true;
    return true;
}
