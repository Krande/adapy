/**
 * The VIEWPORT the builder is drawn in, as the builder's modules see it.
 *
 * The builder keeps its cells, gizmos, handles and markers as three.js objects and picks them with a
 * three.js raycaster, whichever viewport is on screen: that is its one scene logic. What differs per
 * viewport is only where those objects are drawn, which camera they are picked and sized with, which
 * element the pointer arrives on and how the camera is held during a drag the builder makes. This
 * interface is that difference:
 *  - on the three.js viewport (`threeViewHost`, here) the objects live in the viewport's own scene
 *    and are drawn by its renderer;
 *  - on the three.js-free viewport (./nativeViewHost.ts) they live in a scene of the builder's own,
 *    which that viewport draws through its overlays and billboards (viewport/sceneMirror.ts), with a
 *    camera kept in step with the viewport's.
 */

import * as THREE from "three";
import {useGroundGridStore} from "@/state/groundGridStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {viewportInputElement} from "@/viewport/input";
import type {CellBuilderScene} from "./sceneContext";

export interface BuilderViewHost {
    readonly kind: "three" | "native";
    /** Where the builder's objects are put. */
    readonly scene: THREE.Scene;
    /** The camera the view is drawn with now: what the builder picks, projects and sizes with. */
    camera(): THREE.Camera;
    /** The element the view's pointer events arrive on (its cursor, its pointer capture). */
    input(): HTMLElement;
    /** The view's rectangle, in client pixels. */
    rect(): DOMRect;
    /** Let the camera follow the pointer again, or hold it still for a drag the builder makes. */
    setCameraEnabled(enabled: boolean): void;
    /** The loaded object called `name` (the CAD a compile spliced in for an equipment cell, named
     *  for the cell): its box in the world and points of its surface in the world, at most `max` of
     *  them. Null when no such object is on screen. */
    loadedGeometry(name: string): LoadedGeometry | null;
    /** Leave the scene's ground grid out while the builder draws its own grid, or draw it again. */
    setGroundGridHidden(hidden: boolean): void;
    /** What this viewport needs of the builder once its scene stands: returns the undo. */
    bind?(ctx: CellBuilderScene): () => void;
}

export interface LoadedGeometry {
    box: THREE.Box3;
    points(max: number): THREE.Vector3[];
}

/** A mesh's vertices in the world, every so many when there are more than `max`. */
export function meshPoints(mesh: THREE.Mesh, max: number): THREE.Vector3[] {
    const position = (mesh.geometry as THREE.BufferGeometry).getAttribute("position");
    if (!position) return [];
    const stride = Math.max(1, Math.floor(position.count / max));
    const out: THREE.Vector3[] = [];
    for (let i = 0; i < position.count; i += stride) out.push(new THREE.Vector3().fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld));
    return out;
}

/** The three.js viewport: the builder's objects in its scene, drawn by its renderer. */
export function threeViewHost(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera): BuilderViewHost {
    // The scene's static 1 m helper grid(s), hidden while the builder's own grid shows.
    const hiddenGrids: THREE.GridHelper[] = [];
    return {
        kind: "three",
        scene,
        camera: () => getViewerRuntime().camera.current ?? camera,
        input: () => viewportInputElement(renderer),
        rect: () => renderer.domElement.getBoundingClientRect(),
        setCameraEnabled: (enabled) => {
            const controls = getViewerRuntime().controls.current;
            if (controls) controls.enabled = enabled;
        },
        loadedGeometry: (name) => {
            let mesh: THREE.Mesh | null = null;
            (getViewerRuntime().scene.current ?? scene).traverse((o) => {
                if (!mesh && (o as THREE.Mesh).isMesh && o.name && o.name === name) mesh = o as THREE.Mesh;
            });
            if (!mesh) return null;
            const found: THREE.Mesh = mesh;
            // Refresh the world matrix chain so a just-loaded mesh reports correct
            // bounds (expandByObject only refreshes the object's own matrix).
            found.updateWorldMatrix(true, false);
            return {box: new THREE.Box3().setFromObject(found, true), points: (max) => meshPoints(found, max)};
        },
        setGroundGridHidden: (hidden) => {
            if (hidden && hiddenGrids.length === 0) {
                for (const o of scene.children) {
                    if (o instanceof THREE.GridHelper && o.visible && !o.userData.__builderGrid) {
                        o.visible = false;
                        hiddenGrids.push(o);
                    }
                }
            } else if (!hidden && hiddenGrids.length > 0) {
                // Back as the "Ground grid" toggle has it (it may have been turned off meanwhile).
                hiddenGrids.forEach((g) => (g.visible = useGroundGridStore.getState().visible));
                hiddenGrids.length = 0;
            }
        },
    };
}

/** The camera the builder picks and sizes with: the viewport's, as it draws now. */
export const builderCamera = (ctx: CellBuilderScene): THREE.Camera => ctx.host.camera();
