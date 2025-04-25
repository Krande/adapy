import { CustomBatchedMesh } from "./CustomBatchedMesh"; // Adjust the import path
import { useModelStore } from "../../state/modelStore";

export function getSelectedMeshDrawRange(
    mesh: CustomBatchedMesh,
    faceIndex: number
): [string, number, number] | null {
    const userdata = useModelStore.getState().userdata;

    if (!mesh || !userdata) {
        return null;
    }

    // Extract draw ranges from userData for the given mesh name
    const meshName = mesh.name; // Assuming mesh name follows the pattern "node0", "node1", etc.
    const drawRanges = userdata[`draw_ranges_${meshName}`] as Record<
        string,
        [number, number]
    >;

    if (!drawRanges) {
        return null;
    }

    // Find the draw range that includes the specified face index
    for (const [rangeId, [start, length]] of Object.entries(drawRanges)) {
        const end = start + length;
        if (faceIndex * 3 >= start && faceIndex * 3 < end) {
            return [rangeId, start, length];
        }
    }

    // If no range was found for the given face index
    return null;
}
