// The overlays of the joints shown in Connections > Review (`jointFocusStore`): each joint of a
// loaded model drawn with ITS OWN switches -- member axes and labels, weld labels with their symbol,
// name and throat, the welds hidden or in a colour of their own -- and no other joint of the model
// annotated. Connections > Overlays' switches are the joint being developed's (`connectionOverlay`).
//
// DRAWN THROUGH THE VIEWPORT ON SCREEN, as the preview's annotations are (`drawJointMarks`): lines
// of an overlay layer, labels as billboards, the weld colour as object marks (`highlightObjects`),
// hidden welds with `hide` -- so the three.js viewport and the three.js-free one both draw them.
//
// FROM THE MODEL'S OWN METADATA. A joint is its connection's entry in the GLB's design extension
// (`connectionGraphStore`); its beams' axes and sections, its plates' thicknesses and its welds'
// types, throats and line midpoints (`at`) are the object metadata the lineage store keeps. A plate
// is labelled, and a weld from a GLB written before welds had `at`, at the middle of what it draws
// (`ViewportApi.objectsCentre`). SPACE: the metadata's points are model coordinates, placed at their
// source's origin (`ViewportApi.sourceOrigin`).
//
// Drawn again whenever the joints or their switches change, a model's joints or metadata arrive, a
// joint's model comes on screen, or another viewport takes over. No three.js in this module.

import { findConnection, useConnectionGraphStore, type ConnectionRef } from "@/state/connectionGraphStore";
import { useJointFocusStore, type FocusedJoint } from "@/state/jointFocusStore";
import { useLineageStore } from "@/state/lineageStore";
import { useModelState } from "@/state/modelState";
import { drawJointMarks, type JointMarkMember, type JointMarkWeld } from "@/utils/scene/connectionOverlay";
import { unselectedWelds } from "@/utils/scene/connectionWeldMarks";
import { srgbHexText } from "@/utils/scene/sceneColor";
import { findObjectsByName } from "@/utils/tree_view/modelSource";
import type { Point3 } from "@/viewport/api/billboards";
import { register, type OverlayHandle } from "@/viewport/api/overlayManager";
import { activeViewport, onModelLoaded, subscribeSelection, type ObjectRef, type ViewportApi } from "@/viewport/api/viewportApi";

/** The viewport layers (lines, billboards) the shown joints are drawn in, and their marks' owner. */
export const JOINT_FOCUS_LAYER = "core:joint-focus";

const WORLD: Point3 = [0, 0, 0];

/** One draw of the shown joints' axes and labels, an entry of the overlay manager: removed, its
 *  layers go with it. */
let drawing: OverlayHandle | null = null;
/** Each joint's welds in its colour: an entry of the manager each, whose marks it merges. */
let tints: { handle: OverlayHandle; refs: ObjectRef[]; color: string }[] = [];
let hidden: { file: string; refs: ObjectRef[] }[] = [];
let stopFollowing: (() => void) | null = null;
/** Bumped by every clear: a weld symbol that decodes after it has nothing left to update. */
let generation = 0;

/** Take every shown joint's overlays off the screen, and put back the welds they hid. */
export function clearJointFocusOverlay(): void {
  generation++;
  stopFollowing?.();
  stopFollowing = null;
  for (const t of tints) t.handle.remove();
  tints = [];
  const loaded = useModelState.getState().loadedSourceNames;
  const show = hidden.filter((h) => loaded.has(h.file)).flatMap((h) => h.refs);
  hidden = [];
  if (show.length) activeViewport()?.show(show);
  drawing?.remove();
  drawing = null;
}

/** What one draw of the shown joints is drawn through. */
const drawn = (): OverlayHandle =>
  (drawing ??= register({ owner: "core:joint-focus", id: "drawing", lifetime: { kind: "scene" }, onReplace: "keep" }));

const refsOf = (names: Iterable<string>, file: string): ObjectRef[] =>
  findObjectsByName(names, { sourceName: file }).map(({ model, id }) => ({ model, id }));

const plus = (p: readonly number[], o: Point3): number[] => [p[0] + o[0], p[1] + o[1], p[2] + o[2]];

const isPoint = (p: unknown): p is number[] =>
  Array.isArray(p) && p.length >= 3 && p.slice(0, 3).every((v) => typeof v === "number" && Number.isFinite(v));

/** The object metadata called `name`, from `file` first, else from any other model on screen (a weld
 *  of a detail names the members of the model it was built at), with the source it came from. */
function metadataOf(name: string, file: string): { meta: Record<string, unknown>; file: string } | null {
  const lineage = useLineageStore.getState();
  const own = lineage.getMetadata(file, name);
  if (own) return { meta: own, file };
  for (const other of useModelState.getState().loadedSourceNames) {
    if (other === file) continue;
    const meta = lineage.getMetadata(other, name);
    if (meta) return { meta, file: other };
  }
  return null;
}

