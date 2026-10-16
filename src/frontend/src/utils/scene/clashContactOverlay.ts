// A convex-contact run's contacts drawn in the 3D scene: every patch the panel shows, in its
// members' ROLE colours, with the contact normal as an arrow -- what the run measured, where it is.
//
// WHAT IS DRAWN is decided in `clashContactPlan.contactOverlayPlan` (pure, tested); this file only
// turns that plan into ONE THREE group -- two filled meshes (the emphasised contacts and the rest,
// at two opacities), one set of outlines, the arrow shafts and an instanced cone per arrow head --
// and tears it down again. Rebuilt whole on every change of what it shows: a run is a few hundred
// contacts, a rebuild is a few thousand vertices.
//
// SPACE. The plan is in the run's model frame (Z up, untranslated); the group carries the
// source -> world matrix of the loaded model's glTF scene node, exactly as the joint markers do
// (`clashJointMarkers`, `sourceSpaceRoot`): the loader's up-axis fix and recentring offset both sit
// on that node.
//
// PICKING. The fills are raycast (`pickContactPatch`) before the model pickers, like a marker: a
// patch sits ON the geometry, and asked after it would never win. A triangle names its contact.

import * as THREE from "three";

import { requestRender } from "@/state/perfStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { NORMAL_COLOUR, type OverlayContact } from "@/utils/scene/clashContactPlan";

const FILL_OPACITY = { emphasised: 0.72, dimmed: 0.2 } as const;
const DIMMED = 0.35;

let overlay: THREE.Group | null = null;
/** Per fill mesh: triangle index -> contact id. */
const triangleIds = new WeakMap<THREE.Object3D, string[]>();

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

export function clearContactOverlay(): void {
  if (!overlay) return;
  overlay.removeFromParent();
  overlay.traverse((obj) => {
    const o = obj as THREE.Mesh;
    o.geometry?.dispose();
    const mat = o.material as THREE.Material | THREE.Material[] | undefined;
    if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
    else mat?.dispose();
  });
  overlay = null;
  requestRender();
}

/** The flat buffers of one fill mesh: triangle-fanned polygons, a colour per vertex, an id per
 *  triangle. Exported for the test: what is drawn and what a pick resolves to are one array. */
export function fillBuffers(contacts: readonly OverlayContact[]): { positions: Float32Array; colors: Float32Array; ids: string[] } {
  const pos: number[] = [];
  const col: number[] = [];
  const ids: string[] = [];
  const c = new THREE.Color();
  for (const contact of contacts) {
    for (const poly of contact.polygons) {
      c.set(poly.color);
      for (let k = 1; k + 1 < poly.points.length; k++) {
        for (const p of [poly.points[0], poly.points[k], poly.points[k + 1]]) {
          pos.push(p[0], p[1], p[2]);
          col.push(c.r, c.g, c.b);
        }
        ids.push(contact.id);
      }
    }
  }
  return { positions: new Float32Array(pos), colors: new Float32Array(col), ids };
}

function fillMesh(contacts: readonly OverlayContact[], opacity: number, name: string): THREE.Mesh | null {
  const { positions, colors, ids } = fillBuffers(contacts);
  if (ids.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.computeBoundingSphere();
  const material = new THREE.MeshBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity,
    side: THREE.DoubleSide,
    depthWrite: false,
    // Drawn on the faces it lies in: pulled towards the camera so it is not lost in them.
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = name;
  mesh.renderOrder = 3;
  triangleIds.set(mesh, ids);
  return mesh;
}

function lines(contacts: readonly OverlayContact[]): THREE.LineSegments | null {
  const pos: number[] = [];
  const col: number[] = [];
  const c = new THREE.Color();
  const push = (a: readonly number[], b: readonly number[]) => {
    pos.push(a[0], a[1], a[2], b[0], b[1], b[2]);
    col.push(c.r, c.g, c.b, c.r, c.g, c.b);
  };
  for (const contact of contacts) {
    const dim = contact.emphasised ? 1 : DIMMED;
    for (const poly of contact.polygons) {
      c.set(poly.color).multiplyScalar(dim);
      poly.points.forEach((p, k) => push(p, poly.points[(k + 1) % poly.points.length]));
    }
    if (contact.arrow) {
      c.set(NORMAL_COLOUR).multiplyScalar(dim);
      push(contact.arrow.from, contact.arrow.to);
    }
  }
  if (pos.length === 0) return null;
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  const seg = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95 }));
  seg.name = "clash-contact-lines";
  seg.renderOrder = 4;
  return seg;
}

