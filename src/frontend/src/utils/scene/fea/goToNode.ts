// "Go to node" — wire the FEA data table's eye-icon click to a
// visual highlight + camera frame on the corresponding vertex in
// the 3D scene. Decoupled from the existing element-selection
// pipeline because nodes aren't pickable today (CustomBatchedMesh
// + AFEM are element-granularity); this lives alongside selection
// rather than co-opting it.
//
// Lifecycle:
//   * ``goToNode(nodeId)`` adds a small marker sphere to the scene
//     at the node's *deformed* world position (morph delta applied)
//     and smoothly frames the camera on it.
//   * ``clearGoToNode()`` removes the marker. Called when the user
//     clicks the eye icon again on the same row, or when the FEA
//     session is torn down (scene replace / picker exit).

import * as THREE from "three";
import CameraControls from "camera-controls";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {cameraRef, controlsRef, sceneRef} from "@/state/refs";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useModelState} from "@/state/modelState";

// Persistent ref-style holder. Kept in module state rather than a
// real React ref because the marker is a side-effect of imperative
// scene mutation, not a component lifecycle.
let currentMarker: THREE.Mesh | null = null;

function disposeMarker(): void {
    if (!currentMarker) return;
    const scene = sceneRef.current;
    if (scene) scene.remove(currentMarker);
    currentMarker.geometry.dispose();
    const mat = currentMarker.material as THREE.Material | THREE.Material[];
    if (Array.isArray(mat)) {
        mat.forEach((m) => m.dispose());
    } else {
        mat.dispose();
    }
    currentMarker = null;
}

export function clearGoToNode(): void {
    disposeMarker();
}

/** Lookup a vertex's deformed world position. Walks the same
 *  morph delta + influence math as applyFieldToMesh / Three's
 *  built-in morph shader, then transforms through the mesh's
 *  matrixWorld. Returns null when the mesh has no morph state yet
 *  (initial load before applyFieldToMesh runs) — caller should
 *  pretend the click never happened in that case. */
function vertexWorldPosition(
    mesh: THREE.Mesh,
    vertexIndex: number,
): THREE.Vector3 | null {
    const geometry = mesh.geometry;
    const posAttr = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!posAttr || vertexIndex < 0 || vertexIndex >= posAttr.count) return null;

    const v = new THREE.Vector3();
    v.fromBufferAttribute(posAttr, vertexIndex);

    // Apply morph delta: position = base + Σ influence_i * morph_i
    // (when morphTargetsRelative === true, which the FEA bake sets).
    const morphPositions = (geometry.morphAttributes && (geometry.morphAttributes as any).position) as
        | THREE.BufferAttribute[]
        | undefined;
    const morphTargetsRelative = geometry.morphTargetsRelative === true;
    const influences: number[] | undefined = (mesh as any).morphTargetInfluences;
    if (morphPositions && influences) {
        let sumInfluence = 0;
        for (let m = 0; m < morphPositions.length; m++) {
            const inf = influences[m] || 0;
            if (inf === 0) continue;
            sumInfluence += inf;
            const mp = morphPositions[m];
            const mx = mp.getX(vertexIndex);
            const my = mp.getY(vertexIndex);
            const mz = mp.getZ(vertexIndex);
            if (morphTargetsRelative) {
                v.x += mx * inf;
                v.y += my * inf;
                v.z += mz * inf;
            } else {
                v.x = v.x * (1 - sumInfluence) + mx * inf;
                v.y = v.y * (1 - sumInfluence) + my * inf;
                v.z = v.z * (1 - sumInfluence) + mz * inf;
            }
        }
    }

    mesh.localToWorld(v);
    if (isNaN(v.x) || isNaN(v.y) || isNaN(v.z)) return null;
    return v;
}

function makeMarker(worldPos: THREE.Vector3, radius: number): THREE.Mesh {
    // Sphere instead of THREE.Points so the marker scales with the
    // scene (Points are pixel-sized which makes them too small on
    // a framed view of a 100 km ship). Bright magenta — high
    // contrast against both viridis and abaqus colormaps.
    const geom = new THREE.SphereGeometry(radius, 16, 12);
    const mat = new THREE.MeshBasicMaterial({
        color: 0xff00ff,
        transparent: true,
        opacity: 0.85,
        depthTest: false, // always-visible — the user just asked "where is this?"
    });
    const mesh = new THREE.Mesh(geom, mat);
    mesh.renderOrder = 9999; // above mesh + edges in the z order
    mesh.position.copy(worldPos);
    mesh.name = "fea-goto-marker";
    return mesh;
}

