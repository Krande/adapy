// Coordinates the single active per-face highlight across meshes: highlighting a face on one mesh
// clears any face highlight on the previously-highlighted mesh, so only the clicked face is ever lit.
import type {CustomBatchedMesh} from "./CustomBatchedMesh";

let current: CustomBatchedMesh | null = null;

/** Highlight [start, length) (absolute index positions) on `mesh`, clearing the prior highlight. */
export function setFaceHighlight(mesh: CustomBatchedMesh, start: number, length: number): void {
    if (current && current !== mesh) current.clearFaceHighlight();
    current = mesh;
    mesh.highlightFaceRange(start, length);
}

/** Remove the active per-face highlight, if any. */
export function clearFaceHighlight(): void {
    if (current) {
        current.clearFaceHighlight();
        current = null;
    }
}