function arrowHeads(contacts: readonly OverlayContact[]): THREE.InstancedMesh | null {
  const arrows = contacts.filter((c) => c.arrow);
  if (arrows.length === 0) return null;
  // Unit cone along +Y with its TIP at the origin: placed at the arrow's end, scaled and turned below.
  const geometry = new THREE.ConeGeometry(0.35, 1, 10);
  geometry.translate(0, -0.5, 0);
  const mesh = new THREE.InstancedMesh(geometry, new THREE.MeshBasicMaterial(), arrows.length);
  mesh.name = "clash-contact-arrow-heads";
  mesh.renderOrder = 4;
  const up = new THREE.Vector3(0, 1, 0);
  const from = new THREE.Vector3();
  const to = new THREE.Vector3();
  const dir = new THREE.Vector3();
  const q = new THREE.Quaternion();
  const s = new THREE.Vector3();
  const m = new THREE.Matrix4();
  const c = new THREE.Color();
  arrows.forEach((contact, k) => {
    from.set(...contact.arrow!.from);
    to.set(...contact.arrow!.to);
    dir.subVectors(to, from);
    const length = dir.length() || 1;
    q.setFromUnitVectors(up, dir.normalize());
    const head = length * 0.25;
    s.set(head, head, head);
    m.compose(to, q, s);
    mesh.setMatrixAt(k, m);
    mesh.setColorAt(k, c.set(NORMAL_COLOUR).multiplyScalar(contact.emphasised ? 1 : DIMMED));
  });
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  return mesh;
}

/** Draw `contacts` (model frame) placed by `sourceToWorld`. Returns how many were drawn. */
export function renderContactOverlay(contacts: readonly OverlayContact[], sourceToWorld: THREE.Matrix4 | null): number {
  clearContactOverlay();
  const scene = getViewerRuntime().scene.current;
  if (!scene || contacts.length === 0) return 0;
  const group = new THREE.Group();
  group.name = "clash-contact-overlay";
  // An annotation, not geometry: the scene's own picking, measuring and stats walks skip it.
  group.userData.__overlay = true;
  const parts = [
    fillMesh(contacts.filter((c) => c.emphasised), FILL_OPACITY.emphasised, "clash-contact-fills"),
    fillMesh(contacts.filter((c) => !c.emphasised), FILL_OPACITY.dimmed, "clash-contact-fills-dimmed"),
    lines(contacts),
    arrowHeads(contacts),
  ];
  for (const p of parts) {
    if (!p) continue;
    p.userData.__overlay = true;
    p.frustumCulled = false;
    group.add(p);
  }
  group.matrixAutoUpdate = false;
  if (sourceToWorld) group.matrix.copy(sourceToWorld);
  group.matrix.decompose(group.position, group.quaternion, group.scale);
  scene.add(group);
  group.updateMatrixWorld(true);
  overlay = group;
  requestRender();
  return contacts.length;
}

/** The contact whose patch is under the cursor, or null. */
export function pickContactPatch(
  event: { clientX: number; clientY: number },
  camera: THREE.Camera,
  renderer: THREE.WebGLRenderer,
): string | null {
  if (!overlay) return null;
  const rect = renderer.domElement.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const fills = overlay.children.filter((o) => triangleIds.has(o));
  const hit = raycaster.intersectObjects(fills, false)[0];
  if (!hit || hit.faceIndex == null) return null;
  return triangleIds.get(hit.object)?.[hit.faceIndex] ?? null;
}