function frameCamera(worldPos: THREE.Vector3, radius: number): void {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;

    if (controls instanceof CameraControls) {
        const sphere = new THREE.Sphere(worldPos, radius * 8);
        void controls.fitToSphere(sphere, true);
    } else if (controls instanceof OrbitControls) {
        const {zIsUp} = useModelState.getState();
        const dir = new THREE.Vector3();
        camera.getWorldDirection(dir).normalize();
        const distance = radius * 16;
        const newPos = worldPos.clone().add(dir.multiplyScalar(-distance));
        camera.position.copy(newPos);
        camera.up.set(0, zIsUp ? 0 : 1, zIsUp ? 1 : 0);
        camera.lookAt(worldPos);
        camera.updateProjectionMatrix?.();
        controls.target.copy(worldPos);
        controls.update();
    }
}

/** Look up the first vertex (node) of an FEA element by its AFEM
 *  draw-range label. ``E12345`` style — same name shown in the
 *  selected-object info panel after a 3D pick. Returns the 1-based
 *  node id ready for the data-table virtualizer, or null when the
 *  active mesh isn't an FEA mesh / the label isn't in its draw
 *  ranges / the element is a face-less line element.
 *
 *  Drives the reverse-direction nav (info panel → data table): the
 *  user clicks an element in the scene, hits "Show in data", and
 *  the table scrolls to the element's first node. First-node
 *  semantics are arbitrary but stable — same row every time, and
 *  matches what a user inspecting "what does this element look
 *  like" would reach for. */
export function elementFirstNodeId(elementLabel: string): number | null {
    const mesh = useFeaAnimationStore.getState().mesh;
    if (!mesh) return null;
    const drawRanges = (mesh as any).drawRanges as
        | Map<string, [number, number]>
        | undefined;
    if (!drawRanges) return null;
    const range = drawRanges.get(elementLabel);
    if (!range) return null;
    const [start, count] = range;
    if (count === 0) return null;
    const indexAttr = mesh.geometry.getIndex();
    if (!indexAttr) return null;
    const arr = indexAttr.array as Uint16Array | Uint32Array;
    if (start < 0 || start >= arr.length) return null;
    return arr[start] + 1; // 1-based node id
}

/** True when ``label`` resolves to a vertex on the active FEA mesh —
 *  used to gate the "Show in data" button so it doesn't show up on
 *  picks of unrelated CAD geometry. */
export function isFeaElementLabel(label: string): boolean {
    return elementFirstNodeId(label) !== null;
}

/** Highlight the node + frame the camera on it. ``nodeId`` is the
 *  1-based label the table renders; the internal vertex index is
 *  ``nodeId - 1``. No-op when the FEA session isn't active or the
 *  mesh doesn't carry the vertex. */
export function goToNode(nodeId: number): void {
    const mesh = useFeaAnimationStore.getState().mesh;
    const scene = sceneRef.current;
    if (!mesh || !scene) return;

    const worldPos = vertexWorldPosition(mesh, nodeId - 1);
    if (!worldPos) return;

    // Marker radius — size it to the model's scene. Compute once
    // per marker swap from the mesh's bounding sphere so a small
    // cantilever and a 100 m ship both end up with a marker the
    // user can actually see. Falls back to a hardcoded 0.05 if the
    // bounding sphere hasn't been computed yet.
    let radius = 0.05;
    const geom = mesh.geometry;
    if (!geom.boundingSphere) geom.computeBoundingSphere();
    if (geom.boundingSphere) {
        // ~1% of the model's bounding radius — visible without
        // dominating the view.
        radius = Math.max(geom.boundingSphere.radius * 0.01, 0.01);
    }

    disposeMarker();
    currentMarker = makeMarker(worldPos, radius);
    scene.add(currentMarker);
    frameCamera(worldPos, radius);
}
