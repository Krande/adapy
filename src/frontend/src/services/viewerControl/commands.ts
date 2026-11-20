// Running the commands an agent sends this viewer over the REST API (`/api/viewer/...`; the
// vocabulary is `src/ada/comms/rest/viewer_control.py`, documented in the REST API docs). Each
// command does what the UI's own control for it does -- `focus_joint` is the joints review's
// `focusJoint`, `open_detail_model` the Detail models list's Load button -- through the same
// functions, so an agent and a user end in the same state.
//
// Everything the commands touch comes in through `ViewerControlDeps`, so the commands run under
// `node --test` against a fake viewer; `realViewerControlDeps` (./realDeps.ts) is the app's.

import type { WireDetailModel } from "@/services/api/clashCheck";
import type { ObjectRef, ViewportApi, ViewportCameraPose } from "@/viewport/api/viewportApi";

type Vec3 = [number, number, number];

export type ViewerCommand =
  | { action: "focus_joint"; joint_id: string; run_key?: string | null; scope?: string | null; frame?: boolean; reveal_panel?: boolean }
  | { action: "select"; names: string[]; file?: string | null; frame?: boolean }
  | { action: "clear_selection" }
  | {
      action: "camera";
      fit?: "selection" | "all" | null;
      view?: string | null;
      eye?: Vec3 | null;
      target?: Vec3 | null;
      box?: { min: Vec3; max: Vec3 } | null;
    }
  | { action: "open_detail_model"; detail_id: string; scope?: string | null; joint_id?: string | null; frame?: boolean }
  | { action: "find_objects"; names: string[]; file?: string | null }
  | { action: "get_state" };

export const VIEWER_ACTIONS = [
  "focus_joint",
  "select",
  "clear_selection",
  "camera",
  "open_detail_model",
  "find_objects",
  "get_state",
] as const;

/** What the viewer shows, as every answer reports it. */
export interface ViewerState {
  scope: string | null;
  viewport: string | null;
  /** The loaded models, by source name (a storage key). */
  loaded_models: string[];
  active_model: string | null;
  /** The kept detail models loaded, by detail id. */
  detail_models_loaded: string[];
  selection: { count: number; objects: ObjectRef[]; truncated: boolean; bounds: { min: Vec3; max: Vec3 } | null };
  camera: ViewportCameraPose | null;
  clash: { run_key: string | null; joints: number | null; selected_joint: string | null };
}

/** What the commands reach: the viewer's stores and scene, behind functions. */
export interface ViewerControlDeps {
  viewport(): ViewportApi | null;
  scope(): string | null;
  loadedSources(): readonly string[];
  activeSource(): string | null;
  clash(): { runKey: string | null; joints: number | null; selectedJoint: string | null; hasJoint(id: string): boolean };
  /** Open a convex-contact run as the clash result (the Clashes tab's reopen). */
  openRun(scope: string, runKey: string): Promise<void>;
  /** The joints review's focus: cursor, group, members selected, panel revealed if asked. */
  focusJoint(jointId: string, revealPanel: boolean): Promise<void>;
  /** Select objects by name in the loaded source `file` (the Clashes panel's cross-model select). */
  selectNames(file: string, names: readonly string[]): Promise<void>;
  /** The objects that draw each name in the loaded source `file`. */
  objectsNamed(file: string, name: string): Promise<ObjectRef[]>;
  listDetailModels(scope: string): Promise<readonly WireDetailModel[]>;
  /** Load a kept detail model, as its Load button does. */
  openDetailModel(scope: string, model: WireDetailModel): Promise<void>;
  /** The connection names a detail model's take-off lists for `jointId` (`<spec>_<joint id>`). */
  detailConnections(scope: string, model: WireDetailModel, jointId: string): Promise<string[]>;
  /** Let the scene catch up with a change (a frame), before reading it back. */
  settle(): Promise<void>;
}

export class ViewerCommandError extends Error {}

/** Objects listed in a state report at most: the count says how many there are. */
const MAX_LISTED_OBJECTS = 50;
const DETAIL_GLB = /^_clash\/details\/([^/]+)\/model\.glb$/;

function bounds(viewport: ViewportApi | null): ViewerState["selection"]["bounds"] {
  const b = viewport?.selectionBounds?.() ?? null;
  return b ? { min: [b.min[0], b.min[1], b.min[2]], max: [b.max[0], b.max[1], b.max[2]] } : null;
}

