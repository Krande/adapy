// The colour the FEA model is drawn in where nothing paints it: a plugin (a UI shell's mode) can
// give the plain model a colour of its own while no result or property colours it, and leave every
// painted state alone.
//
// It is the model's own base material, not an overlay, so whatever paints the model (a result, a
// property, a plugin's colour field) still draws over it exactly as before. Applied by the render
// loop each frame, beside the deformation gate: a paint installs per-vertex colours and the base
// colour stops showing; when the paint goes, the base colour is the one asked for. The model's own
// colour is kept and given back exactly once no owner asks any more.

import * as THREE from "three";

import type {SceneColor} from "@/plugins/registry";
import {requestRender} from "@/state/perfStore";
import {feaSession} from "./streaming/session";

/** What each owner asked for; the latest ask wins while several hold one. */
const asked = new Map<string, THREE.Color>();
/** Where a coloured material keeps its own colour, to be given back exactly. */
const OWN = "__ownUnpaintedColor";

type BaseMaterial = THREE.Material & {color?: THREE.Color; vertexColors?: boolean};

/** `color` for the unpainted FEA model until this owner asks again or passes null (or is released). */
export function setFeaUnpaintedColor(owner: string, color: SceneColor | null): void {
    asked.delete(owner);
    if (color != null) {
        const c = typeof color === "string" ? new THREE.Color(color) : new THREE.Color().setRGB(color[0], color[1], color[2], THREE.SRGBColorSpace);
        asked.set(owner, c);
    }
    requestRender();
}

/** Drop whatever `owner` asked for (its plugin's overlays are being removed). */
export function releaseFeaUnpaintedColor(owner: string): void {
    if (asked.delete(owner)) requestRender();
}

/** The material drawing the unpainted model: slot 0 on a CustomBatchedMesh, whose other slots are
 *  the selection overlay and the hidden-range material. */
function baseMaterial(mesh: THREE.Mesh): BaseMaterial | null {
    const m = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as BaseMaterial | undefined;
    return m?.color && typeof m.color.setRGB === "function" ? m : null;
}

/** Colour one surface's base material, or give it its own colour back. Whether it changed. */
export function applyUnpaintedColor(mesh: THREE.Mesh, color: THREE.Color | null): boolean {
    const mat = baseMaterial(mesh);
    if (!mat?.color) return false;
    const own = mat.userData[OWN] as [number, number, number] | undefined;
    if (color && !mat.vertexColors) {
        if (!own) mat.userData[OWN] = [mat.color.r, mat.color.g, mat.color.b];
        if (mat.color.equals(color)) return false;
        mat.color.copy(color);
        return true;
    }
    if (!own) return false;
    mat.color.setRGB(own[0], own[1], own[2]);
    delete mat.userData[OWN];
    return true;
}

/** Called by the render loop each frame, before drawing. */
export function applyFeaUnpaintedColor(): void {
    const active = feaSession.active;
    if (!active?.mesh) return;
    const color = asked.size ? [...asked.values()].at(-1)! : null;
    for (const mesh of [active.mesh, active.beamSolidMesh]) {
        if (mesh) applyUnpaintedColor(mesh as THREE.Mesh, color);
    }
}

/** Tests only. */
export function _resetFeaUnpaintedColor(): void {
    asked.clear();
}
