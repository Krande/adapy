// Detailing a clash result: which spec details each joint, the detail jobs in batches, and
// the joints a detail run produced.

import { rankSpecs, type SpecProviderPreference } from "@/services/clashSpecProviders";
import type { ClashApplicableSpec, ClashJoint, ClashResult } from "./result";

/** The spec each joint would be detailed WITH: its highest-priority applicable spec, ties broken
 *  by name so the answer is stable. `null` for a joint no registered spec binds. */
export function specForJoint(joint: ClashJoint, providers: SpecProviderPreference = null): ClashApplicableSpec | null {
  // The scope's spec-provider preference first (`services/clashSpecProviders.ts`), then the spec's
  // own priority. Unrestricted (`null`) is priority alone -- what this always did.
  return rankSpecs(joint.applicable, providers)[0] ?? null;
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
  providers: SpecProviderPreference = null,
): readonly { spec: ClashApplicableSpec; jointIds: readonly string[] }[] {
  const limit = only == null ? null : only instanceof Set ? only : new Set(only);
  const bySpec = new Map<string, { spec: ClashApplicableSpec; jointIds: string[] }>();
  for (const joint of result.joints) {
    if (limit && !limit.has(joint.id)) continue;
    const spec = specForJoint(joint, providers);
    if (!spec) continue;
    const bucket = bySpec.get(spec.spec) ?? { spec, jointIds: [] };
    bucket.jointIds.push(joint.id);
    bySpec.set(spec.spec, bucket);
  }
  return [...bySpec.values()].sort((a, b) => b.spec.priority - a.spec.priority || a.spec.spec.localeCompare(b.spec.spec));
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