export function collectViewerState(deps: ViewerControlDeps): ViewerState {
  const viewport = deps.viewport();
  const loaded = [...deps.loadedSources()];
  const objects: ObjectRef[] = [];
  let count = 0;
  if (viewport) {
    count = viewport.selectionCount();
    for (const ref of viewport.selection()) {
      if (objects.length >= MAX_LISTED_OBJECTS) break;
      objects.push({ model: ref.model, id: ref.id, ...(ref.node ? { node: ref.node } : {}) });
    }
  }
  const clash = deps.clash();
  return {
    scope: deps.scope(),
    viewport: viewport?.kind ?? null,
    loaded_models: loaded,
    active_model: deps.activeSource(),
    detail_models_loaded: loaded.flatMap((k) => {
      const m = DETAIL_GLB.exec(k);
      return m ? [m[1]] : [];
    }),
    selection: { count, objects, truncated: count > objects.length, bounds: viewport ? bounds(viewport) : null },
    camera: viewport?.cameraPose() ?? null,
    clash: { run_key: clash.runKey, joints: clash.joints, selected_joint: clash.selectedJoint },
  };
}

function needViewport(deps: ViewerControlDeps): ViewportApi {
  const v = deps.viewport();
  if (!v) throw new ViewerCommandError("this viewer has no 3D view open");
  return v;
}

function needScope(deps: ViewerControlDeps, given: string | null | undefined): string {
  const scope = given || deps.scope();
  if (!scope) throw new ViewerCommandError("no scope: name one, or open a scope in the viewer");
  return scope;
}

async function frameSelection(deps: ViewerControlDeps): Promise<number> {
  await deps.settle();
  const v = needViewport(deps);
  const n = v.selectionCount();
  if (n > 0) v.fitSelection();
  return n;
}

async function focusJoint(deps: ViewerControlDeps, c: Extract<ViewerCommand, { action: "focus_joint" }>) {
  if (c.run_key && deps.clash().runKey !== c.run_key) {
    await deps.openRun(needScope(deps, c.scope), c.run_key);
    if (deps.clash().runKey !== c.run_key) throw new ViewerCommandError(`could not open the clash run ${c.run_key}`);
  }
  if (!deps.clash().hasJoint(c.joint_id)) {
    const run = deps.clash().runKey;
    throw new ViewerCommandError(
      `joint ${c.joint_id} is not in the clash result open here${run ? ` (${run})` : ""}; pass the run_key it is in`,
    );
  }
  await deps.focusJoint(c.joint_id, c.reveal_panel !== false);
  const selected = c.frame === false ? (await deps.settle(), deps.viewport()?.selectionCount() ?? 0) : await frameSelection(deps);
  return { joint_id: c.joint_id, selected };
}

function fileFor(deps: ViewerControlDeps, file: string | null | undefined): string {
  const loaded = deps.loadedSources();
  if (file) {
    if (!loaded.includes(file)) throw new ViewerCommandError(`${file} is not loaded in this viewer`);
    return file;
  }
  const active = deps.activeSource() ?? (loaded.length === 1 ? loaded[0] : null);
  if (!active) throw new ViewerCommandError("several models are loaded: name the one to look in ('file')");
  return active;
}

async function select(deps: ViewerControlDeps, c: Extract<ViewerCommand, { action: "select" }>) {
  const file = fileFor(deps, c.file);
  await deps.selectNames(file, c.names);
  const selected = c.frame === false ? (await deps.settle(), deps.viewport()?.selectionCount() ?? 0) : await frameSelection(deps);
  if (selected === 0) throw new ViewerCommandError(`none of ${c.names.length} name(s) is in ${file}`);
  return { file, selected, bounds: bounds(deps.viewport()) };
}

function camera(deps: ViewerControlDeps, c: Extract<ViewerCommand, { action: "camera" }>) {
  const v = needViewport(deps);
  if (c.fit === "selection") {
    if (v.selectionCount() === 0) throw new ViewerCommandError("nothing is selected to fit");
    v.fitSelection();
  } else if (c.fit === "all") v.fitAll();
  else if (c.view) v.setView(c.view as Parameters<ViewportApi["setView"]>[0]);
  else if (c.eye && c.target) v.setCameraPose(c.eye, c.target, 0);
  else if (c.box) {
    if (!v.fitBox(c.box.min, c.box.max)) throw new ViewerCommandError("the box could not be framed (empty, or no camera yet)");
  } else throw new ViewerCommandError("give one of fit, view, eye+target or box");
  return { camera: v.cameraPose() };
}