/** The joint's members as its marks need them, in world coordinates. */
function membersOf(conn: ConnectionRef, file: string, viewport: ViewportApi): JointMarkMember[] {
  const roleOf = new Map<string, string>();
  for (const [role, names] of Object.entries(conn.memberRoles)) for (const n of names) roleOf.set(n, role);
  const names = new Set([...roleOf.keys(), ...conn.beamNames, ...conn.plateNames]);
  // The members the welds say they join: a detail's connection may list none of its own.
  for (const w of conn.weldNames) {
    const meta = metadataOf(w, file)?.meta as { members?: unknown } | undefined;
    if (Array.isArray(meta?.members)) for (const m of meta.members) if (typeof m === "string") names.add(m);
  }
  const out: JointMarkMember[] = [];
  for (const name of names) {
    const found = metadataOf(name, file);
    const meta = (found?.meta ?? {}) as {
      type?: string;
      section?: { name?: string | null } | null;
      thickness?: number | null;
      axis?: { n1?: unknown; n2?: unknown } | null;
    };
    const origin = found ? viewport.sourceOrigin(found.file) : null;
    const member: JointMarkMember = {
      name,
      role: roleOf.get(name) ?? null,
      type: meta.type ?? "member",
      section: meta.section ? { name: meta.section.name ?? null, type: null, h: null, w_top: null } : null,
      thickness: typeof meta.thickness === "number" ? meta.thickness : null,
    };
    if (origin && isPoint(meta.axis?.n1) && isPoint(meta.axis?.n2)) {
      member.axis = [plus(meta.axis.n1, origin), plus(meta.axis.n2, origin)];
    } else {
      const centre = viewport.objectsCentre(refsOf([name], found?.file ?? file));
      if (centre) member.centre = [...centre];
    }
    if (member.axis || member.centre) out.push(member);
  }
  return out;
}

/** The joint's welds as their labels need them, in world coordinates. */
function weldsOf(conn: ConnectionRef, file: string, origin: Point3, viewport: ViewportApi): JointMarkWeld[] {
  return conn.weldNames.map((name) => {
    const meta = (useLineageStore.getState().getMetadata(file, name) ?? {}) as {
      weld_type?: string | null;
      throat?: number | null;
      at?: unknown;
      symbol_svg?: string | null;
    };
    const centre = isPoint(meta.at) ? null : viewport.objectsCentre(refsOf([name], file));
    return {
      name,
      weld_type: meta.weld_type ?? null,
      throat: typeof meta.throat === "number" ? meta.throat : null,
      at: isPoint(meta.at) ? plus(meta.at, origin) : centre ? [...centre] : null,
      symbol_svg: meta.symbol_svg ?? null,
    };
  });
}

function retint(): void {
  const selection = activeViewport()?.selection() ?? [];
  for (const t of tints) t.handle.setMarks(unselectedWelds(t.refs, selection), t.color);
}

/** Draw every shown joint's overlays in the viewport on screen. Returns how many marks were drawn
 *  (labels and axes; welds coloured or hidden are not counted). */
export function drawFocusedJoints(): number {
  clearJointFocusOverlay();
  const viewport = activeViewport();
  const joints = useJointFocusStore.getState().joints;
  if (!viewport || joints.length === 0) return 0;
  const mine = generation;
  const alive = () => mine === generation;
  let marks = 0;
  for (const joint of joints) marks += drawJoint(viewport, joint, alive);
  if (tints.length) stopFollowing = subscribeSelection(retint);
  return marks;
}

function drawJoint(viewport: ViewportApi, joint: FocusedJoint, alive: () => boolean): number {
  const conn = findConnection(joint.file, joint.name);
  const origin = viewport.sourceOrigin(joint.file);
  if (!conn || !origin) return 0;
  const o = joint.overlays;
  let marks = 0;
  if (o.axes || o.welds) {
    const members = o.axes ? membersOf(conn, joint.file, viewport) : [];
    const welds = o.welds ? weldsOf(conn, joint.file, origin, viewport) : [];
    const lines = members.length || welds.some((w) => w.at) ? drawn().lines(JOINT_FOCUS_LAYER) : null;
    const labels = lines ? drawn().labels(JOINT_FOCUS_LAYER) : null;
    if (lines && labels) marks = drawJointMarks(lines, labels, WORLD, members, welds, o, alive);
  }
  const refs = conn.weldNames.length ? refsOf(conn.weldNames, joint.file) : [];
  if (refs.length && !o.showWelds) {
    viewport.hide(refs);
    hidden.push({ file: joint.file, refs });
  } else if (refs.length && o.tintWelds) {
    // The weld colour is the joint's, not a highlight of the models: a replace that keeps the joint keeps it.
    const handle = register({ owner: "core:joint-focus", kind: "tint", lifetime: { kind: "scene" }, onReplace: "keep" });
    const color = srgbHexText(o.weldColour);
    handle.setMarks(unselectedWelds(refs, viewport.selection()), color);
    tints.push({ handle, refs, color });
  }
  return marks;
}

// WHEN. One redraw for any number of changes in one turn.
let pending: ReturnType<typeof setTimeout> | null = null;
function schedule(): void {
  if (pending) return;
  pending = setTimeout(() => {
    pending = null;
    drawFocusedJoints();
  }, 0);
}

const anyShown = () => useJointFocusStore.getState().joints.length > 0;

// The last joint closed draws nothing, which clears.
useJointFocusStore.subscribe((s, prev) => {
  if (s.joints !== prev.joints) schedule();
});
useConnectionGraphStore.subscribe((s, prev) => {
  if (s.byFile !== prev.byFile && anyShown()) schedule();
});
useLineageStore.subscribe((s, prev) => {
  if (s.entries !== prev.entries && anyShown()) schedule();
});
onModelLoaded((info) => {
  if (info.sourceName && useJointFocusStore.getState().joints.some((j) => j.file === info.sourceName)) schedule();
});

// The shown joints as a view of the overlay manager (viewport/api/overlayManager.ts): another viewport
// taking over draws them there. A clear of the scene takes them at once, joints and drawing -- the
// joints are of the models it takes -- rather than a turn later, when the store would have dropped
// them. A replace leaves them: one that swaps a model in place keeps its joints, and any other takes
// the models and with them their joints (`jointFocusStore`).
register({
  owner: "core:joint-focus",
  id: "joints",
  lifetime: { kind: "view" },
  onReplace: "keep",
  forget: () => {
    useJointFocusStore.getState().clear();
    clearJointFocusOverlay();
  },
  draw: () => {
    if (anyShown()) schedule();
  },
});
