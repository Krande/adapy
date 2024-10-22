import * as THREE from "three";
import {useModelStore} from "../../state/modelStore";

export function getSelectedMeshDrawRange(mesh: THREE.Mesh, faceIndex: number): [string, number, number] | null {
    let scene = useModelStore.getState().scene

    if (!mesh || !scene?.userData) {
        return null;
    }

    // Extract draw ranges from userData for the given mesh name
    const meshName = mesh.name; // Assuming mesh name follows the pattern "node0", "node1", etc.
    const drawRanges = scene.userData[`draw_ranges_${meshName}`] as Record<string, [number, number]>;

    if (!drawRanges) {
        return null;
    }

    // Find the draw range that includes the specified face index
    for (const [rangeId, [start, length]] of Object.entries(drawRanges)) {
        const end = start + length;
        if (faceIndex*3 >= start && faceIndex*3 < end) {
            return [rangeId, start, length];
        }
    }

    // If no range was found for the given face index
    return null;
}
