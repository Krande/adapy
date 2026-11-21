// The joints a clash check found, drawn in the 3D scene as one coloured dot per joint, and a
// convex-contact run's patches and normals beside them (`clashContactOverlay`).
//
// WHY. The Clashes panel lists GROUPS, and a group is a count -- "48 × BEAM · HP/I ·
// Girder→Girder". A count says nothing about WHERE those joints are, so reading a row gave no way
// to tell a roof grid from a deck edge without selecting members one row at a time. A marker per
// joint, coloured by the row it belongs to, makes the list and the model the same picture: the
// swatch on a row is the colour of its dots, because both come from `jointMarkers()` in
// `clashCheckStore` (one derived view -- the same discipline the panel's counts hold).
//
// DRAWN THROUGH THE VIEWPORT ON SCREEN (viewport/api): the markers as points of an overlay layer,
// sized in screen pixels, and the contacts as meshes and lines of another, so either viewport draws
// them. They were three.js objects added to the three.js scene (an instanced sphere mesh named
// "clash-joint-markers"; the name is kept here for the committed bundle's provenance check), which
// the three.js-free viewport never draws: a check there, a saved group's above all, showed its rows
// and nothing in 3D.
//
// SPACE. Joint centres and contact points are in the SOURCE model's coordinates. Every viewport
// places a loaded model by one rule, world = model + its source's origin (`ViewportApi.sourceOrigin`),
// so each point is drawn at itself plus that origin. A saved group's run spans several loaded models:
// each contact (and its marker) takes the origin of the model its members are in
// (`clashContactPlan.contactOrigins`); a check of one model takes that model's.
//
// WHICH MODELS ARE LOADED is the model store's list (`loadedSourceNames`), which both viewports
// keep -- not the three.js scene's own groups, which the three.js-free viewport has none of.
//
// PICKING. The layers take no clicks; what was drawn is kept for the click test each viewport asks
// before it picks the model (`clashOverlayPicks`).
//
// LIFETIME. The overlay is a view of the RESULT, not of the panel: it is driven by a store
// subscription (`startClashMarkerSync`), so switching to another scene tab, or closing the panel,
// leaves the markers where they are. Unloading the model takes them down (its origin is gone), and
// another viewport taking over draws them there (they are a view of the overlay manager, which draws
// them again on it), and a clear of the scene takes the result they are drawn from. Nothing here draws with three.js, and neither does
// fading the rest (`clashIsolation`, through the viewport's `setObjectFade`), which was the three.js
// viewport's own until it too went through the viewport on screen.

import { useModelState } from "@/state/modelState";
import {
  checkedSourceName,
  contactRunOf,
  filteredGroups,
  isolationMembers,
  jointMarkers,
  useClashCheckStore,
  visibleResult,
  type JointMarker,
} from "@/state/clashCheckStore";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";
import { clearJointIsolation, isolateElementRanges, isolateJointMembers } from "@/utils/scene/clashIsolation";
import {
  contactIsolationTargets,
  contactKeepByPart,
  contactOrigins,
  contactOverlayPlan,
  contactSpaceSource,
} from "@/utils/scene/clashContactPlan";
import { drawContactOverlay, type ContactOrigin } from "@/utils/scene/clashContactOverlay";
import { setShownClashPicks } from "@/utils/scene/clashOverlayPicks";
import { installJointViewExit } from "@/utils/scene/jointViewExit";
import { linearColor } from "@/utils/scene/sceneColor";
import type { Point3 } from "@/viewport/api/billboards";
import type { OverlayLayer } from "@/viewport/api/overlayLayer";
import { register, type OverlayHandle } from "@/viewport/api/overlayManager";
import { activeViewport, onModelLoaded, type ViewportApi } from "@/viewport/api/viewportApi";

/** The viewport layers the markers and the contacts are drawn in. */
export const CLASH_MARKER_LAYER = "core:clash-joints";
export const CLASH_CONTACT_LAYER = "core:clash-contacts";

/** A normal marker's diameter on screen, in CSS pixels, before the user's size and its own scale. */
const MARKER_PX = 8;
const MIN_PX = 2;
const MAX_PX = 64;

/** Dim factor applied to a marker outside the expanded group (see `JointMarker.emphasised`). */
const DIMMED = 0.28;

