// What a convex-contact RUN looks like in the scene -- which draw ranges of which loaded model its
// clashes are, and the overlay drawn for its contacts -- as pure functions of the run, the panel's
// cursor and filters, and the names of what is loaded. The THREE side (`clashIsolation`,
// `clashContactOverlay`, `clashJointMarkers`) only applies what these return, so every decision is
// testable without a scene.
//
// BY ELEMENT ID, PER MODEL. A run's members are GLB elements: in a run over one GLB (a file or a
// node) a member's id IS its element id, which is the draw-range key the scene draws it under; in a
// saved group's run a member is `(part, element)`, the part naming which model's GLB it came from.
// Names are what the result document carries, and they repeat -- within a model and across models --
// so isolating or selecting by name hides the wrong steel or none at all. And a group's run is no
// single loaded model, so "the checked source" names nothing that is loaded: which is why fading the
// rest did nothing for a group or selection run.
//
// ONE FRAME. Every point a run stores is in its GLB's own model frame (Z up, untranslated). The
// loaded models share one recentring offset (`modelStore.translation`, reused by every overlay load)
// and the same up-axis rule, so the frame of the first part's loaded model is every part's: that is
// the source the markers and the overlay take their transform from (`contactSpaceSource`).

import type { ContactRecordDoc, ContactRunDoc, ContactTarget } from "@/services/clash/contactRuns";
import { roleOf } from "@/services/clash/contactRuns";
import {
  filteredGroups,
  jointsForGroup,
  visibleResult,
  type ClashFilters,
  type ClashResult,
} from "@/state/clashCheckStore";
import { refOfSourceName, type GroupMemberTarget, type LoadedRef } from "@/utils/groups/savedGroups";

export type Vec3 = readonly [number, number, number];

/** The model a run's GLB is the viewer GLB of: a group part's member target, or the run's own target. */
export type RunSource = GroupMemberTarget | Extract<ContactTarget, { kind: "node" }>;

/** A run member as the scene knows it: which of the run's GLBs (`part`, 0 for a one-GLB run) and the
 *  element id -- its draw-range key there. */
export interface ElementRef {
  readonly part: number;
  readonly element: string;
}

/** The models a run measured, one per GLB, in part order. */
export function runSources(run: ContactRunDoc): RunSource[] {
  const t = run.target;
  if (t.kind === "group") return (run.geometry.parts ?? []).map((p) => p.source);
  if (t.kind === "file") return [{ kind: "file", source_key: t.source_key }];
  return [t];
}

/** Whether the model loaded under `sourceName` is `source`. A node matches by collection, subject and
 *  node -- and provider where the source names one -- not by revision, like a saved group's members
 *  (`rootMatchesTarget`). */
export function sourceMatches(source: RunSource, sourceName: string, loaded: readonly LoadedRef[]): boolean {
  if (source.kind === "file") return sourceName === source.source_key;
  const ref = refOfSourceName(sourceName, loaded);
  if (!ref) return false;
  const provider = "provider" in source ? source.provider : undefined;
  return (
    (!provider || ref.provider === provider) &&
    ref.collection === source.collection &&
    ref.subject === source.subject &&
    (ref.node ?? ref.subject) === (source.node ?? source.subject)
  );
}

/** For each part of the run, the loaded sources it is (usually one; none when its model is not loaded). */
export function loadedSourcesByPart(
  run: ContactRunDoc,
  loadedNames: Iterable<string>,
  loaded: readonly LoadedRef[],
): string[][] {
  const names = [...loadedNames];
  return runSources(run).map((s) => names.filter((n) => sourceMatches(s, n, loaded)));
}

/** The loaded source whose frame the run's points are drawn in: the first part that is loaded. */
export function contactSpaceSource(
  run: ContactRunDoc,
  loadedNames: Iterable<string>,
  loaded: readonly LoadedRef[],
): string | null {
  for (const names of loadedSourcesByPart(run, loadedNames, loaded)) if (names.length) return names[0];
  return null;
}

