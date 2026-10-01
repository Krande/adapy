// A project's LOADING VIEW, and the bulk-load session that uses it.
//
// Loading many models used to re-frame the camera after every one (the scene's auto-fit), so the
// view jumped once per model while the user was trying to look at it. During a bulk load the
// camera is now placed ONCE and left alone:
//
//   - with a saved loading view for the project, it is applied before the first model arrives;
//   - without one, the camera frames the first model that lands, and holds;
//   - when the load finishes, the view framing EVERYTHING it loaded is saved as the project's
//     loading view -- the total bounding box nobody knew up front -- so the next load starts there.
//
// The user can replace it with the current view at any time, or clear it. Kept per user, in this
// browser, keyed by scope and project.
//
// SOURCE SPACE. Every model is shifted by the scene's recentring offset (`modelState.translation`,
// taken from whichever model loaded first). A camera saved in world coordinates would only line up
// when the same model happened to load first again, so the view is saved relative to that offset,
// with the offset itself: a later load into an empty scene pins the same offset, and one into a
// scene that already has another adds whatever offset is there.

import * as THREE from "three";
import CameraControls from "camera-controls";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls";

import { applyAdaptiveClipping } from "@/components/viewer/sceneHelpers/adaptiveClipping";
import { useModelState } from "@/state/modelState";
import { useOptionsStore } from "@/state/optionsStore";
import { requestRender } from "@/state/perfStore";
import { getViewerRuntime } from "@/state/viewerRuntime";

type Vec3 = [number, number, number];

export interface LoadingView {
  v: 1;
  /** The recentring offset the models were shifted by when this was saved; null when none was. */
  translation: Vec3 | null;
  /** Camera position and orbit target, minus `translation` -- the project's own coordinates. */
  position: Vec3;
  target: Vec3;
  up: Vec3;
}

const STORAGE_PREFIX = "ada.loadingView:";

const storageKey = (scope: string, project: string) => `${STORAGE_PREFIX}${scope}|${project}`;

export function readLoadingView(scope: string, project: string): LoadingView | null {
  try {
    const raw = globalThis.localStorage?.getItem(storageKey(scope, project));
    if (!raw) return null;
    const v = JSON.parse(raw) as LoadingView;
    return v && v.v === 1 && isVec3(v.position) && isVec3(v.target) && isVec3(v.up) ? v : null;
  } catch {
    return null;
  }
}

export function writeLoadingView(scope: string, project: string, view: LoadingView): void {
  try {
    globalThis.localStorage?.setItem(storageKey(scope, project), JSON.stringify(view));
  } catch {
    // Private mode or a full store: the view is a convenience, the load goes on without it.
  }
}

export function clearLoadingView(scope: string, project: string): void {
  try {
    globalThis.localStorage?.removeItem(storageKey(scope, project));
  } catch {
    // As above.
  }
}

function isVec3(v: unknown): v is Vec3 {
  return Array.isArray(v) && v.length === 3 && v.every((n) => typeof n === "number" && Number.isFinite(n));
}

const vec = (v: THREE.Vector3): Vec3 => [v.x, v.y, v.z];

/** Where a camera looking along `direction` must stand to fit `box` -- `zoomToAll`'s framing,
 *  without moving anything. Null for an empty or degenerate box. */
export function fitView(
  box: THREE.Box3,
  direction: THREE.Vector3,
  fovDeg: number,
  aspect: number,
): { position: THREE.Vector3; target: THREE.Vector3; radius: number } | null {
  if (box.isEmpty()) return null;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  if (!(sphere.radius > 0) || !Number.isFinite(sphere.radius)) return null;
  const vFov = THREE.MathUtils.degToRad(fovDeg);
  const vDist = sphere.radius / Math.tan(vFov / 2);
  const hFov = 2 * Math.atan(Math.tan(vFov / 2) * (aspect || 1));
  const hDist = sphere.radius / Math.tan(hFov / 2);
  const dir = direction.clone().normalize();
  const position = sphere.center.clone().add(dir.multiplyScalar(-Math.max(vDist, hDist)));
  return { position, target: sphere.center.clone(), radius: sphere.radius };
}

/** The offset models are shifted by now (zero when none was set, or translation is locked). */
function sceneOffset(): THREE.Vector3 {
  return useModelState.getState().translation?.clone() ?? new THREE.Vector3();
}

/** Put the camera at `view`. Pins the recentring offset first when the scene has none yet, so the
 *  models about to load land where the view expects them. False when there is no camera. */
export function applyLoadingView(view: LoadingView): boolean {
  const rt = getViewerRuntime();
  const camera = rt.camera.current as THREE.PerspectiveCamera | null;
  const controls = rt.controls.current as OrbitControls | CameraControls | null;
  if (!camera || !controls) return false;
  const models = useModelState.getState();
  if (!models.translation && view.translation && !useOptionsStore.getState().lockTranslation) {
    models.setTranslation(new THREE.Vector3(...view.translation));
  }
  const offset = sceneOffset();
  const position = new THREE.Vector3(...view.position).add(offset);
  const target = new THREE.Vector3(...view.target).add(offset);
  camera.up.set(...view.up);
  placeCamera(camera, controls, position, target, false);
  applyAdaptiveClipping(camera, controls, position.distanceTo(target));
  requestRender();
  return true;
}

