// "Load into scene" for a saved group: put every member's model in the scene from what is ALREADY
// published or stored -- the case of geometry requested in an earlier session -- then select the
// group. Nothing is requested from a provider here; that is "Request geometry…", a separate action,
// and a member with nothing published is reported, not fetched.
//
// One load per SOURCE, not per member: several members commonly live in one published node or one
// file, and loading it once is what makes the group's members appear together.

import { loadPrepared, parseDeliveryClaim, prepareNode, type LoadNodeDeps, type NodeRef } from "@/assets/delivery";
import { assetsApi } from "@/services/api/assets";
import type { ScopeUrl } from "@/services/api/client";
import { getSingletonViewerStores } from "@/state/AdaViewerContext";
import { requestRender } from "@/state/perfStore";

import type { GroupMember, SavedGroup } from "./savedGroups";
import { groupSourceKey, sourcesToLoad } from "./groupSources";
import { selectSavedGroup, type GroupSelectionOutcome } from "./groupScene";

export interface GroupLoadOutcome {
  /** Sources put in the scene by this call. */
  readonly loaded: number;
  /** Sources already in the scene, left as they were. */
  readonly already: number;
  /** Sources that could not be loaded, each with its members and why. */
  readonly failed: readonly { readonly source: string; readonly members: readonly GroupMember[]; readonly reason: string }[];
  /** The selection made afterwards. */
  readonly selection: GroupSelectionOutcome;
}

export async function loadSavedGroup(
  group: SavedGroup,
  scope: string,
  deps: LoadNodeDeps,
  onStage?: (stage: string) => void,
): Promise<GroupLoadOutcome> {
  // What is in the scene already answers itself: the selection's own resolver says which members'
  // models are missing, by the same matching "Select" uses, so the two cannot disagree.
  const before = selectSavedGroup(group);
  const todo = sourcesToLoad(before.notLoaded);
  const failed: { source: string; members: GroupMember[]; reason: string }[] = [];
  let loaded = 0;
  let n = 0;

  for (const [key, members] of todo) {
    n += 1;
    const target = members[0].target;
    onStage?.(`loading ${n} of ${todo.size}: ${describeSource(members[0])}`);
    try {
      if (target.kind === "file") {
        const { overlay_file_in_scene } = await import("@/utils/scene/handlers/overlay_file_in_scene");
        await overlay_file_in_scene(target.source_key, undefined, { scope });
      } else {
        await loadNode(scope, deps, target);
      }
      loaded += 1;
    } catch (e) {
      failed.push({ source: key, members, reason: reasonFor(e) });
    }
  }
  requestRender();

  // Selected again AFTER the loads, so the members that just arrived are in it.
  const selection = loaded ? selectSavedGroup(group) : before;
  const already = new Set(group.members.map(groupSourceKey)).size - todo.size;
  return { loaded, already, failed, selection };
}

async function loadNode(
  scope: string,
  deps: LoadNodeDeps,
  target: Extract<GroupMember["target"], { kind: "node" }>,
): Promise<void> {
  const node = target.node ?? target.subject;
  // The delivery claim for the node AS PUBLISHED by the member's own provider -- the revision the
  // group pinned, or the latest when it pinned none.
  const wire = await assetsApi.getAssetDelivery(scope as ScopeUrl, target.provider, target.collection, node, {
    ...(target.revision ? { revision: target.revision } : {}),
  });
  const claim = parseDeliveryClaim(wire);
  const ref: NodeRef = {
    provider: target.provider,
    collection: target.collection,
    subject: target.subject,
    revision: claim.revision,
    ...(node !== target.subject ? { node } : {}),
  };
  // Registered with the asset browser the way its own Load is, so the Sources tab shows it loaded
  // and "Select" resolves the members against it.
  const store = getSingletonViewerStores().useAssetBrowserStore;
  const key = `${node}\u0000${target.provider}`;
  store.getState().beginLoad(key);
  try {
    const asset = await loadPrepared(deps, ref, prepareNode(deps, scope, ref, claim), node);
    store.getState().endLoad(key, asset);
  } catch (e) {
    store.getState().failLoad(key, reasonFor(e));
    throw e;
  }
}

function describeSource(m: GroupMember): string {
  const t = m.target;
  return t.kind === "file" ? t.source_key : `${t.collection}/${t.node ?? t.subject}`;
}

/** A load failure in words a person can act on: nothing published is the common case. */
function reasonFor(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (/\b(404|409)\b|not found|no published|delivery 'none'|nothing to deliver/i.test(msg)) {
    return `nothing published to load (${msg}) -- use Request geometry first`;
  }
  return msg;
}