/** Member `k` of the run as `(part, element id)`, or null for a group member missing either. */
export function memberElement(run: ContactRunDoc, k: number): ElementRef | null {
  const m = run.members[k];
  if (!m) return null;
  if (run.target.kind !== "group") return { part: 0, element: m.id };
  return m.part !== undefined && m.element !== undefined ? { part: m.part, element: m.element } : null;
}

/** The two members of contact `contactId` as `(part, element id)`. */
export function contactMemberElements(run: ContactRunDoc, contactId: string): ElementRef[] {
  const c = run.contacts.find((x) => x.id === contactId);
  if (!c) return [];
  return c.members.flatMap((k) => {
    const e = memberElement(run, k);
    return e ? [e] : [];
  });
}

/** What isolation keeps, per part: the focused contact's members, else the open group's, else
 *  `null` -- nothing isolated (the same precedence as `isolationMembers`). */
export function contactKeepByPart(
  run: ContactRunDoc,
  result: ClashResult | null,
  selectedJoint: string | null,
  selectedGroup: string | null,
): Map<number, Set<string>> | null {
  if (!result) return null;
  let ids: readonly string[] = [];
  if (selectedJoint && result.jointsById.has(selectedJoint)) ids = [selectedJoint];
  else if (selectedGroup) ids = jointsForGroup(result, selectedGroup).map((j) => j.id);
  if (ids.length === 0) return null;
  const keep = new Map<number, Set<string>>();
  for (const id of ids) {
    for (const { part, element } of contactMemberElements(run, id)) {
      let set = keep.get(part);
      if (!set) keep.set(part, (set = new Set()));
      set.add(element);
    }
  }
  return keep.size ? keep : null;
}

/** One loaded model's isolation: the draw ranges to keep solid; every other range fades (or hides). */
export interface IsolationTarget {
  readonly sourceName: string;
  readonly keep: ReadonlySet<string>;
}

/** Isolation across EVERY loaded model: a model holding a kept member keeps those ranges, every other
 *  loaded model is faded whole -- they are all "the rest". Empty (isolate nothing) when no kept
 *  member is in a loaded model: hiding everything because nothing resolved would look broken. */
export function contactIsolationTargets(
  run: ContactRunDoc,
  keepByPart: ReadonlyMap<number, ReadonlySet<string>> | null,
  loadedNames: Iterable<string>,
  loaded: readonly LoadedRef[],
): IsolationTarget[] {
  if (!keepByPart) return [];
  const names = [...loadedNames];
  const byPart = loadedSourcesByPart(run, names, loaded);
  const keepOf = new Map<string, Set<string>>();
  for (const [part, elements] of keepByPart) {
    for (const name of byPart[part] ?? []) {
      let set = keepOf.get(name);
      if (!set) keepOf.set(name, (set = new Set()));
      for (const e of elements) set.add(e);
    }
  }
  if (keepOf.size === 0) return [];
  return names.map((sourceName) => ({ sourceName, keep: keepOf.get(sourceName) ?? new Set<string>() }));
}

/** Which loaded source and draw ranges are contact `contactId`'s members -- what a click selects. */
export function contactSceneElements(
  run: ContactRunDoc,
  contactIds: readonly string[],
  loadedNames: Iterable<string>,
  loaded: readonly LoadedRef[],
): { sourceName: string; element: string }[] {
  const byPart = loadedSourcesByPart(run, loadedNames, loaded);
  const out: { sourceName: string; element: string }[] = [];
  const seen = new Set<string>();
  for (const id of contactIds) {
    for (const { part, element } of contactMemberElements(run, id)) {
      for (const sourceName of byPart[part] ?? []) {
        const key = `${sourceName}\n${element}`;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ sourceName, element });
      }
    }
  }
  return out;
}

// ── the contacts overlay ────────────────────────────────────────────────────────────────────

