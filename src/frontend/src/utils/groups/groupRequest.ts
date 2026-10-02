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

import type { GroupNodePlan } from "./savedGroups";

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
