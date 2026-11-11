// The unpainted colour (./unpaintedColor.ts) on the three.js viewport: the base material of the
// streaming loader's result mesh and of its beam sections. Applied by the render loop each frame,
// beside the deformation gate.

import type * as THREE from "three";

import {feaSession} from "./streaming/session";
import {feaUnpaintedColor, type LinearRgb} from "./unpaintedColor";

/** Where a coloured material keeps its own colour, to be given back exactly. */
const OWN = "__ownUnpaintedColor";

type BaseMaterial = THREE.Material & {color?: THREE.Color; vertexColors?: boolean};

/** The material drawing the unpainted model: slot 0 on a CustomBatchedMesh, whose other slots are
 *  the selection overlay and the hidden-range material. */
function baseMaterial(mesh: THREE.Mesh): BaseMaterial | null {
    const m = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as BaseMaterial | undefined;
    return m?.color && typeof m.color.setRGB === "function" ? m : null;
}

/** Colour one surface's base material, or give it its own colour back. Whether it changed. */
export function applyUnpaintedColor(mesh: THREE.Mesh, color: LinearRgb | null): boolean {
    const mat = baseMaterial(mesh);
    if (!mat?.color) return false;
    const own = mat.userData[OWN] as [number, number, number] | undefined;
    if (color && !mat.vertexColors) {
        if (!own) mat.userData[OWN] = [mat.color.r, mat.color.g, mat.color.b];
        if (mat.color.r === color[0] && mat.color.g === color[1] && mat.color.b === color[2]) return false;
        mat.color.setRGB(color[0], color[1], color[2]);
        return true;
    }
    if (!own) return false;
    mat.color.setRGB(own[0], own[1], own[2]);
    delete mat.userData[OWN];
    return true;
}

/** Called by the three.js render loop each frame, before drawing. */
export function applyFeaUnpaintedColor(): void {
    const active = feaSession.active;
    if (!active?.mesh) return;
    const color = feaUnpaintedColor();
    applyUnpaintedColor(active.mesh, color);
    if (active.beamSolidMesh) applyUnpaintedColor(active.beamSolidMesh, color);
}
