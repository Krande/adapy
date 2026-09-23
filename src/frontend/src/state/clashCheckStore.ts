// The Scene panel's "Clashes" tab: run a clash check over the loaded model's SOURCE, read back
// `ada.clash/result@1`, and hand a chosen group off to a generator.
//
// ONE RESULT, READ BY EVERYTHING. Every badge, count and filter the panel shows is a pure
// function of the single `result` object this store holds -- never per-row state, never
// recomputed independently by two call sites. That is the same discipline `@/assets/assetView`
// holds for the asset browser (`notes_core_asset_browser.md` §Verification "One derived view"),
// and it is why every derivation below (`groupFacets`, `filteredGroups`, `isSpecAvailable`,
// `noMembersSentence`, `jointsForGroup`) is a plain function of `(result, ...)` rather than a
// second piece of state the store would have to keep in sync with the first.
//
// WHY IDENTIFICATION AND REVIEW SHARE NOTHING HERE BUT THE RESULT SCHEMA. This store is the
// "identify" half of the workflow; the "review" half is the existing `Joints` tab
// (`JointsOverviewPanel.tsx`), which reads this store's `result.joints` ALONGSIDE the
// take-off's produced joints rather than duplicating a table. `Clashes` -> `Joints` is one
// workflow, not two features that happen to look similar (Decision 10's naming paragraph).

import { create } from "zustand";

import {
  clashCheckApi,
  type ClashCheckOptions,
  type ClashCheckResponse,
  type ClashDetailResponse,
  type WireClashApplicableSpec,
  type WireClashGroup,
  type WireClashJoint,
  type WireClashResult,
} from "@/services/api/clashCheck";
import { conversionApi } from "@/services/api/conversion";
import { trackJob } from "@/services/jobTracking";

// ---------------------------------------------------------------------------------------------
// The browser's model. Camel-cased, `applicable` normalised (`capability` stays `null` rather
// than `undefined` so every reader tests one falsy shape, not two), and `jointsById` precomputed
// ONCE at parse time -- every group-level derivation below reads through it instead of
// rescanning `joints`, so two call sites deriving the same fact can never disagree.
// ---------------------------------------------------------------------------------------------

export interface ClashApplicableSpec {
  readonly spec: string;
  /** `null` = a built-in spec, runs in core's default pool, always offered
   *  (`ada/clash/builtin_specs.py`). A string names the capability an out-of-tree spec needs a
   *  live pool to advertise -- see `isSpecAvailable`. */
  readonly capability: string | null;
  readonly tags: readonly string[];
  readonly priority: number;
}

export interface ClashJointMember {
  readonly name: string;
  readonly kind: string; // "BEAM" | "PLATE"
  readonly guid: string | null;
  readonly section: string | null;
  readonly memberType: string | null;
}

export interface ClashJoint {
  readonly id: string;
  readonly centre: readonly [number, number, number];
  readonly members: readonly ClashJointMember[];
  readonly typeKey: string;
  readonly typeLabel: string;
  readonly applicable: readonly ClashApplicableSpec[];
}

export interface ClashGroup {
  readonly typeKey: string;
  readonly typeLabel: string;
  readonly count: number;
  readonly jointIds: readonly string[];
  readonly applicable: readonly ClashApplicableSpec[];
}

export interface ClashResult {
  readonly schema: string;
  readonly sourceKey: string;
  readonly sourceSha256: string | null;
  readonly options: Readonly<Record<string, unknown>>;
  /** OMITS what was not measured -- see the module doc on `WireClashResult`. Read through
   *  `"members" in counts` before comparing to zero: an ABSENT key means a pass never ran, a
   *  PRESENT zero means it ran and found nothing. */
  readonly counts: Readonly<Record<string, number>>;
  readonly joints: readonly ClashJoint[];
  readonly groups: readonly ClashGroup[];
  readonly provenance: Readonly<Record<string, unknown>>;
  readonly warnings: readonly string[];
  readonly jointsById: ReadonlyMap<string, ClashJoint>;
}

export class ClashResultError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ClashResultError";
  }
}

const CLASH_RESULT_SCHEMA = "ada.clash/result@1";

function parseApplicable(entries: readonly WireClashApplicableSpec[] | undefined): readonly ClashApplicableSpec[] {
  return (entries ?? []).map((a) => ({
    spec: a.spec,
    capability: a.capability ?? null,
    tags: a.tags ?? [],
    priority: a.priority ?? 0,
  }));
}

/** Parse a result document. An unknown schema is REFUSED, never partially read -- the same rule
 *  `parseBuildSummary`/`parseHierarchy` hold elsewhere in this viewer: a document core cannot
 *  fully understand is not "mostly fine" (mirrors `ada.clash.result.parse_clash_result`). */
