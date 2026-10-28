// The attribute index's row for a selected scene-tree row (`GET /assets/search/node`).
//
// A row of a model loaded from a published asset is looked up in that asset's (provider,
// collection): the model's root row by the node it was loaded as, any row below it by its name,
// which is the node's full name. Rows of any other model have no lookup.
//
// Answers are cached per (scope, provider, collection, node); a failed read caches as no answer.

import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import {
  attributeSearchApi,
  type AttributesRequestPermission,
  type NodeAttributesResponse,
} from "@/services/api/attributeSearch";
import { refOfSourceName, type LoadedRef } from "@/utils/groups/savedGroups";
import type { TreeIndices } from "@/utils/tree_view/treeGraph";

export interface AttributeLookup {
  readonly provider: string;
  readonly collection: string;
  readonly by: { readonly nodeId: string } | { readonly name: string };
}

/** The lookup for `row`, or null when its model was not loaded from a published asset. */
export function attributeLookupFor(
  row: TreeNodeData,
  idx: TreeIndices,
  loaded: readonly LoadedRef[],
): AttributeLookup | null {
  let root: TreeNodeData | undefined = idx.byId.get(row.id);
  if (!root || root === idx.root) return null;
  for (let p = idx.parent.get(root.id); p && p !== idx.root; p = idx.parent.get(root.id)) root = p;
  const sourceName = root.source_name;
  if (!sourceName || !sourceName.startsWith("assets:")) return null;
  const ref = refOfSourceName(sourceName, loaded);
  if (!ref) return null;
  if (root.id === row.id) return { provider: ref.provider, collection: ref.collection, by: { nodeId: ref.node ?? ref.subject } };
  return row.name ? { provider: ref.provider, collection: ref.collection, by: { name: row.name } } : null;
}

const collectionKey = (scope: string, provider: string, collection: string) =>
  `${scope}\u0000${provider}\u0000${collection.toLowerCase()}`;

export function lookupKey(scope: string, l: AttributeLookup): string {
  const by = "nodeId" in l.by ? `id\u0000${l.by.nodeId}` : `name\u0000${l.by.name}`;
  return `${collectionKey(scope, l.provider, l.collection)}\u0000${by}`;
}

const answers = new Map<string, Promise<NodeAttributesResponse | null>>();
const permissions = new Map<string, Promise<AttributesRequestPermission | null>>();

export function nodeAttributesFor(scope: string, l: AttributeLookup): Promise<NodeAttributesResponse | null> {
  const k = lookupKey(scope, l);
  let p = answers.get(k);
  if (!p) {
    p = attributeSearchApi.nodeAttributes(scope, l.provider, l.collection, l.by).catch(() => null);
    answers.set(k, p);
  }
  return p;
}

/** Forget the answers of one collection: after its index was (re)built. */
export function forgetCollection(scope: string, provider: string, collection: string): void {
  const prefix = `${collectionKey(scope, provider, collection)}\u0000`;
  for (const k of [...answers.keys()]) if (k.startsWith(prefix)) answers.delete(k);
}

export function attributesRequestPermissionFor(scope: string, provider: string): Promise<AttributesRequestPermission | null> {
  const k = `${scope}\u0000${provider}`;
  let p = permissions.get(k);
  if (!p) {
    p = attributeSearchApi.attributesRequestPermission(scope, provider).catch(() => null);
    permissions.set(k, p);
  }
  return p;
}

/** Test hook. */
export function clearIndexedAttributesCache(): void {
  answers.clear();
  permissions.clear();
}
