// "Request geometry" for a set of group members: ask one or more providers for the nodes the members
// name, in the batches each provider takes, and publish what they stage.
//
// ONE IMPLEMENTATION, TWO CALLERS. The saved group's "Request geometry…" form asks for a whole
// group; a clash check through a geometry provider asks for exactly the members that provider had
// no node for, before it runs (`state/clashCheckStore.ts`'s `runGeometryAwareCheckFlow`). Both go
// through here, so batching (`maxNodes`), labels (`labelOption`) and the job toast behave the same.
//
// Pure apart from the injected request deps (`@/assets/collectionRequest`), so it runs under node.

import { nodeBatches, requestNodes, type CollectionRequestDeps } from "@/assets/collectionRequest";
import type { AssetNodeRequest } from "@/services/assetScopeCollections";

import { groupNodePlan, memberKey, parseMember, type GroupMember, type GroupNodePlan } from "./savedGroups";

/** A provider to ask, and how it takes node requests. */
export interface GroupRequestProvider {
  readonly providerId: string;
  readonly req: AssetNodeRequest;
}

export interface GroupRequestOutcome {
  /** Requests whose provider staged something that was then published. */
  readonly published: number;
  /** Requests whose provider answered that nothing changed since its last publish. */
  readonly unchanged: number;
  /** `"<provider> / <collection>: <why>"` per request that failed. */
  readonly failed: readonly string[];
}

/** Every node of `plan` requested from every provider in `providers`, one job per batch of the
 *  provider's `maxNodes`. All requests run at once -- each is its own provider job, and the slow
 *  part is the provider's, not ours -- and one failing does not stop the others. */
export async function requestGroupGeometry(
  deps: CollectionRequestDeps,
  scope: string,
  providers: readonly GroupRequestProvider[],
  plan: GroupNodePlan,
): Promise<GroupRequestOutcome> {
  const jobs: Promise<"published" | "unchanged" | string>[] = [];
  for (const { providerId, req } of providers) {
    for (const [collection, targets] of plan.byCollection) {
      for (const batch of nodeBatches(req, targets)) {
        jobs.push(
          requestNodes(
            deps,
            scope,
            providerId,
            req,
            collection,
            batch.map((t) => ({ id: t.id, label: t.label })),
          ).then(
            (out) => (out.unchanged ? "unchanged" : "published"),
            (e) => `${providerId} / ${collection}: ${e instanceof Error ? e.message : String(e)}`,
          ),
        );
      }
    }
  }
  const results = await Promise.all(jobs);
  return {
    published: results.filter((r) => r === "published").length,
    unchanged: results.filter((r) => r === "unchanged").length,
    failed: results.filter((r) => r !== "published" && r !== "unchanged"),
  };
}

/** "3 requests published, 1 already up to date." */
export function describeRequestOutcome(out: GroupRequestOutcome): string {
  return (
    `${out.published} request${out.published === 1 ? "" : "s"} published` +
    (out.unchanged ? `, ${out.unchanged} already up to date` : "") +
    "."
  );
}

/** How many nodes a plan names, across its collections. */
export function planNodeCount(plan: GroupNodePlan): number {
  let n = 0;
  for (const targets of plan.byCollection.values()) n += targets.length;
  return n;
}

// --- members a geometry provider had nothing for -------------------------------------------------

/** A member left out by a geometry provider, as the server reports one -- the geometry plan's
 *  `unmatched` (`POST …/clash-check/geometry-plan`) and a failed export's `error.unmatched`: the
 *  member in the group wire shape, why, the name it was looked for under, and its row in its own
 *  provider's tree (`null` when that tree does not place it). */
export interface UnmatchedMember {
  readonly member: unknown;
  readonly reason?: string;
  readonly label?: string | null;
  readonly row?: string | null;
}

/** The members `unmatched` names, and what the server already knows about each by `memberKey`: the
 *  label it looked for and the row it placed. Entries that are not a member are dropped. */
export function unmatchedRequestParts(unmatched: readonly UnmatchedMember[]): {
  members: GroupMember[];
  labels: Map<string, string>;
  rows: Map<string, string>;
} {
  const members: GroupMember[] = [];
  const labels = new Map<string, string>();
  const rows = new Map<string, string>();
  for (const u of unmatched) {
    const member = parseMember(u.member);
    if (!member) continue;
    members.push(member);
    if (u.label) labels.set(memberKey(member), u.label);
    if (u.row && member.element !== null) rows.set(memberKey(member), u.row);
  }
  return { members, labels, rows };
}

export interface UnmatchedRequestDeps {
  /** Element members -> the published tree's row ids (`resolveElementRows`). */
  resolveRows(scope: string, members: readonly GroupMember[]): Promise<ReadonlyMap<string, string>>;
  /** The request jobs' own deps -- the same ones "Request geometry…" uses. */
  request: CollectionRequestDeps;
  /** After a request published something: let the asset browser re-read what is published. */
  afterRequest?: () => Promise<void> | void;
}

/** Request exactly the `unmatched` members from `provider`, through the shared group request:
 *  element members at the rows the server placed (the tree walk only for the ones it could not),
 *  whole nodes by their own id, each with the label it was looked for under (a provider may need it
 *  to find a node another provider published), in the provider's batches. Resolves the notes for the
 *  panel and the request's outcome (`null` when there was nothing to request). */
export async function requestUnmatchedMembers(
  deps: UnmatchedRequestDeps,
  scope: string,
  provider: string,
  req: AssetNodeRequest,
  unmatched: readonly UnmatchedMember[],
  stage: (s: string | null) => void = () => {},
): Promise<{ notes: string[]; outcome: GroupRequestOutcome | null }> {
  const notes: string[] = [];
  const { members, labels, rows } = unmatchedRequestParts(unmatched);
  const unplaced = members.filter((m) => m.element !== null && !rows.has(memberKey(m)));
  if (unplaced.length > 0) {
    stage("finding the missing members in the published tree");
    for (const [key, row] of await deps.resolveRows(scope, unplaced)) rows.set(key, row);
  }
  const nodePlan = groupNodePlan(members, rows, labels);
  if (nodePlan.unresolved.length > 0) {
    const first = nodePlan.unresolved[0];
    notes.push(
      `${nodePlan.unresolved.length} member${nodePlan.unresolved.length === 1 ? "" : "s"} could not be requested: ` +
        `${first.reason}${nodePlan.unresolved.length > 1 ? " (and others)" : ""}.`,
    );
  }
  const count = planNodeCount(nodePlan);
  if (count === 0) return { notes, outcome: null };
  stage(`requesting ${count} node${count === 1 ? "" : "s"} from ${provider}`);
  const requestDeps: CollectionRequestDeps = {
    ...deps.request,
    onStage: (s) => {
      deps.request.onStage?.(s);
      stage(s);
    },
  };
  const out = await requestGroupGeometry(requestDeps, scope, [{ providerId: provider, req }], nodePlan);
  notes.push(`Requested from ${provider}: ${describeRequestOutcome(out)}`);
  if (out.failed.length) notes.push(`${out.failed.length} request${out.failed.length === 1 ? "" : "s"} failed: ${out.failed.join("; ")}`);
  if (out.published > 0) await deps.afterRequest?.();
  return { notes, outcome: out };
}