async function openDetailModel(deps: ViewerControlDeps, c: Extract<ViewerCommand, { action: "open_detail_model" }>) {
  const scope = needScope(deps, c.scope);
  const model = (await deps.listDetailModels(scope)).find((m) => m.detail_id === c.detail_id);
  if (!model) throw new ViewerCommandError(`no detail model ${c.detail_id} in ${scope}`);
  if (model.status !== "done") throw new ViewerCommandError(`detail model ${c.detail_id} is ${model.status}, not built yet`);
  if (!deps.loadedSources().includes(model.glb_key)) await deps.openDetailModel(scope, model);
  if (!deps.loadedSources().includes(model.glb_key)) throw new ViewerCommandError(`could not load ${model.label}`);
  if (!c.joint_id) {
    if (c.frame !== false) needViewport(deps).fitAll();
    return { detail_id: c.detail_id, glb_key: model.glb_key };
  }
  // The joint's connection(s) in the detail model; a joint it does not hold (declined, failed) is
  // shown in the clash run it was detailed from instead, so its members are still on screen.
  const names = await deps.detailConnections(scope, model, c.joint_id);
  if (names.length > 0) {
    await deps.selectNames(model.glb_key, names);
    const selected = c.frame === false ? 0 : await frameSelection(deps);
    if (c.frame === false || selected > 0) return { detail_id: c.detail_id, joint_id: c.joint_id, shown_in: "detail_model", connections: names };
  }
  const runKey = model.source.kind === "run" ? model.source.run_key ?? null : null;
  if (!runKey) throw new ViewerCommandError(`joint ${c.joint_id} is not in this detail model, and it was not made from a clash run`);
  const out = await focusJoint(deps, { action: "focus_joint", joint_id: c.joint_id, run_key: runKey, scope, frame: c.frame, reveal_panel: true });
  return { detail_id: c.detail_id, joint_id: c.joint_id, shown_in: "source_model", run_key: runKey, selected: out.selected };
}

async function findObjects(deps: ViewerControlDeps, c: Extract<ViewerCommand, { action: "find_objects" }>) {
  const viewport = needViewport(deps);
  const files = c.file ? [fileFor(deps, c.file)] : [...deps.loadedSources()];
  const found: { name: string; file: string; objects: number; centre: Vec3 | null; bounds: ViewerState["selection"]["bounds"] }[] = [];
  const missing: string[] = [];
  // A box is the viewport's answer for a SELECTION: each name is selected in turn, measured, and
  // the selection the user had is put back.
  const before = viewport.captureState({ selection: true });
  try {
    for (const name of c.names) {
      let hit = false;
      for (const file of files) {
        const refs = await deps.objectsNamed(file, name);
        if (refs.length === 0) continue;
        hit = true;
        const centre = viewport.objectsCentre(refs);
        viewport.setSelection(refs);
        found.push({ name, file, objects: refs.length, centre: centre ? [centre[0], centre[1], centre[2]] : null, bounds: bounds(viewport) });
      }
      if (!hit) missing.push(name);
    }
  } finally {
    viewport.restoreState(before);
  }
  return { found, missing };
}

/** Run one command; the answer's `result`. Throws `ViewerCommandError` (or anything a dep throws)
 *  for a command that could not be done -- the caller answers it as an error. */
export async function runViewerCommand(deps: ViewerControlDeps, command: ViewerCommand): Promise<unknown> {
  switch (command.action) {
    case "focus_joint":
      return focusJoint(deps, command);
    case "select":
      return select(deps, command);
    case "clear_selection":
      needViewport(deps).clearSelection();
      return { selected: 0 };
    case "camera":
      return camera(deps, command);
    case "open_detail_model":
      return openDetailModel(deps, command);
    case "find_objects":
      return findObjects(deps, command);
    case "get_state":
      return null;
    default:
      throw new ViewerCommandError(`unknown action ${JSON.stringify((command as { action?: unknown }).action)}`);
  }
}