/** Role colours -- the panel's legend reads these, so the two cannot disagree. Amber and teal are far
 *  apart in hue and in lightness for the common colour-vision deficiencies, and neither is the
 *  selection's blue; an unresolved side is a neutral grey. */
export const ROLE_COLOURS = {
  incoming: "#f59e0b",
  landing: "#14b8a6",
  unresolved: "#9ca3af",
} as const;
/** The contact normal's arrow. */
export const NORMAL_COLOUR = "#f8fafc";

export type ContactRole = "incoming" | "landing" | null;

export interface OverlayPolygon {
  /** Closed implicitly (last point back to the first), in the frame the plan was asked for. */
  readonly points: readonly Vec3[];
  readonly role: ContactRole;
  readonly color: string;
}

export interface OverlayContact {
  readonly id: string;
  /** Inside the open group / the focused one, or nothing selected: drawn full strength. */
  readonly emphasised: boolean;
  readonly focused: boolean;
  /** Each patch twice: the side on member i and the side on member j, in their roles' colours. */
  readonly polygons: readonly OverlayPolygon[];
  /** The contact normal, from the patch centroid, member i -> member j. */
  readonly arrow: { readonly from: Vec3; readonly to: Vec3 } | null;
}

/** A 4x4 column-major matrix (`THREE.Matrix4.elements`). */
export type Mat4 = ArrayLike<number>;