export function parseClashResult(doc: unknown): ClashResult {
  if (typeof doc !== "object" || doc === null || Array.isArray(doc)) {
    throw new ClashResultError(`clash result must be a JSON object, got ${doc === null ? "null" : typeof doc}`);
  }
  const raw = doc as Partial<WireClashResult>;
  if (raw.schema !== CLASH_RESULT_SCHEMA) {
    throw new ClashResultError(
      `unknown clash result schema ${JSON.stringify(raw.schema)}: this viewer reads ` +
        `${JSON.stringify(CLASH_RESULT_SCHEMA)} only. Refusing rather than reading the fields it recognises.`,
    );
  }
  const joints: ClashJoint[] = (raw.joints ?? []).map((j: WireClashJoint) => ({
    id: j.id,
    centre: j.centre,
    members: (j.members ?? []).map((m) => ({
      name: m.name,
      kind: m.kind,
      guid: m.guid ?? null,
      section: m.section ?? null,
      memberType: m.member_type ?? null,
    })),
    typeKey: j.type_key,
    typeLabel: j.type_label,
    applicable: parseApplicable(j.applicable),
  }));
  const jointsById = new Map(joints.map((j) => [j.id, j] as const));
  const groups: ClashGroup[] = (raw.groups ?? []).map((g: WireClashGroup) => ({
    typeKey: g.type_key,
    typeLabel: g.type_label,
    count: g.count,
    jointIds: g.joint_ids ?? [],
    applicable: parseApplicable(g.applicable),
  }));
  return {
    schema: raw.schema,
    sourceKey: raw.source_key ?? "",
    sourceSha256: raw.source_sha256 ?? null,
    options: raw.options ?? {},
    counts: raw.counts ?? {},
    joints,
    groups,
    provenance: raw.provenance ?? {},
    warnings: raw.warnings ?? [],
    jointsById,
  };
}

// ---------------------------------------------------------------------------------------------
// Pure derivations. Every one takes a `ClashResult` (plus, where relevant, the current filters)
// and returns an answer -- nothing here is stored, nothing here is recomputed differently by two
// callers. Call these from a component's `useMemo`, the same way `AssetsTab` derives `view`.
// ---------------------------------------------------------------------------------------------

/** The joints belonging to one group, in the group's own order. Defensive against a joint id a
 *  group names but `joints[]` does not carry (a malformed document) -- filtered out rather than
 *  thrown, because one missing joint must not blank the whole group. */
export function jointsForGroup(result: ClashResult, typeKey: string): readonly ClashJoint[] {
  const group = result.groups.find((g) => g.typeKey === typeKey);
  if (!group) return [];
  const out: ClashJoint[] = [];
  for (const id of group.jointIds) {
    const joint = result.jointsById.get(id);
    if (joint) out.push(joint);
  }
  return out;
}

/** Every member name a group's joints touch, deduplicated -- what a "select all" for a group
 *  hands to `selectInOtherModel`. */
export function memberNamesForGroup(result: ClashResult, typeKey: string): readonly string[] {
  const names = new Set<string>();
  for (const joint of jointsForGroup(result, typeKey)) {
    for (const m of joint.members) names.add(m.name);
  }
  return [...names];
}

export interface ClashGroupFacets {
  readonly kinds: readonly string[];
  readonly sectionFamilies: readonly string[];
  readonly memberTypes: readonly string[];
}

/** The section families / member types / kinds present across one group's joints -- read
 *  straight off the structured `members[]` the result already carries (never by re-parsing the
 *  opaque `typeKey` string, which would be a second, easier-to-desync copy of the same fact). */
export function groupFacets(result: ClashResult, typeKey: string): ClashGroupFacets {
  const kinds = new Set<string>();
  const sectionFamilies = new Set<string>();
  const memberTypes = new Set<string>();
  for (const joint of jointsForGroup(result, typeKey)) {
    for (const m of joint.members) {
      kinds.add(m.kind);
      if (m.section) sectionFamilies.add(m.section);
      if (m.memberType) memberTypes.add(m.memberType);
    }
  }
  return {
    kinds: [...kinds].sort(),
    sectionFamilies: [...sectionFamilies].sort(),
    memberTypes: [...memberTypes].sort(),
  };
}

/** The full set of filter options across every group in the result -- feeds the filter
 *  dropdowns, so they only ever offer a value that can actually narrow something. */
export function facetOptions(result: ClashResult): ClashGroupFacets {
  const sectionFamilies = new Set<string>();
  const memberTypes = new Set<string>();
  const kinds = new Set<string>();
  for (const joint of result.joints) {
    for (const m of joint.members) {
      kinds.add(m.kind);
      if (m.section) sectionFamilies.add(m.section);
      if (m.memberType) memberTypes.add(m.memberType);
    }
  }
  return { kinds: [...kinds].sort(), sectionFamilies: [...sectionFamilies].sort(), memberTypes: [...memberTypes].sort() };
}

export interface ClashFilters {
  /** Exact match against a group's own `typeKey` -- narrows to one row. */
  readonly typeKey: string | null;
  readonly sectionFamily: string | null;
  readonly memberType: string | null;
  readonly applicable: "any" | "matched" | "unmatched";
}

