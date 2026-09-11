import * as THREE from "three";

// The section planes' clipping, for overlays built after the planes were.
//
// Section planes clip per MATERIAL. SectionPlanesController walks the scene when a
// plane is added, toggled, committed or removed, and hands every model material --
// and every object tagged ``userData.__clipWithModel`` -- one array of live
// THREE.Plane instances. That walk was the only place clipping was applied, so an
// overlay built after it rendered unclipped until the next plane edit. Overlays
// rebuild on their own schedule: colouring by an element field (a beam's section or
// material) reinstalls the coloured beam lines on every repaint, and those beams
// then showed straight through the cut.
//
// ``clipWithModel`` covers both halves: the tag, so the next walk keeps the overlay
// in step, and the planes active NOW. The array is the controller's own and a gizmo
// drag moves its planes in place, so an overlay seeded here follows a drag exactly
// as the model does.

let activePlanes: THREE.Plane[] | null = null;

/** The controller reports every re-application here. Empty or null = no planes. */
export function setSectionClippingPlanes(planes: THREE.Plane[] | null): void {
    activePlanes = planes && planes.length > 0 ? planes : null;
}

/** The live section planes, or null when none is enabled. */
export function sectionClippingPlanes(): THREE.Plane[] | null {
    return activePlanes;
}

/** Hand `planes` to a material (or material array); `shadows` clips its shadow too. */
export function applyClippingPlanes(
    material: unknown,
    planes: THREE.Plane[] | null,
    shadows = false,
): void {
    const mats = Array.isArray(material) ? material : [material];
    for (const m of mats) {
        if (!m) continue;
        (m as THREE.Material).clippingPlanes = planes;
        if (shadows) (m as THREE.Material).clipShadows = true;
        (m as THREE.Material).needsUpdate = true;
    }
}

/**
 * Make `root`, and everything under it, clip with the model under section planes.
 *
 * Call it where the overlay is built, every time it is built. Without planes it
 * only tags: a material left alone costs no shader recompile, which is why the
 * controller never touches materials before a plane exists.
 */
export function clipWithModel(root: THREE.Object3D): void {
    root.traverse((o) => {
        o.userData.__clipWithModel = true;
        if (activePlanes) {
            applyClippingPlanes((o as unknown as {material?: unknown}).material, activePlanes);
        }
    });
}

/** Is `point` (world space) cut away? three.js discards whatever lies on the
 *  negative side of any one plane -- the union mode the section planes use. */
export function isClippedAway(
    point: THREE.Vector3,
    planes: readonly THREE.Plane[] | null | undefined,
): boolean {
    return !!planes && planes.some((plane) => plane.distanceToPoint(point) < 0);
}
