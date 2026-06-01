// Stencil-buffer capping for section planes — adapted from the three.js
// webgl_clipping_stencil example. For each clipping plane we (a) write the
// stencil where the solid is cut (back faces increment, front faces decrement,
// colour-less) and (b) draw a cap quad that fills only where the stencil marks
// the interior, coloured like the geometry — so the cut reads as solid, not
// hollow.
//
// All produced meshes go on layer 1 so they are excluded from the GPU picker
// (which only renders CustomBatchedMesh) and from raycast (layer 0 only), while
// the camera still renders them.
import * as THREE from "three";

const HELPER_LAYER = 1;

/** Two stencil-writing meshes sharing ``geometry``, clipped by ``plane``. They
 *  write nothing to colour; they mark the stencil buffer where the solid is
 *  sectioned so the cap can fill it. */
export function createPlaneStencilGroup(
    geometry: THREE.BufferGeometry,
    plane: THREE.Plane,
    renderOrder: number,
): THREE.Group {
    const group = new THREE.Group();
    const base = new THREE.MeshBasicMaterial();
    base.depthWrite = false;
    base.depthTest = false;
    base.colorWrite = false;
    base.stencilWrite = true;
    base.stencilFunc = THREE.AlwaysStencilFunc;
    base.clippingPlanes = [plane];

    const backMat = base.clone();
    backMat.side = THREE.BackSide;
    backMat.stencilFail = THREE.IncrementWrapStencilOp;
    backMat.stencilZFail = THREE.IncrementWrapStencilOp;
    backMat.stencilZPass = THREE.IncrementWrapStencilOp;
    const back = new THREE.Mesh(geometry, backMat);
    back.renderOrder = renderOrder;
    back.layers.set(HELPER_LAYER);
    group.add(back);

    const frontMat = base.clone();
    frontMat.side = THREE.FrontSide;
    frontMat.stencilFail = THREE.DecrementWrapStencilOp;
    frontMat.stencilZFail = THREE.DecrementWrapStencilOp;
    frontMat.stencilZPass = THREE.DecrementWrapStencilOp;
    const front = new THREE.Mesh(geometry, frontMat);
    front.renderOrder = renderOrder;
    front.layers.set(HELPER_LAYER);
    group.add(front);

    group.layers.set(HELPER_LAYER);
    return group;
}

/** Orient a cap quad (PlaneGeometry lies in XY, faces +Z) onto ``plane``. */
export function orientCapToPlane(cap: THREE.Object3D, plane: THREE.Plane): void {
    const p = plane.coplanarPoint(new THREE.Vector3());
    cap.position.copy(p);
    cap.lookAt(p.clone().add(plane.normal));
}

/** The filled cross-section quad for ``plane``. Renders only where the stencil
 *  is non-zero (interior of the cut), clipped by ``otherPlanes`` so multiple
 *  cuts intersect cleanly. Clears the stencil after itself so the next plane's
 *  groups start clean. */
export function createCapMesh(
    plane: THREE.Plane,
    size: number,
    color: THREE.ColorRepresentation,
    otherPlanes: THREE.Plane[],
    renderOrder: number,
): THREE.Mesh {
    const mat = new THREE.MeshStandardMaterial({
        color,
        metalness: 0.1,
        roughness: 0.75,
        side: THREE.DoubleSide,
        clippingPlanes: otherPlanes,
        stencilWrite: true,
        stencilRef: 0,
        stencilFunc: THREE.NotEqualStencilFunc,
        stencilFail: THREE.ReplaceStencilOp,
        stencilZFail: THREE.ReplaceStencilOp,
        stencilZPass: THREE.ReplaceStencilOp,
    });
    const cap = new THREE.Mesh(new THREE.PlaneGeometry(size, size), mat);
    cap.renderOrder = renderOrder;
    cap.layers.set(HELPER_LAYER);
    orientCapToPlane(cap, plane);
    cap.onAfterRender = (renderer) => renderer.clearStencil();
    return cap;
}