export const DEFAULT_CLASH_FILTERS: ClashFilters = {
  typeKey: null,
  sectionFamily: null,
  memberType: null,
  applicable: "any",
};

/** Rows are GROUPS, not joints -- a frame has hundreds of joints and a handful of KINDS of
 *  joint, and the kinds are what a person decides about (Decision 10, `result.py`'s module doc).
 *  Filtering therefore narrows which ROWS show, never which joints exist inside a shown row. */
export function filteredGroups(result: ClashResult, filters: ClashFilters): readonly ClashGroup[] {
  return result.groups.filter((g) => {
    if (filters.typeKey && g.typeKey !== filters.typeKey) return false;
    if (filters.applicable === "matched" && g.applicable.length === 0) return false;
    if (filters.applicable === "unmatched" && g.applicable.length > 0) return false;
    if (filters.sectionFamily || filters.memberType) {
      const facets = groupFacets(result, g.typeKey);
      if (filters.sectionFamily && !facets.sectionFamilies.includes(filters.sectionFamily)) return false;
      if (filters.memberType && !facets.memberTypes.includes(filters.memberType)) return false;
    }
    return true;
  });
}

/** `counts.members === 0` is a SUCCESSFUL answer ("this source has no beams or plates"), never
 *  an error state -- `identify_joints` in `ada/clash/identify.py` returns it for a STEP file or a
 *  shapes-only IFC read on purpose, with the sentence in `warnings[0]`. Returns `null` when the
 *  check found members (or never ran), so a caller can render the sentence in place of the
 *  groups table exactly when this is non-null and nothing else. */
export function noMembersSentence(result: ClashResult): string | null {
  if (result.counts.members !== 0) return null;
  return (
    result.warnings[0] ??
    "This source has no beams or plates, so there is nothing to find joints between."
  );
}

// ---------------------------------------------------------------------------------------------
// Group colour + the 3D marker plan. A joint is a POINT in the model (its `centre`), and the
// panel is a list of groups; without something drawn at those points a user reading a row has no
// way to tell WHERE in the frame the 48 joints it counts actually are. So the same grouping that
// orders the rows also colours a sphere per joint, and both read the ONE function below -- a row
// whose swatch disagreed with its spheres would be worse than no swatch at all.
// ---------------------------------------------------------------------------------------------

/** A group's colour, as a CSS/THREE-parsable hex string.
 *
 *  Derived from the group's INDEX in `result.groups`, which `group_joints` already sorted
 *  (largest first, then type key) -- so the colour is stable for a given result and needs no
 *  stored palette. Hues walk by the golden angle, which keeps neighbouring rows apart even when a
 *  model produces a dozen groups. */
export function groupColor(result: ClashResult, typeKey: string): string {
  const index = result.groups.findIndex((g) => g.typeKey === typeKey);
  return hueColor(index < 0 ? 0 : index);
}

const GOLDEN_ANGLE_DEG = 137.508;

/** `index -> #rrggbb`, walking hue by the golden angle at a fixed saturation/lightness picked to
 *  read on the dark canvas AND on the panel's dark rows. */
export function hueColor(index: number): string {
  const hue = ((index * GOLDEN_ANGLE_DEG) % 360) / 360;
  const [r, g, b] = hslToRgb(hue, 0.62, 0.58);
  return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const f = (n: number) => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)))));
  };
  return [f(0), f(8), f(4)];
}

export interface JointMarker {
  readonly id: string;
  readonly typeKey: string;
  readonly centre: readonly [number, number, number];
  readonly color: string;
  /** Relative size, 1 for a normal marker. A selected group's markers are drawn larger and the
   *  rest smaller, so "which of these am I looking at" is answerable without hiding anything --
   *  hiding the others would lose the only view that shows how a group sits in the whole frame. */
  readonly scale: number;
  /** False for a joint outside the selected group: the caller dims it rather than dropping it. */
  readonly emphasised: boolean;
  /** The one joint the cursor is on, if any -- drawn largest, so arrowing down the list moves a
   *  visible dot through the model. */
  readonly focused: boolean;
}

/** One marker per joint, in result order, coloured by group.
 *
 *  `visibleTypeKeys` is the set of groups the panel's FILTERS currently leave on screen -- a
 *  marker for a row the user has filtered away would contradict the list beside it. `null` means
 *  no filter is active (every group shows). `highlight` is the expanded group, if any. */
export function jointMarkers(
  result: ClashResult,
  opts?: { visibleTypeKeys?: ReadonlySet<string> | null; highlight?: string | null; focus?: string | null },
): readonly JointMarker[] {
  const visible = opts?.visibleTypeKeys ?? null;
  const highlight = opts?.highlight ?? null;
  const focus = opts?.focus ?? null;
  const out: JointMarker[] = [];
  for (const joint of result.joints) {
    if (visible && !visible.has(joint.typeKey)) continue;
    const emphasised = highlight === null || joint.typeKey === highlight;
    const focused = focus !== null && joint.id === focus;
    out.push({
      id: joint.id,
      typeKey: joint.typeKey,
      centre: joint.centre,
      color: groupColor(result, joint.typeKey),
      scale: focused ? 2.4 : highlight === null ? 1 : emphasised ? 1.6 : 0.6,
      emphasised: emphasised || focused,
      focused,
    });
  }
  return out;
}

