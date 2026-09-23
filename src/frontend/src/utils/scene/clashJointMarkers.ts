// The joints a clash check found, drawn in the 3D scene as one coloured sphere per joint.
//
// WHY. The Clashes panel lists GROUPS, and a group is a count -- "48 × BEAM · HP/I ·
// Girder→Girder". A count says nothing about WHERE those joints are, so reading a row gave no way
// to tell a roof grid from a deck edge without selecting members one row at a time. A marker per
// joint, coloured by the row it belongs to, makes the list and the model the same picture: the
// swatch on a row is the colour of its spheres, because both come from `jointMarkers()` in
// `clashCheckStore` (one derived view -- the same discipline the panel's counts hold).
//
// SPACE. Joint centres are in the SOURCE model's coordinates. Two transforms separate those from
// world space -- the up-axis fix, and the offset every load applies to centre the model in the
// scene -- and both sit on the glTF scene node INSIDE the group registered as the loaded source,
// not on that group (`setupModelLoader`, which marks it `__sourceSpaceRoot` for exactly this).
// Taking the registered group's matrix instead draws every sphere at its un-recentred position,
// which looks like a model-sized offset. The overlay copies that matrix rather than being
// parented to the node: parenting would put a non-mesh child inside a subtree that the tree
// build, the mesh-stats walk and the GPU picker all scan for meshes.
//
// LIFETIME. The overlay is a view of the RESULT, not of the panel: it is driven by a store
// subscription (`startClashMarkerSync`), so switching to another scene tab, or closing the panel,
// leaves the markers where they are. Unloading the model takes them down.

import * as THREE from "three";

import { loadedSourceGroups, useModelState } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";
import {
  checkedSourceName,
  filteredGroups,
  isolationMembers,
  jointMarkers,
  useClashCheckStore,
  type JointMarker,
} from "@/state/clashCheckStore";
import { clearJointIsolation, isolateJointMembers } from "@/utils/scene/clashIsolation";
import { getViewerRuntime } from "@/state/viewerRuntime";

/** Marker radius as a fraction of the model's bounding-box diagonal. A joint is a point, so the
 *  only size that reads at every zoom is one relative to the thing it sits in. */
const RADIUS_FRACTION = 0.008;
const MIN_RADIUS = 0.01;

/** Dim factor applied to a marker outside the expanded group (see `JointMarker.emphasised`). */
const DIMMED = 0.28;

let overlay: THREE.InstancedMesh | null = null;
/** instance index -> joint id, so a ray hit on the overlay names a joint. */
let drawnIds: string[] = [];
let syncing = false;

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

/** Bounding-box diagonal of the loaded model, for sizing. Falls back to the spread of the joints
 *  themselves when the group has no measurable extent (a source whose meshes are all empty). */
export function markerRadius(diagonal: number): number {
  return Math.max(MIN_RADIUS, diagonal * RADIUS_FRACTION);
}

function spreadDiagonal(markers: readonly JointMarker[]): number {
  if (markers.length === 0) return 0;
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const m of markers) {
    for (let i = 0; i < 3; i++) {
      min[i] = Math.min(min[i], m.centre[i]);
      max[i] = Math.max(max[i], m.centre[i]);
    }
  }
  return Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
}

/** The node whose local space the source's own coordinates are -- the glTF scene the loader
 *  rotated and recentred, marked by `setupModelLoader`. Falls back to the registered group for a
 *  load path that adds its group to the scene without that step (the FEA streaming session, a
 *  plugin), where the group IS that node. */
function sourceSpaceRoot(group: THREE.Object3D): THREE.Object3D {
  let found: THREE.Object3D | null = null;
  group.traverse((obj) => {
    if (!found && obj.userData?.__sourceSpaceRoot) found = obj;
  });
  return found ?? group;
}

/** Remove the overlay and free its GPU resources. Safe to call when nothing is drawn. */
export function clearJointMarkers(): void {
  drawnIds = [];
  if (!overlay) return;
  overlay.removeFromParent();
  overlay.geometry.dispose();
  (overlay.material as THREE.Material).dispose();
  overlay = null;
  requestRender();
}

/** Draw one sphere per marker, in the space of the loaded source `file`. Returns how many were
 *  drawn -- 0 (and nothing drawn) when the scene or that source is not available. */
