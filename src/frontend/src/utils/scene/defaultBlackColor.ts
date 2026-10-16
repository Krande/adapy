import * as THREE from "three";

/** The default colour as a glTF base colour (linear, light grey -- `ada.visit.gltf.materials`):
 *  what a GLB's untextured, pure-black material is shown in; some exporters write black for
 *  "no colour". */
export const DEFAULT_BASE_COLOR = 204 / 255;

/** Give `material` the default colour when it is pure black, untextured and not coloured by vertex. */
export function defaultBlackColor(material: THREE.Material): boolean {
    const m = material as THREE.Material & {color?: THREE.Color; map?: unknown; vertexColors?: boolean};
    if (!m.color || m.map || m.vertexColors || m.color.r !== 0 || m.color.g !== 0 || m.color.b !== 0) return false;
    m.color.setRGB(DEFAULT_BASE_COLOR, DEFAULT_BASE_COLOR, DEFAULT_BASE_COLOR, THREE.LinearSRGBColorSpace);
    return true;
}