/** The spec each joint would be detailed WITH: its highest-priority applicable spec, ties broken
 *  by name so the answer is stable. `null` for a joint no registered spec binds. */
export function specForJoint(joint: ClashJoint): ClashApplicableSpec | null {
  if (joint.applicable.length === 0) return null;
  return [...joint.applicable].sort((a, b) => b.priority - a.priority || a.spec.localeCompare(b.spec))[0];
}

/** "Generate detail model" as JOBS: one batch per spec, each joint in exactly one of them.
 *
 *  A joint with two applicable specs must not be detailed twice -- that is two overlapping bodies
 *  at one joint, and nothing downstream could tell which was meant. So each joint goes to its
 *  highest-priority spec only (`specForJoint`), and a joint no spec binds is left out entirely
 *  rather than queued against a spec that would refuse it.
 *
 *  `only` restricts the run to a subset (one joint's button, or the joints a filter leaves on
 *  screen); omitted means every joint in the result. */
export function detailBatches(
  result: ClashResult,
  only?: ReadonlySet<string> | readonly string[] | null,
): readonly { spec: ClashApplicableSpec; jointIds: readonly string[] }[] {
  const limit = only == null ? null : only instanceof Set ? only : new Set(only);
  const bySpec = new Map<string, { spec: ClashApplicableSpec; jointIds: string[] }>();
  for (const joint of result.joints) {
    if (limit && !limit.has(joint.id)) continue;
    const spec = specForJoint(joint);
    if (!spec) continue;
    const bucket = bySpec.get(spec.spec) ?? { spec, jointIds: [] };
    bucket.jointIds.push(joint.id);
    bySpec.set(spec.spec, bucket);
  }
  return [...bySpec.values()].sort((a, b) => b.spec.priority - a.spec.priority || a.spec.spec.localeCompare(b.spec.spec));
}

/** What "isolate" leaves visible: the focused joint's members, else the open group's, else
 *  nothing -- and nothing means no isolation at all, not an empty model.
 *
 *  The focused joint wins over its group on purpose: arrowing down the list is a walk through
 *  individual joints, and isolating the whole group while the cursor is on one of its joints
 *  would make every step look the same. */
export function isolationMembers(
  result: ClashResult | null,
  selectedJoint: string | null,
  selectedGroup: string | null,
): readonly string[] {
  if (!result) return [];
  if (selectedJoint) {
    const joint = result.jointsById.get(selectedJoint);
    if (joint) return joint.members.map((m) => m.name);
  }
  if (selectedGroup) return memberNamesForGroup(result, selectedGroup);
  return [];
}

/** The loaded source a result's members live in -- the model the CHECK ran against, never
 *  whatever was loaded last.
 *
 *  Detailing a group loads the produced joints as a SECOND source, which makes them the loaded
 *  source. Every selection is resolved by member name against one loaded model, so asking the
 *  overlay for `Girder_04` finds nothing: after a generate run, clicking a row or a marker
 *  silently highlighted nothing at all. The result knows which model it was run against, so that
 *  is what answers this -- `loaded` is only the fallback for the window before a check has run.
 */
export function checkedSourceName(sourceName: string | null, loaded: string | null): string | null {
  return sourceName ?? loaded;
}

/** Whether a spec should be OFFERED, never whether it would succeed.
 *
 *  `capability === null` -- a built-in spec, runs in core's default pool, always offered.
 *
 *  `capability` a string -- an out-of-tree spec, routed to whichever pool currently advertises
 *  that capability. `liveCapabilities` is the live `connection_specs` union (Decision 10 item
 *  4); when it is known, a capability absent from it is shown as UNAVAILABLE rather than
 *  silently offered, because handing a worker a job no pool will ever pick up is a spinner that
 *  never resolves, not a failure that surfaces.
 *
 *  `liveCapabilities === null` -- the live union route does not exist yet in this deployment
 *  (`services/api/clashCheck.ts`'s TODO). There is then no way to tell "no live pool" apart from
 *  "nobody has checked", so this fails OPEN: a capability-bearing spec is offered rather than
 *  hidden. Once the route lands, callers pass the real set and this branch stops firing. */
export function isSpecAvailable(spec: ClashApplicableSpec, liveCapabilities: ReadonlySet<string> | null): boolean {
  if (spec.capability === null) return true;
  if (liveCapabilities === null) return true;
  return liveCapabilities.has(spec.capability);
}

// ---------------------------------------------------------------------------------------------
// The PRODUCED take-off. A detail run writes two documents: the GLB the scene overlays, and a
// `result.stats.json` holding the same joints take-off a compiled model carries
// (`_joints_takeoff`). The Joints tab's "produced" section has always read that take-off from the
// loaded model's own stats -- which a run-time overlay does not have, so a freshly generated
// detail model showed its geometry and an empty table. These read the run's document instead.
// ---------------------------------------------------------------------------------------------