/** What is drawn, in the world, for the click test. */
let markerPicks: { positions: Float32Array; ids: string[]; radii: number[] } = { positions: new Float32Array(0), ids: [], radii: [] };
let contactPicks: { triangles: Float32Array; ids: string[] } = { triangles: new Float32Array(0), ids: [] };
let syncing = false;
/** The sync's redraw, once it runs (`startClashMarkerSync`). */
let redrawSync: ((force?: boolean) => void) | null = null;
/** The viewport the markers' layers were made on, while they are held there. */
let drawnOn: ViewportApi | null = null;

const emptyPicks = (): void => {
  markerPicks = { positions: new Float32Array(0), ids: [], radii: [] };
  contactPicks = { triangles: new Float32Array(0), ids: [] };
};

// A view of the overlay manager (viewport/api/overlayManager.ts): drawn again from the store on the
// viewport that comes on screen; a replace keeps it (a renderer switch hands the same models over),
// and a clear empties it through the store (state/clashCheckStore.ts forgets its result).
const view: OverlayHandle = register({
  owner: "core:clash",
  id: "markers",
  lifetime: { kind: "view" },
  onReplace: "keep",
  draw: () => {
    redrawSync?.(true);
    return () => {
      // Its layers leave with the viewport: what was drawn in them is not there to click.
      drawnOn = null;
      emptyPicks();
      publishPicks();
    };
  },
});

/** The layers of the viewport on screen. */
function layers(): { viewport: ViewportApi; markers: OverlayLayer; contacts: OverlayLayer } | null {
  const markers = view.lines(CLASH_MARKER_LAYER);
  const contacts = view.lines(CLASH_CONTACT_LAYER);
  if (!markers || !contacts || !view.viewport) return null;
  drawnOn = view.viewport;
  return { viewport: view.viewport, markers, contacts };
}

/** The layers they are drawn in, when they are made on the viewport on screen: none is made to be cleared. */
const heldLayer = (name: string): OverlayLayer | null => (drawnOn && drawnOn === view.viewport ? view.lines(name) : null);

function publishPicks(): void {
  const viewport = drawnOn;
  if (!viewport || (markerPicks.ids.length === 0 && contactPicks.ids.length === 0)) {
    setShownClashPicks(null, null);
    return;
  }
  setShownClashPicks(
    viewport,
    {
      markers: markerPicks.positions,
      markerIds: markerPicks.ids,
      markerRadiiPx: markerPicks.radii,
      triangles: contactPicks.triangles,
      triangleIds: contactPicks.ids,
    },
    // Imported on use: focusing reaches the panels and the scene.
    (id) => void import("@/utils/scene/clashJointFocus").then((m) => m.focusJoint(id, { revealPanel: true })),
  );
}

/** Remove the markers. Safe to call when nothing is drawn. */
export function clearJointMarkers(): void {
  markerPicks = { positions: new Float32Array(0), ids: [], radii: [] };
  heldLayer(CLASH_MARKER_LAYER)?.clear();
  publishPicks();
}

/** Remove the contacts overlay. Safe to call when nothing is drawn. */
export function clearContactOverlay(): void {
  contactPicks = { triangles: new Float32Array(0), ids: [] };
  heldLayer(CLASH_CONTACT_LAYER)?.clear();
  publishPicks();
}

/** A marker's diameter on screen: the user's size on top of the marker's own (a selected group's are
 *  larger, the rest smaller). */
export function markerSizePx(markerScale: number, userScale = 1): number {
  return Math.max(MIN_PX, Math.min(MAX_PX, MARKER_PX * markerScale * userScale));
}

/** Draw one dot per marker, each at its centre plus `originOf(marker.id)` (null: not drawn), in the
 *  viewport on screen. Returns how many were drawn -- 0 (and nothing drawn) without a viewport. */
