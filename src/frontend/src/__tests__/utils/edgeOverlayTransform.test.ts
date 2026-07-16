/** The edge overlay must land ON the mesh, whenever it is built.
 *
 * getEdgeOverlay bakes a matrix into the line geometry, and the overlay is then added as a SIBLING
 * of the mesh (prepareLoadedModel adds both to the same parent; refreshEdgeOverlays re-adds to
 * `old.parent ?? mesh.parent`). So the baked matrix must be the mesh's LOCAL matrix — the scene
 * graph supplies the ancestors' contribution when it renders.
 *
 * Baking matrixWorld instead is invisible at load: setupModelLoader centers the model by moving
 * gltf_scene AFTER prepareLoadedModel runs, so the ancestors are still at the origin while the
 * overlay is built, and mesh + sibling overlay then move together. A LIVE rebuild (toggling
 * "Triangles" in the Scene→Mesh panel, which calls refreshEdgeOverlays) bakes a world matrix that
 * ALREADY contains the centering, and the scene graph applies it a second time — the overlay draws
 * offset from its mesh by exactly the centering translation, and stays offset when toggled back.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as THREE from "three";
import { CustomBatchedMesh } from "../../utils/mesh_select/CustomBatchedMesh";

function soup(triangles: number[][][]): THREE.BufferGeometry {
  const pos: number[] = [];
  for (const tri of triangles) for (const v of tri) pos.push(...v);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
  return g;
}

const mockRenderer = { capabilities: { maxTextureSize: 4096 } } as unknown as THREE.WebGLRenderer;

/** A mesh under a parent, mirroring the real graph: modelGroup > gltf_scene > mesh. */
function scene() {
  const geom = soup([[[0, 0, 0], [1, 0, 0], [1, 1, 0]]]);
  const ranges = new Map<string, [number, number]>([["A", [0, 3]]]);
  const mesh = new CustomBatchedMesh(geom, new THREE.MeshBasicMaterial(), ranges, "k", true, null);
  const gltfScene = new THREE.Group();
  gltfScene.add(mesh);
  const root = new THREE.Scene();
  root.add(gltfScene);
  return { mesh, gltfScene, root };
}

/** World-space centroid of a LineSegments' baked vertices, after the scene graph's transforms. */
function overlayWorldCentroid(overlay: THREE.LineSegments): THREE.Vector3 {
  overlay.updateWorldMatrix(true, false);
  const pos = overlay.geometry.getAttribute("position");
  const acc = new THREE.Vector3();
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i).applyMatrix4(overlay.matrixWorld);
    acc.add(v);
  }
  return acc.divideScalar(Math.max(pos.count, 1));
}

/** World-space centroid of the mesh's own geometry. */
function meshWorldCentroid(mesh: THREE.Mesh): THREE.Vector3 {
  mesh.updateWorldMatrix(true, false);
  const pos = mesh.geometry.getAttribute("position");
  const acc = new THREE.Vector3();
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld);
    acc.add(v);
  }
  return acc.divideScalar(Math.max(pos.count, 1));
}

test("overlay built BEFORE centering lands on the mesh (the load path)", () => {
  const { mesh, gltfScene } = scene();
  const overlay = mesh.getEdgeOverlay(mockRenderer);
  gltfScene.add(overlay); // sibling of the mesh, as prepareLoadedModel does

  // setupModelLoader centers the model only after the meshes are prepared.
  gltfScene.position.set(-100, -200, -300);
  gltfScene.updateMatrixWorld(true);

  const d = overlayWorldCentroid(overlay).distanceTo(meshWorldCentroid(mesh));
  assert.ok(d < 1e-6, `overlay offset from mesh by ${d}`);
});

test("overlay rebuilt AFTER centering lands on the mesh (the Triangles-toggle path)", () => {
  const { mesh, gltfScene } = scene();

  // Model already centered and live — this is the state a live rebuild happens in.
  gltfScene.position.set(-100, -200, -300);
  gltfScene.updateMatrixWorld(true);

  // refreshEdgeOverlays: drop the old overlay, rebuild, re-add to the same parent.
  mesh.invalidateEdgeOverlay();
  const overlay = mesh.getEdgeOverlay(mockRenderer);
  gltfScene.add(overlay);
  gltfScene.updateMatrixWorld(true);

  const d = overlayWorldCentroid(overlay).distanceTo(meshWorldCentroid(mesh));
  assert.ok(d < 1e-6, `overlay offset from mesh by ${d} (the centering applied twice?)`);
});

test("a mesh with its own local transform still gets a coincident overlay", () => {
  const { mesh, gltfScene } = scene();
  mesh.position.set(7, 0, 0); // the mesh's own placement must still be honoured
  mesh.updateMatrix();
  gltfScene.position.set(-100, -200, -300);
  gltfScene.updateMatrixWorld(true);

  const overlay = mesh.getEdgeOverlay(mockRenderer);
  gltfScene.add(overlay);
  gltfScene.updateMatrixWorld(true);

  const d = overlayWorldCentroid(overlay).distanceTo(meshWorldCentroid(mesh));
  assert.ok(d < 1e-6, `overlay offset from mesh by ${d}`);
});
