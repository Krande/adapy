// Scene objects owned by one mount of a component.
//
// The viewer's scene outlives the components that draw into it: a code reload,
// a z-up or controller flip, or a remount re-runs their setup against the same
// scene. Anything a setup adds directly must therefore be removed by its
// cleanup, or every rerun stacks another copy (lights, most visibly: the model
// gets brighter with each one). Adding through an owner makes that structural:
//   * claiming an owner first removes whatever an earlier claim of it left in
//     the scene, so a cleanup that never ran cannot leave a duplicate;
//   * `dispose` removes exactly what this claim added, so a late cleanup of an
//     older mount never takes a newer mount's objects with it.

import type * as THREE from "three";

const OWNER_KEY = "adaOwner";

export interface OwnedSceneObjects {
    /** Add `o` to the scene as this owner's; returns it. */
    add<T extends THREE.Object3D>(o: T): T;
    /** Remove everything this claim added. */
    dispose(): void;
}

/** Top-level objects in `scene` that `owner` added. */
export function ownedBy(scene: THREE.Object3D, owner: string): THREE.Object3D[] {
    return scene.children.filter((o) => o.userData[OWNER_KEY] === owner);
}

/** Start adding to `scene` as `owner`, after removing what any earlier claim of it left. */
export function claimSceneObjects(scene: THREE.Object3D, owner: string): OwnedSceneObjects {
    for (const o of ownedBy(scene, owner)) scene.remove(o);
    const mine = new Set<THREE.Object3D>();
    return {
        add(o) {
            o.userData[OWNER_KEY] = owner;
            scene.add(o);
            mine.add(o);
            return o;
        },
        dispose() {
            for (const o of mine) scene.remove(o);
            mine.clear();
        },
    };
}