export interface ProducedJointType {
  readonly slug: string;
  readonly name: string;
  readonly count: number;
}

export interface ProducedJointItem {
  readonly name: string;
  readonly slug: string;
  readonly type: string;
  readonly members: readonly string[];
  readonly plates: number;
  readonly welds: number;
  readonly centre: readonly [number, number, number] | null;
}

export interface ProducedJoints {
  readonly count: number;
  readonly by_type: readonly ProducedJointType[];
  readonly items: readonly ProducedJointItem[];
}

export interface DetailStats {
  readonly joints: ProducedJoints | null;
  /** Joints the builder refused, as `"<joint id>: <reason>"`. A run reports these rather than
   *  failing: the other joints were still built, and hiding the refusals would make a take-off of
   *  11 joints out of 12 look like a complete answer. */
  readonly skipped: readonly string[];
}

/** Read a detail run's `result.stats.json`. Shape-checked field by field: this is a document from
 *  a worker, and a take-off that half-parsed would put made-up counts in front of a user. */
export function parseDetailStats(doc: unknown): DetailStats {
  if (typeof doc !== "object" || doc === null || Array.isArray(doc)) return { joints: null, skipped: [] };
  const raw = doc as { joints?: unknown; skipped?: unknown };
  const skipped = Array.isArray(raw.skipped) ? raw.skipped.map((x) => String(x)) : [];
  const j = raw.joints;
  if (typeof j !== "object" || j === null) return { joints: null, skipped };
  const t = j as { count?: unknown; by_type?: unknown; items?: unknown };
  const by_type: ProducedJointType[] = (Array.isArray(t.by_type) ? t.by_type : []).map((r) => {
    const row = r as Record<string, unknown>;
    return { slug: String(row.slug ?? ""), name: String(row.name ?? row.slug ?? ""), count: Number(row.count ?? 0) };
  });
  const items: ProducedJointItem[] = (Array.isArray(t.items) ? t.items : []).map((r) => {
    const row = r as Record<string, unknown>;
    const centre = Array.isArray(row.centre) && row.centre.length === 3 ? row.centre.map(Number) : null;
    return {
      name: String(row.name ?? ""),
      slug: String(row.slug ?? ""),
      type: String(row.type ?? row.slug ?? ""),
      members: Array.isArray(row.members) ? row.members.map((m) => String(m)) : [],
      plates: Number(row.plates ?? 0),
      welds: Number(row.welds ?? 0),
      centre: centre as [number, number, number] | null,
    };
  });
  return { joints: { count: Number(t.count ?? items.length), by_type, items }, skipped };
}

/** Fold a second take-off into the first -- "generate detail model" is one job PER SPEC, and the
 *  panel shows one table, so the runs have to add up. Types merge by slug, items by name (a
 *  repeat run of the same spec rebuilds the same joints, and showing them twice would double the
 *  count a person reads off the table). */
export function mergeProducedJoints(a: ProducedJoints | null, b: ProducedJoints | null): ProducedJoints | null {
  if (!a) return b;
  if (!b) return a;
  const byType = new Map<string, ProducedJointType>();
  for (const row of [...a.by_type, ...b.by_type]) {
    const seen = byType.get(row.slug);
    byType.set(row.slug, seen ? { ...seen, count: seen.count + row.count } : row);
  }
  const items = new Map<string, ProducedJointItem>();
  for (const item of [...a.items, ...b.items]) items.set(item.name, item);
  // The counts have to agree with the rows: recount from the de-duplicated items rather than
  // adding the two `count` fields, which would double-count a rerun.
  const deduped = [...items.values()];
  const typeCounts = new Map<string, number>();
  for (const item of deduped) typeCounts.set(item.slug, (typeCounts.get(item.slug) ?? 0) + 1);
  return {
    count: deduped.length,
    by_type: [...byType.values()].map((row) => ({ ...row, count: typeCounts.get(row.slug) ?? row.count })),
    items: deduped,
  };
}

// ---------------------------------------------------------------------------------------------
// Job orchestration. Pure aside from the injected `api`/`trackJob`/`wait`/`now`, so it runs under
// `node --test` against fakes -- no network, mirrors `assets/delivery.ts`'s `loadNode` split.
// ---------------------------------------------------------------------------------------------

export interface ClashFlowApi {
  runClashCheck(scope: string, body: { source_key: string; options?: ClashCheckOptions }): Promise<ClashCheckResponse>;
  runClashDetail(
    scope: string,
    body: { result_key: string; joint_ids: readonly string[]; spec: string; options?: Record<string, unknown> },
  ): Promise<ClashDetailResponse>;
  getClashResult(scope: string, key: string): Promise<unknown>;
  getDetailStats(scope: string, key: string): Promise<unknown>;
  jobStatus(jobId: string): Promise<{ status: string; error: string | null }>;
}

