import * as THREE from "three";
import {Camera} from "three";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import CameraControls from "camera-controls";

import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useModelState} from "@/state/modelState";
import {center_on_bounding_box} from "./centerViewOnSelection";

// Frame the camera on procedural cellbuilder boxes. The regular camera helpers
// look at draw-range meshes / the fittable scene, but builder cells live in a
// tool group marked __excludeFromFit — so "Go to object" / "Fit all" would find
// nothing for a cell selection. Here we build the box straight from the cells'
// model-space origin/size and lift it into world space by the model translation
// the builder container carries (same offset that aligns cells with loaded GLBs).
export function frameCells(
    cellIds: string[] | "all",
    controls: OrbitControls | CameraControls,
    camera: Camera,
): boolean {
    const cells = useCellBuilderStore.getState().cells;
    const ids = cellIds === "all" ? Object.keys(cells) : cellIds;
    const box = new THREE.Box3();
    let any = false;
    for (const id of ids) {
        const c = cells[id];
        if (!c) continue;
        box.expandByPoint(new THREE.Vector3(c.origin[0], c.origin[1], c.origin[2]));
        box.expandByPoint(
            new THREE.Vector3(
                c.origin[0] + c.size[0],
                c.origin[1] + c.size[1],
                c.origin[2] + c.size[2],
            ),
        );
        any = true;
    }
    if (!any) return false;
    const t = useModelState.getState().translation;
    if (t) box.translate(t);
    center_on_bounding_box(box, camera, 1, controls);
    return true;
}
