// What the Clashes panel shows, derived from the one result the clash-check store holds.

import type { ClashCheckTarget } from "@/services/api/clashCheck";
import { memberKey } from "@/utils/groups/savedGroups";
import type { ClashApplicableSpec, ClashGroup, ClashJoint, ClashResult } from "./result";

// ---------------------------------------------------------------------------------------------
// Pure derivations. Every one takes a `ClashResult` (plus, where relevant, the current filters)
// and returns an answer -- nothing here is stored, nothing here is recomputed differently by two
// callers. Call these from a component's `useMemo`, the same way `AssetsTab` derives `view`.
// ---------------------------------------------------------------------------------------------

/** The result with every joint from a hidden PRODUCER removed -- joints, groups and counts
 *  together.
 *
 *  Filtered as ONE derivation rather than at each reading site, because the rows, the 3D markers
 *  and the group counts are all read from this and a filter applied to only some of them would
 *  show a count that does not match what is listed. A group left with no visible joints is
 *  dropped entirely: an empty row invites a click that can select nothing.
 *
 *  `hidden` empty is the identity, returned as the SAME object, so the common case allocates
 *  nothing and a `useMemo` on it stays stable. */
export function withoutHiddenOrigins(result: ClashResult, hidden: readonly string[]): ClashResult {
  if (hidden.length === 0) return result;
  const hiddenSet = new Set(hidden);
  const joints = result.joints.filter((j) => !hiddenSet.has(j.origin));
  if (joints.length === result.joints.length) return result;

  const visible = new Set(joints.map((j) => j.id));
  const groups = result.groups
    .map((g) => {
      const jointIds = g.jointIds.filter((id) => visible.has(id));
      return { ...g, jointIds, count: jointIds.length };
    })
    .filter((g) => g.count > 0);

  return {
    ...result,
    joints,
    groups,
    // The counts a reader compares against the rows. `members` is a fact about the SOURCE, not
    // about which passes are shown, so it is left alone.
    counts: { ...result.counts, joints: joints.length },
    jointsById: new Map(joints.map((j) => [j.id, j] as const)),
  };
}

/** The result every VIEW should read: the raw one with hidden producers removed.
 *
 *  One derivation, called from the rows, the markers and the panel alike. A filter applied by
 *  some readers and not others is how a panel ends up showing eight joints under a heading that
 *  says twelve. */
export function visibleResult(state: {
  result: ClashResult | null;
  hiddenOrigins: readonly string[];
}): ClashResult | null {
  return state.result ? withoutHiddenOrigins(state.result, state.hiddenOrigins) : null;
}

/** Which producers this result actually contains, with how many joints each contributed -- what
 *  the filter offers. Built from the JOINTS, not from `passes`, so a producer shows up exactly
 *  when it has something to hide. */
export function originsInResult(result: ClashResult): readonly { origin: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const j of result.joints) counts.set(j.origin, (counts.get(j.origin) ?? 0) + 1);
  return [...counts.entries()]
    .map(([origin, count]) => ({ origin, count }))
    .sort((a, b) => b.count - a.count || a.origin.localeCompare(b.origin));
}

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
 *  joint, and the kinds are what a person decides about (`result.py`'s module doc).
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

/** Whether two targets check the same model, compared by VALUE -- callers build a fresh object per
 *  render, and identity would reset the result on every repaint. A group is its members (in any
 *  order); its name is only a label, so renaming one does not throw a result away. */
export function sameClashTarget(a: ClashCheckTarget | null, b: ClashCheckTarget | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.kind === "file" && b.kind === "file") return a.sourceKey === b.sourceKey;
  if (a.kind === "node" && b.kind === "node") {
    return (
      a.collection === b.collection &&
      a.subject === b.subject &&
      (a.revision ?? null) === (b.revision ?? null) &&
      (a.node ?? null) === (b.node ?? null)
    );
  }
  if (a.kind === "group" && b.kind === "group") {
    if (a.members.length !== b.members.length) return false;
    const keys = new Set(a.members.map(memberKey));
    return b.members.every((m) => keys.has(memberKey(m)));
  }
  return false;
}

/** Whether a spec should be OFFERED, never whether it would succeed.
 *
 *  `capability === null` -- a built-in spec, runs in core's default pool, always offered.
 *
 *  `capability` a string -- an out-of-tree spec, routed to whichever pool currently advertises
 *  that capability. `liveCapabilities` is the live `connection_specs` union; when it is known, a capability absent from it is shown as UNAVAILABLE rather than
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