export function renderJointMarkers(originOf: ContactOrigin, markers: readonly JointMarker[], scale = 1): number {
  clearJointMarkers();
  const at = markers.length ? layers() : null;
  if (!at) return 0;
  // One point set per size: a handful of sizes (normal, emphasised, dimmed, focused).
  const bySize = new Map<number, { positions: number[]; colors: number[] }>();
  const positions: number[] = [];
  const ids: string[] = [];
  const radii: number[] = [];
  for (const marker of markers) {
    const o = originOf(marker.id);
    if (!o) continue;
    const size = markerSizePx(marker.scale, scale);
    let set = bySize.get(size);
    if (!set) bySize.set(size, (set = { positions: [], colors: [] }));
    const p: Point3 = [marker.centre[0] + o[0], marker.centre[1] + o[1], marker.centre[2] + o[2]];
    set.positions.push(...p);
    const c = linearColor(marker.color);
    const dim = marker.emphasised ? 1 : DIMMED;
    set.colors.push(c[0] * dim, c[1] * dim, c[2] * dim);
    positions.push(...p);
    ids.push(marker.id);
    radii.push(size / 2 + 2);
  }
  // The focused marker last: drawn over the rest.
  const sizes = [...bySize.keys()].sort((a, b) => a - b);
  for (const size of sizes) {
    const set = bySize.get(size)!;
    at.markers.addPoints({
      positions: new Float32Array(set.positions),
      colors: new Float32Array(set.colors),
      sizePx: size,
      opacity: 0.85,
      // A joint centre is inside the members it joins: drawn through them, as a marker of the model.
      onTop: true,
      fitted: false,
    });
  }
  markerPicks = { positions: new Float32Array(positions), ids, radii };
  publishPicks();
  return ids.length;
}

/** Draw a run's contacts (model frame), each placed by `originOf`. Returns how many were drawn. */
export function renderContactOverlay(contacts: Parameters<typeof drawContactOverlay>[1], originOf: ContactOrigin): number {
  clearContactOverlay();
  const at = contacts.length ? layers() : null;
  if (!at) return 0;
  const { drawn: n, triangles, triangleIds } = drawContactOverlay(at.contacts, contacts, originOf);
  contactPicks = { triangles, ids: triangleIds };
  publishPicks();
  return n;
}

/** What the overlay should show for a given store state -- pure, so the subscription below is a
 *  one-liner and the decision is testable without a scene. */
export function markersForState(state: {
  result: ReturnType<typeof useClashCheckStore.getState>["result"];
  filters: ReturnType<typeof useClashCheckStore.getState>["filters"];
  hiddenOrigins: readonly string[];
  selectedGroup: string | null;
  selectedJoint: string | null;
  showMarkers: boolean;
}): readonly JointMarker[] {
  // The FILTERED result, like every other reader: a marker for a joint the list is hiding
  // would be a dot a user cannot click through to anything.
  const result = visibleResult(state);
  if (!state.showMarkers || !result) return [];
  const shown = filteredGroups(result, state.filters);
  // An unfiltered result leaves every group on screen; passing `null` then skips building a set
  // per redraw for the common case.
  const visible = shown.length === result.groups.length ? null : new Set(shown.map((g) => g.typeKey));
  return jointMarkers(result, {
    visibleTypeKeys: visible,
    highlight: state.selectedGroup,
    focus: state.selectedJoint,
  });
}

/** Where the points of the current result are in the world, per joint / contact id, on `viewport`. */
export function resultOrigins(
  viewport: ViewportApi | null,
  state: ReturnType<typeof useClashCheckStore.getState>,
  loadedNames: readonly string[],
): ContactOrigin {
  if (!viewport) return () => null;
  const run = contactRunOf(state.result);
  // The model the CHECK ran against, not whatever was loaded last: overlaying this run's produced
  // joints adds a second source.
  const file = checkedSourceName(state.sourceName, useModelState.getState().loadedSourceName);
  const fileOrigin = file ? viewport.sourceOrigin(file) : null;
  if (!run) return () => fileOrigin;
  return contactOrigins(run, loadedNames, useAssetBrowserStore.getState().loaded, (n) => viewport.sourceOrigin(n), fileOrigin);
}

/** Keep the overlay in step with the store. Idempotent: the first caller starts the subscription
 *  and later calls are no-ops, so any panel may call it on mount without coordinating. */