/** The camera as it stands, as a loading view. Null when there is no camera. */
export function captureLoadingView(): LoadingView | null {
  const rt = getViewerRuntime();
  const camera = rt.camera.current as THREE.PerspectiveCamera | null;
  const controls = rt.controls.current as OrbitControls | CameraControls | null;
  if (!camera || !controls) return null;
  const offset = sceneOffset();
  const target = controlTarget(controls);
  return {
    v: 1,
    translation: useModelState.getState().translation ? vec(offset) : null,
    position: vec(camera.position.clone().sub(offset)),
    target: vec(target.sub(offset)),
    up: vec(camera.up),
  };
}

function controlTarget(controls: OrbitControls | CameraControls): THREE.Vector3 {
  if (controls instanceof OrbitControls) return controls.target.clone();
  return (controls as CameraControls).getTarget(new THREE.Vector3());
}

function placeCamera(
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls | CameraControls,
  position: THREE.Vector3,
  target: THREE.Vector3,
  smooth: boolean,
): void {
  if (controls instanceof OrbitControls) {
    camera.position.copy(position);
    camera.lookAt(target);
    controls.target.copy(target);
    camera.updateProjectionMatrix();
    controls.update();
  } else {
    void (controls as CameraControls).setLookAt(position.x, position.y, position.z, target.x, target.y, target.z, smooth);
    camera.updateProjectionMatrix();
  }
}

// --- the bulk-load session ----------------------------------------------------------------------

interface Session {
  scope: string;
  project: string;
  /** Union of every model this session loaded, in world coordinates. */
  box: THREE.Box3;
  /** The camera has been placed (a saved view applied, or the first model framed). */
  placed: boolean;
  loaded: number;
}

let session: Session | null = null;

/** Whether a bulk load is running: the loader then skips its per-model fit and slices its work. */
export function bulkLoadActive(): boolean {
  return session !== null;
}

/** Start loading many models of `project` in `scope`: the camera goes to the project's loading
 *  view now, when there is one, and stays there. Returns the handle `endBulkLoad` takes -- a
 *  second bulk load started meanwhile takes the session over, and the first one's end then leaves
 *  it alone. */
export function beginBulkLoad(scope: string, project: string): object {
  const view = readLoadingView(scope, project);
  const s: Session = { scope, project, box: new THREE.Box3(), placed: false, loaded: 0 };
  // Taking over from a session still running: the camera was already placed for this scene.
  if (session) s.placed = true;
  session = s;
  if (view && !s.placed) s.placed = applyLoadingView(view);
  return s;
}

/** The loader's report of one model in the scene, with its world box. Frames the FIRST one when no
 *  saved view placed the camera, and nothing after that. */
export function noteBulkModel(worldBox: THREE.Box3): void {
  if (!session || worldBox.isEmpty()) return;
  session.box.union(worldBox);
  session.loaded += 1;
  // Near/far must cover everything loaded so far, not only the model that just arrived -- the
  // loader's own clipping only knows its last model.
  const camera = getViewerRuntime().camera.current as THREE.PerspectiveCamera | null;
  const controls = getViewerRuntime().controls.current as OrbitControls | CameraControls | null;
  if (!camera || !controls) return;
  const all = session.box.getBoundingSphere(new THREE.Sphere()).radius;
  if (Number.isFinite(all) && all > 0) applyAdaptiveClipping(camera, controls, all);
  if (session.placed) return;
  const fit = fitView(worldBox, camera.getWorldDirection(new THREE.Vector3()), camera.fov, camera.aspect);
  if (fit) placeCamera(camera, controls, fit.position, fit.target, false);
  session.placed = true;
  requestRender();
}

/** The bulk load is over. A project with no loading view gets one: the view framing everything
 *  this session loaded, from the direction the camera looks now. The camera itself is not moved. */
export function endBulkLoad(handle: object): void {
  if (session !== handle) return;
  const s = session;
  session = null;
  if (s.loaded === 0 || readLoadingView(s.scope, s.project)) return;
  const camera = getViewerRuntime().camera.current as THREE.PerspectiveCamera | null;
  if (!camera) return;
  const fit = fitView(s.box, camera.getWorldDirection(new THREE.Vector3()), camera.fov, camera.aspect);
  if (!fit) return;
  const offset = sceneOffset();
  writeLoadingView(s.scope, s.project, {
    v: 1,
    translation: useModelState.getState().translation ? vec(offset) : null,
    position: vec(fit.position.sub(offset)),
    target: vec(fit.target.sub(offset)),
    up: vec(camera.up),
  });
}