export interface ClashFlowDeps {
  api: ClashFlowApi;
  trackJob?: (opts: { jobId: string; label: string; derivedKey?: string }) => void;
  /** Poll backoff. Defaults to a real 1.5s timer; tests inject an instant no-op. */
  wait?: (ms: number) => Promise<void>;
  now?: () => number;
}

const POLL_INTERVAL_MS = 1500;
/** Same 10-minute ceiling `assets/delivery.ts` uses, for the same reason: a capability no live
 *  pool advertises is an ordinary deployment state, not a bug, and an unbounded poll would render
 *  that as a spinner that never resolves. */
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

async function pollToTerminal(deps: ClashFlowDeps, jobId: string, label: string): Promise<void> {
  const wait = deps.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = deps.now ?? (() => Date.now());
  const startedAt = now();
  for (;;) {
    const status = await deps.api.jobStatus(jobId);
    if (status.status === "done") return;
    if (status.status === "error" || status.status === "cancelled") {
      throw new ClashResultError(`${label} ${status.status}${status.error ? `: ${status.error}` : ""}`);
    }
    if (now() - startedAt >= POLL_TIMEOUT_MS) {
      throw new ClashResultError(
        `${label} is still ${status.status} after ${Math.round(POLL_TIMEOUT_MS / 60000)} min. The job was not ` +
          `cancelled -- running the check again will pick up its result if it does finish.`,
      );
    }
    await wait(POLL_INTERVAL_MS);
  }
}

/** Run a clash check (or read a cached one) and return the parsed result. `cached: true` on the
 *  response means the result already sits at `derived_key` -- NOTHING is enqueued, which is
 *  what lets a repeat run be a cache hit rather than a job (pinned by a test). */
export async function runClashCheckFlow(
  deps: ClashFlowDeps,
  scope: string,
  sourceKey: string,
  options: ClashCheckOptions,
): Promise<{ result: ClashResult; derivedKey: string; cached: boolean }> {
  const resp = await deps.api.runClashCheck(scope, { source_key: sourceKey, options });
  if (!resp.cached) {
    if (!resp.job_id) {
      throw new ClashResultError("clash-check reported neither a cached result nor a job id");
    }
    deps.trackJob?.({ jobId: resp.job_id, label: `Clash check: ${sourceKey}`, derivedKey: resp.derived_key });
    await pollToTerminal(deps, resp.job_id, "clash check");
  }
  const doc = await deps.api.getClashResult(scope, resp.derived_key);
  const result = parseClashResult(doc);
  return { result, derivedKey: resp.derived_key, cached: resp.cached };
}

/** Hand a group's joints off to one of its `applicable` specs. Returns the derived key of the
 *  overlay GLB (+ `.stats.json`) the caller should load with `overlay_file_in_scene` -- loading
 *  into the scene is deliberately NOT this module's job, the same split `assets/delivery.ts`
 *  keeps between fetching/validating a build and `SceneHandle.loadModelFromUrl` actually loading
 *  it. */
export async function runClashDetailFlow(
  deps: ClashFlowDeps,
  scope: string,
  resultKey: string,
  jointIds: readonly string[],
  spec: string,
  options?: Record<string, unknown>,
): Promise<{ derivedKey: string; glbKey: string | null; cached: boolean; stats: DetailStats }> {
  const resp = await deps.api.runClashDetail(scope, { result_key: resultKey, joint_ids: jointIds, spec, options });
  const cached = resp.cached ?? false;
  if (!cached) {
    if (!resp.job_id) {
      throw new ClashResultError("clash-detail reported neither a cached result nor a job id");
    }
    deps.trackJob?.({ jobId: resp.job_id, label: `Detail: ${spec}`, derivedKey: resp.derived_key });
    await pollToTerminal(deps, resp.job_id, `detail (${spec})`);
  }
  // The take-off is part of what a detail run PRODUCED, so it is read here rather than by the
  // panel: a surface that fetched it itself could show a table for a run whose document had not
  // been written yet. A document that cannot be read is not a failed run -- the geometry is
  // built and loadable -- so this degrades to an empty take-off rather than throwing.
  let stats: DetailStats = { joints: null, skipped: [] };
  try {
    stats = parseDetailStats(await deps.api.getDetailStats(scope, resp.derived_key));
  } catch {
    stats = { joints: null, skipped: [] };
  }
  return { derivedKey: resp.derived_key, glbKey: resp.glb_key ?? null, cached, stats };
}

// ---------------------------------------------------------------------------------------------
// The store. Thin: state plus actions that assemble the REAL deps (the REST client, the global
// job toast) and call the pure flows above. `ClashesPanel.tsx` reads state and derives its view
// with the pure functions above in a `useMemo`, never by adding new store fields for a badge.
// ---------------------------------------------------------------------------------------------