export function transformPoint(m: Mat4 | null | undefined, p: Vec3): Vec3 {
  if (!m) return p;
  const [x, y, z] = p;
  const w = m[3] * x + m[7] * y + m[11] * z + m[15] || 1;
  return [
    (m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
    (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
    (m[2] * x + m[6] * y + m[10] * z + m[14]) / w,
  ];
}

const add = (a: Vec3, b: Vec3, s = 1): Vec3 => [a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s];
const mid = (a: Vec3, b: Vec3): Vec3 => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2];
const dist = (a: Vec3, b: Vec3) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

function unit(v: Vec3): Vec3 | null {
  const n = Math.hypot(v[0], v[1], v[2]);
  return n > 0 ? [v[0] / n, v[1] / n, v[2] / n] : null;
}

function centroid(points: readonly Vec3[]): Vec3 {
  const s = points.reduce<Vec3>((acc, p) => add(acc, p), [0, 0, 0]);
  return [s[0] / points.length, s[1] / points.length, s[2] / points.length];
}

function spread(points: readonly Vec3[]): number {
  let d = 0;
  for (let a = 0; a < points.length; a++) for (let b = a + 1; b < points.length; b++) d = Math.max(d, dist(points[a], points[b]));
  return d;
}

/** A closed polygon as stored repeats its first point last; drawn closed implicitly, so dropped. */
function open(points: readonly (readonly number[])[]): Vec3[] {
  const pts = points.map((p) => [p[0], p[1], p[2]] as Vec3);
  if (pts.length > 1 && dist(pts[0], pts[pts.length - 1]) === 0) pts.pop();
  return pts;
}

export function roleColour(role: ContactRole): string {
  return role === "incoming" ? ROLE_COLOURS.incoming : role === "landing" ? ROLE_COLOURS.landing : ROLE_COLOURS.unresolved;
}

/** Arrow length: proportional to the patch, kept between 1% and 5% of the model -- a fixed length is
 *  invisible on a plant and swallows a bracket. */
export function arrowLength(patchSize: number, modelDiagonal: number): number {
  const lo = modelDiagonal > 0 ? modelDiagonal * 0.01 : 0;
  const hi = modelDiagonal > 0 ? modelDiagonal * 0.05 : Infinity;
  const want = patchSize > 0 ? patchSize * 1.5 : modelDiagonal * 0.02;
  return Math.min(hi, Math.max(lo, want || 0.1));
}

/** One contact's overlay geometry, in the frame `matrix` maps the run's points to (model frame when
 *  omitted).
 *
 *  WHAT IS DRAWN, AND WHY TWO POLYGONS. A patch is stored twice -- `points1` on member i, `points2` on
 *  member j -- and the two lie within the security margin of each other, so drawn as they are they
 *  z-fight into noise. One midpoint polygon would read cleanly but say nothing about which member is
 *  which. So each patch is drawn as its MIDPOINT polygon twice, nudged a hair (1% of the patch) off
 *  the contact plane towards its own member and filled in that member's role colour: from either
 *  side you see the colour of the member on that side, edge-on a two-tone sandwich, never a flicker.
 *  The normal is drawn as measured (member i -> member j) from the patch centroid. */
export function contactOverlayGeometry(
  c: ContactRecordDoc,
  opts: { modelDiagonal: number; matrix?: Mat4 | null },
): Pick<OverlayContact, "polygons" | "arrow"> {
  const [i, j] = c.members;
  const roleI = roleOf(c, i);
  const roleJ = roleOf(c, j);
  const n = unit(c.normal as unknown as Vec3) ?? ([0, 0, 1] as Vec3);
  const polygons: OverlayPolygon[] = [];
  const centres: Vec3[] = [];
  let size = 0;
  for (const p of c.patches) {
    const a = open(p.points1);
    const b = open(p.points2);
    const midPoly = a.length === b.length && a.length > 0 ? a.map((q, k) => mid(q, b[k])) : a.length ? a : b;
    if (midPoly.length < 3) continue;
    const pn = unit(p.normal as unknown as Vec3) ?? n;
    const s = spread(midPoly);
    size = Math.max(size, s);
    centres.push(centroid(midPoly));
    const nudge = Math.max(s * 0.01, 1e-5);
    // The normal points from i to j: member i's side is behind the plane, j's in front.
    polygons.push({ points: midPoly.map((q) => transformPoint(opts.matrix, add(q, pn, -nudge))), role: roleI, color: roleColour(roleI) });
    polygons.push({ points: midPoly.map((q) => transformPoint(opts.matrix, add(q, pn, nudge))), role: roleJ, color: roleColour(roleJ) });
  }
  const [a, b] = c.near_points as unknown as [Vec3, Vec3];
  const from = centres.length ? centroid(centres) : mid(a, b);
  const length = arrowLength(size, opts.modelDiagonal);
  return {
    polygons,
    arrow: { from: transformPoint(opts.matrix, from), to: transformPoint(opts.matrix, add(from, n, length)) },
  };
}

/** Every contact the panel shows, as overlay geometry -- through the SAME filters as the rows and the
 *  markers (`visibleResult` for hidden producers, `filteredGroups` for the filter bar), so the 3D view
 *  never shows a contact the list has hidden. With the toggle off, only the focused contact: picking a
 *  clash from the list is asking to see that contact, and its patch and normal are what it is. */
export function contactOverlayPlan(
  state: {
    result: ClashResult | null;
    filters: ClashFilters;
    hiddenOrigins: readonly string[];
    selectedGroup: string | null;
    selectedJoint: string | null;
    showContacts: boolean;
  },
  run: ContactRunDoc | null,
  opts: { modelDiagonal: number; matrix?: Mat4 | null },
): OverlayContact[] {
  const result = visibleResult(state);
  if (!run || !result) return [];
  if (!state.showContacts && !state.selectedJoint) return [];
  const shown = new Set(filteredGroups(result, state.filters).map((g) => g.typeKey));
  const typeKeyOf = new Map(result.joints.map((j) => [j.id, j.typeKey] as const));
  const out: OverlayContact[] = [];
  for (const c of run.contacts) {
    if (!state.showContacts && c.id !== state.selectedJoint) continue;
    const typeKey = typeKeyOf.get(c.id);
    if (typeKey === undefined || !shown.has(typeKey)) continue;
    const focused = state.selectedJoint === c.id;
    const emphasised = state.selectedJoint
      ? focused
      : state.selectedGroup === null || typeKey === state.selectedGroup;
    out.push({ id: c.id, emphasised, focused, ...contactOverlayGeometry(c, opts) });
  }
  return out;
}