export function renderJointMarkers(file: string | null, markers: readonly JointMarker[]): number {
  clearJointMarkers();
  const scene = getViewerRuntime().scene.current;
  if (!scene || !file || markers.length === 0) return 0;
  const group = loadedSourceGroups.get(file);
  if (!group) return 0;

  const sourceRoot = sourceSpaceRoot(group);
  const box = new THREE.Box3().setFromObject(group);
  const diagonal = box.isEmpty() ? spreadDiagonal(markers) : box.getSize(new THREE.Vector3()).length();
  const radius = markerRadius(diagonal);

  const geometry = new THREE.SphereGeometry(radius, 12, 8);
  const material = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.85 });
  const mesh = new THREE.InstancedMesh(geometry, material, markers.length);
  mesh.name = "clash-joint-markers";
  // Marked as an overlay so the scene's own picking and measuring walks can tell it from geometry
  // the user loaded. It stays raycastable on purpose: `pickJointMarker` is what makes a sphere
  // clickable, and it is asked BEFORE the model pickers, so a marker in front of a beam wins.
  mesh.userData.__overlay = true;
  mesh.frustumCulled = false;
  mesh.renderOrder = 2;

  const matrix = new THREE.Matrix4();
  const colour = new THREE.Color();
  markers.forEach((marker, i) => {
    matrix.makeScale(marker.scale, marker.scale, marker.scale);
    matrix.setPosition(marker.centre[0], marker.centre[1], marker.centre[2]);
    mesh.setMatrixAt(i, matrix);
    colour.set(marker.color);
    if (!marker.emphasised) colour.multiplyScalar(DIMMED);
    mesh.setColorAt(i, colour);
  });
  drawnIds = markers.map((m) => m.id);
  mesh.instanceMatrix.needsUpdate = true;
  if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;

  // Source -> world, applied to the overlay itself rather than by parenting.
  sourceRoot.updateWorldMatrix(true, false);
  mesh.matrixAutoUpdate = false;
  mesh.matrix.copy(sourceRoot.matrixWorld);
  mesh.matrix.decompose(mesh.position, mesh.quaternion, mesh.scale);

  scene.add(mesh);
  overlay = mesh;
  requestRender();
  return markers.length;
}

/** The joint whose marker is under the cursor, or null. Cheap enough to ask on every click: one
 *  object and a few hundred instances, against a scene that can hold millions of triangles. */
export function pickJointMarker(
  event: { clientX: number; clientY: number },
  camera: THREE.Camera,
  renderer: THREE.WebGLRenderer,
): string | null {
  if (!overlay || drawnIds.length === 0) return null;
  const rect = renderer.domElement.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return null;
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObject(overlay, false);
  const instanceId = hits[0]?.instanceId;
  return instanceId === undefined ? null : drawnIds[instanceId] ?? null;
}

/** What the overlay should show for a given store state -- pure, so the subscription below is a
 *  one-liner and the decision is testable without a scene. */
export function markersForState(state: {
  result: ReturnType<typeof useClashCheckStore.getState>["result"];
  filters: ReturnType<typeof useClashCheckStore.getState>["filters"];
  selectedGroup: string | null;
  selectedJoint: string | null;
  showMarkers: boolean;
}): readonly JointMarker[] {
  if (!state.showMarkers || !state.result) return [];
  const shown = filteredGroups(state.result, state.filters);
  // An unfiltered result leaves every group on screen; passing `null` then skips building a set
  // per redraw for the common case.
  const visible = shown.length === state.result.groups.length ? null : new Set(shown.map((g) => g.typeKey));
  return jointMarkers(state.result, {
    visibleTypeKeys: visible,
    highlight: state.selectedGroup,
    focus: state.selectedJoint,
  });
}

/** Keep the overlay in step with the store. Idempotent: the first caller starts the subscription
 *  and later calls are no-ops, so any panel may call it on mount without coordinating. */
export function startClashMarkerSync(): void {
  if (syncing) return;
  syncing = true;

  // The store changes for reasons the overlay does not care about (a job's busy flag, a detail
  // key), and a redraw rebuilds every instance. So compare the four facts the drawing is a
  // function of -- plus the loaded model -- and skip the rest.
  let last = "";
  const redraw = (force = false) => {
    const state = useClashCheckStore.getState();
    // The markers belong to the model the CHECK ran against, not to whatever was loaded last:
    // overlaying this run's produced joints adds a second source, and drawing the centres in that
    // overlay's space would move every sphere by its own centring offset.
    const file = checkedSourceName(state.sourceName, useModelState.getState().loadedSourceName);
    const signature = JSON.stringify([
      file,
      state.showMarkers,
      state.selectedGroup,
      state.selectedJoint,
      state.isolate,
      state.isolateOpacity,
      state.filters,
      state.derivedKey,
      state.result?.joints.length ?? 0,
    ]);
    if (!force && signature === last) return;
    last = signature;
    renderJointMarkers(file, markersForState(state));
    // Isolation follows the same cursor the markers do, from the same subscription: two
    // subscriptions could show a joint whose neighbours had been faded for a different one.
    void isolateJointMembers(
      file,
      isolationMembers(state.result, state.selectedJoint, state.selectedGroup),
      state.isolate,
      state.isolateOpacity,
    );
  };

  useClashCheckStore.subscribe(() => redraw());
  // Unloading (or switching) the model takes the markers down with it: they are drawn in that
  // model's space, and a result outlives the load it was run against only as stale numbers.
  useModelState.subscribe((s, prev) => {
    if (s.loadedSourceName === prev.loadedSourceName) return;
    // A model change takes the isolation down with the markers: hidden ranges belong to the mesh
    // that was isolated, and leaving them set would hide most of a model nobody isolated.
    clearJointIsolation();
    redraw();
  });
  redraw(true);
}