interface ClashCheckState {
  /** The scene source name the check was run against, for display only. */
  sourceName: string | null;
  /** The model's SOURCE key -- what the check actually reads (never the GLB). */
  sourceKey: string | null;
  options: ClashCheckOptions;
  filters: ClashFilters;
  selectedGroup: string | null;
  selectedJoints: readonly string[];
  /** The ONE joint under the cursor -- set by clicking its marker in the 3D view, by clicking its
   *  row, and by arrowing through the list. Its group is opened with it, because a cursor on a
   *  joint inside a collapsed group would be a selection with nothing on screen to show it. */
  selectedJoint: string | null;
  /** What happens to the members that are NOT part of what the cursor is on: nothing (`off`),
   *  faded to a translucent ghost, or hidden outright. A joint is a few members inside thousands
   *  and is usually behind something, so this is how it gets looked at.
   *
   *  Defaults to `ghost`, not `off`: a joint picked out of a frame is nearly always occluded, and
   *  the fade is the reading that keeps the rest of the model as context rather than removing it.
   *  It costs nothing when nothing is selected -- with no cursor there is nothing to isolate. */
  isolate: "off" | "ghost" | "hidden";
  /** How faint the ghost is, 0-1. Adjustable because a fixed value is wrong at both ends:
   *  unusable on a dense deck, pointless on a bare frame. */
  isolateOpacity: number;
  /** Whether the joint markers are drawn in the 3D scene. Held here rather than in the panel so
   *  the overlay survives a tab switch -- the markers are a view of the RESULT, not of the panel
   *  being mounted. */
  showMarkers: boolean;

  jobId: string | null;
  derivedKey: string | null;
  cached: boolean;
  result: ClashResult | null;
  busy: boolean;
  error: string | null;

  detailBusy: boolean;
  detailError: string | null;
  detailSpec: string | null;
  /** Set once a hand-off finishes -- the RESULT document's key (the produced-joints take-off). */
  detailDerivedKey: string | null;
  /** The produced GLB's key, which is what the scene overlays. Separate from `detailDerivedKey`
   *  because they are different documents: loading the take-off json as a model answers 415. */
  detailGlbKey: string | null;
  /** What this session's detail runs have PRODUCED, accumulated across specs -- the `Joints` tab's
   *  produced section reads this beside the loaded model's own take-off. */
  producedJoints: ProducedJoints | null;
  /** Joints a builder refused during those runs, reported rather than hidden. */
  producedSkipped: readonly string[];
  /** Where a batched "generate detail model" run has got to, for the button's label. `null` when
   *  no batched run is in flight. */
  detailProgress: { done: number; total: number } | null;

  setSource: (sourceName: string | null, sourceKey: string | null) => void;
  setOptions: (patch: Partial<ClashCheckOptions>) => void;
  setFilters: (patch: Partial<ClashFilters>) => void;
  selectGroup: (typeKey: string | null) => void;
  selectJoints: (ids: readonly string[]) => void;
  focusJoint: (id: string | null) => void;
  setIsolate: (mode: "off" | "ghost" | "hidden") => void;
  setIsolateOpacity: (v: number) => void;
  setShowMarkers: (v: boolean) => void;
  runCheck: (scope: string) => Promise<void>;
  runDetail: (scope: string, jointIds: readonly string[], spec: string) => Promise<void>;
  /** Detail every joint that has a matching generator, one job per spec. */
  runDetailAll: (scope: string, only?: readonly string[] | null) => Promise<void>;
  reset: () => void;
}

function realFlowDeps(scope: string): ClashFlowDeps {
  return {
    api: {
      runClashCheck: (s, body) => clashCheckApi.runClashCheck(s, body),
      runClashDetail: (s, body) => clashCheckApi.runClashDetail(s, body),
      getClashResult: (s, key) => clashCheckApi.getClashResult(s, key),
      getDetailStats: (s, key) => clashCheckApi.getDetailStats(s, key),
      async jobStatus(jobId) {
        const status = await conversionApi.convertStatus(jobId);
        return { status: status.status, error: status.error };
      },
    },
    trackJob: (opts) => {
      trackJob({ ...opts, scopeUrl: scope });
    },
  };
}

const INITIAL_OPTIONS: ClashCheckOptions = {
  out_of_plane_tol: 0.1,
  point_tol: 1e-5,
  root: null,
  include_plate_joints: true,
};