export function startClashMarkerSync(): void {
  if (syncing) return;
  syncing = true;
  // Esc, or a click on empty space, leaves the faded connection view this sync draws (`jointViewExit`).
  if (typeof window !== "undefined") installJointViewExit();

  // The store changes for reasons the overlay does not care about (a job's busy flag, a detail
  // key), and a redraw rebuilds every point. So compare the facts the drawing is a function of --
  // plus the loaded models -- and skip the rest.
  let last = "";
  let lastContacts = "";
  const redraw = (force = false) => {
    const state = useClashCheckStore.getState();
    const run = contactRunOf(state.result);
    const loadedNames = [...useModelState.getState().loadedSourceNames];
    const loadedRefs = useAssetBrowserStore.getState().loaded;
    const viewport = activeViewport();
    const originOf = resultOrigins(viewport, state, loadedNames);
    // A convex-contact run names its models itself -- a group's or a selection's run is no single
    // loaded source, and its label names nothing loaded.
    const file =
      (run ? contactSpaceSource(run, loadedNames, loadedRefs) : null) ??
      checkedSourceName(state.sourceName, useModelState.getState().loadedSourceName);

    // The contacts overlay: its own signature, since it draws from the RUN (patches, normals, roles)
    // and a reopened run -- its roles filled in by the worker -- has the same counts as before.
    const contactSignature = JSON.stringify([
      loadedNames,
      resultSerial(state.result),
      state.showContacts,
      state.selectedGroup,
      state.selectedJoint,
      state.filters,
      state.hiddenOrigins,
    ]);
    if (force || contactSignature !== lastContacts) {
      lastContacts = contactSignature;
      // With the toggle off, the focused contact is still drawn (`contactOverlayPlan`).
      if (!run || (!state.showContacts && !state.selectedJoint)) clearContactOverlay();
      else {
        const box = viewport?.modelBounds() ?? null;
        const diagonal = box ? Math.hypot(box[3] - box[0], box[4] - box[1], box[5] - box[2]) : 0;
        renderContactOverlay(contactOverlayPlan(state, run, { modelDiagonal: diagonal }), originOf);
      }
    }

    const signature = JSON.stringify([
      file,
      // Which models are loaded: the markers sit at their origins, and isolation fades every one of
      // them (one loaded since must be faded too, and an unloaded one let go of).
      loadedNames,
      state.showMarkers,
      state.markerScale,
      state.selectedGroup,
      state.selectedJoint,
      state.isolate,
      state.isolateOpacity,
      state.filters,
      // Hiding a producer changes which dots belong on screen, so it has to wake this
      // subscription. Left out, the filter would edit the list and leave the 3D view showing
      // joints the list no longer has.
      state.hiddenOrigins,
      state.derivedKey,
      // WHICH result: two runs with as many joints are still two runs.
      resultSerial(state.result),
    ]);
    if (!force && signature === last) return;
    last = signature;
    renderJointMarkers(originOf, markersForState(state), state.markerScale);
    // Isolation follows the same cursor the markers do, from the same subscription: two
    // subscriptions could show a joint whose neighbours had been faded for a different one.
    if (run) {
      // By element id, in every loaded model: the members are draw ranges of their own models, and
      // every model without one is "the rest" too (`clashContactPlan`).
      const keep = contactKeepByPart(run, state.result, state.selectedJoint, state.selectedGroup);
      const targets = contactIsolationTargets(run, keep, loadedNames, loadedRefs);
      // Names remain the fallback for a one-model run whose model is loaded under another name.
      if (targets.length > 0 || keep === null || run.target.kind === "group") {
        isolateElementRanges(targets, state.isolate, state.isolateOpacity);
        return;
      }
    }
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
    if (s.loadedSourceName === prev.loadedSourceName && s.loadedSourceNames === prev.loadedSourceNames) return;
    // A model change takes the isolation down with the markers: what was faded belongs to the models
    // that were there, and leaving it would fade most of a model nobody isolated.
    clearJointIsolation();
    // Forced: the isolation was just taken down, and must be put back on what is loaded now.
    redraw(true);
  });
  // The three.js-free viewport takes a model after its load returned: its origin is known from then
  // (after the model's other listeners). Another viewport taking over draws them there.
  onModelLoaded(() => void setTimeout(() => redraw(true), 0));
  // Another viewport taking over draws them there: the manager draws the view again on it.
  redrawSync = redraw;
  redraw(true);
}

const serials = new WeakMap<object, number>();
let nextSerial = 1;

/** A number per result OBJECT, for a signature: each run, check or reopen makes a new one. */
function resultSerial(result: object | null): number {
  if (!result) return 0;
  let n = serials.get(result);
  if (n === undefined) serials.set(result, (n = nextSerial++));
  return n;
}