export const useClashCheckStore = create<ClashCheckState>((set, get) => ({
  sourceName: null,
  sourceKey: null,
  options: INITIAL_OPTIONS,
  filters: DEFAULT_CLASH_FILTERS,
  selectedGroup: null,
  selectedJoints: [],
  selectedJoint: null,
  isolate: "ghost",
  isolateOpacity: 0.15,
  showMarkers: true,

  jobId: null,
  derivedKey: null,
  cached: false,
  result: null,
  busy: false,
  error: null,

  detailBusy: false,
  detailError: null,
  detailSpec: null,
  detailDerivedKey: null,
  detailGlbKey: null,
  producedJoints: null,
  producedSkipped: [],
  detailProgress: null,

  setSource: (sourceName, sourceKey) => {
    // A different source invalidates the previous run's result -- a stale clash result rendered
    // against a model that is no longer loaded would misreport joints that aren't there.
    if (get().sourceKey !== sourceKey) {
      set({
        sourceName,
        sourceKey,
        result: null,
        derivedKey: null,
        cached: false,
        error: null,
        selectedGroup: null,
        selectedJoints: [],
        selectedJoint: null,
        detailDerivedKey: null,
        detailGlbKey: null,
        producedJoints: null,
        producedSkipped: [],
        detailError: null,
      });
    } else {
      set({ sourceName });
    }
  },
  setOptions: (patch) => set((s) => ({ options: { ...s.options, ...patch } })),
  setFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
  selectGroup: (typeKey) => set({ selectedGroup: typeKey, selectedJoints: [], selectedJoint: null }),
  selectJoints: (ids) => set({ selectedJoints: ids }),
  focusJoint: (id) => {
    if (id === null) {
      set({ selectedJoint: null });
      return;
    }
    // Opening the joint's group with it is what makes a marker click land somewhere visible: the
    // row exists only inside its group, and the groups are collapsed by default.
    const typeKey = get().result?.jointsById.get(id)?.typeKey ?? null;
    set({ selectedJoint: id, selectedGroup: typeKey ?? get().selectedGroup });
  },
  setIsolate: (mode) => set({ isolate: mode }),
  setIsolateOpacity: (v) => set({ isolateOpacity: Math.max(0.02, Math.min(1, v)) }),
  setShowMarkers: (v) => set({ showMarkers: v }),

  runCheck: async (scope) => {
    const { sourceKey, options } = get();
    if (!sourceKey || get().busy) return;
    set({ busy: true, error: null });
    try {
      const { result, derivedKey, cached } = await runClashCheckFlow(realFlowDeps(scope), scope, sourceKey, options);
      set({ result, derivedKey, cached, busy: false, selectedGroup: null, selectedJoints: [], selectedJoint: null });
    } catch (e) {
      set({ busy: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  runDetail: async (scope, jointIds, spec) => {
    const { derivedKey } = get();
    if (!derivedKey || get().detailBusy) return;
    set({ detailBusy: true, detailError: null, detailSpec: spec, detailDerivedKey: null, detailGlbKey: null });
    try {
      const { derivedKey: detailKey, glbKey, stats } = await runClashDetailFlow(
        realFlowDeps(scope),
        scope,
        derivedKey,
        jointIds,
        spec,
      );
      set((s) => ({
        detailBusy: false,
        detailDerivedKey: detailKey,
        detailGlbKey: glbKey ?? detailKey,
        producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
        producedSkipped: [...s.producedSkipped, ...stats.skipped],
      }));
    } catch (e) {
      set({ detailBusy: false, detailError: e instanceof Error ? e.message : String(e) });
    }
  },

  runDetailAll: async (scope, only) => {
    const { derivedKey, result } = get();
    if (!derivedKey || !result || get().detailBusy) return;
    const batches = detailBatches(result, only ?? null);
    if (batches.length === 0) {
      set({ detailError: "No joint in this result has a generator that could detail it." });
      return;
    }
    set({
      detailBusy: true,
      detailError: null,
      detailDerivedKey: null,
      detailGlbKey: null,
      detailProgress: { done: 0, total: batches.length },
    });
    // SEQUENTIAL, not parallel: each batch is a worker job, and firing every spec at once would
    // queue work the pools have to serialise anyway while making a partial failure harder to
    // read. Each finished batch sets `detailDerivedKey`, which is what loads it as an overlay --
    // so the joints appear as they are built rather than all at the end.
    let done = 0;
    for (const batch of batches) {
      set({ detailSpec: batch.spec.spec });
      try {
        const { derivedKey: detailKey, glbKey, stats } = await runClashDetailFlow(
          realFlowDeps(scope),
          scope,
          derivedKey,
          batch.jointIds,
          batch.spec.spec,
        );
        done += 1;
        set((s) => ({
          detailDerivedKey: detailKey,
          detailGlbKey: glbKey ?? detailKey,
          producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
          producedSkipped: [...s.producedSkipped, ...stats.skipped],
          detailProgress: { done, total: batches.length },
        }));
      } catch (e) {
        // One spec failing does not cancel the rest: the others are separate jobs against
        // separate joints, and stopping would throw away work already queued.
        set({ detailError: e instanceof Error ? e.message : String(e) });
        done += 1;
        set({ detailProgress: { done, total: batches.length } });
      }
    }
    set({ detailBusy: false, detailProgress: null });
  },

  reset: () =>
    set({
      sourceName: null,
      sourceKey: null,
      options: INITIAL_OPTIONS,
      filters: DEFAULT_CLASH_FILTERS,
      selectedGroup: null,
      selectedJoints: [],
      selectedJoint: null,
      jobId: null,
      derivedKey: null,
      cached: false,
      result: null,
      busy: false,
      error: null,
      detailBusy: false,
      detailError: null,
      detailSpec: null,
      detailDerivedKey: null,
      detailGlbKey: null,
      producedJoints: null,
      producedSkipped: [],
      detailProgress: null,
    }),
}));
