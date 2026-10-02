// A group member INSIDE a published node, to the published tree's row id a provider can be asked
// for.
//
// A member stores names (`./savedGroups`); a provider's node request takes the row id the asset
// tree shows. The two meet the way a 3D pick is revealed in Sources (`utils/tree_view/
// revealPick.ts`): walk the published tree down from the row the model was loaded at, one name
// per level, fetching levels on the way (`@/assets/scenePick`). A member counts as resolved only
// when the walk reaches a row named like the element -- asking for the deepest row it did reach
// would request a whole deck for one plate.
//
// The walk reads the asset browser's tree, so it may switch the Sources tab to the member's
// collection, as a pick revealed in Sources does.

import { buildAssetHierarchy, carriesContent } from "@/assets/assetView";
import type { Hierarchy } from "@/assets/hierarchy";
import { resolveCollection } from "@/assets/resolve";
import { matchRow, resolvePickRow } from "@/assets/scenePick";
import { levelKey, levelOwner, levelWanted } from "@/assets/spines";
import type { AssetNode } from "@/assets/types";
import { assetsApi } from "@/services/api/assets";
import { sourceNodesApi } from "@/services/api/sourceNodes";
import { loaderFor } from "@/state/assetBrowserLoader";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";

import { memberKey, type GroupMember } from "./savedGroups";

/** `memberKey` -> row id, for every node member with an element the published tree could place.
 *  Members it could not place are simply absent (`groupNodePlan` reports them). Best effort: a
 *  collection that cannot be opened leaves its members out rather than failing the rest. */
export async function resolveElementRows(scope: string, members: readonly GroupMember[]): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  const byCollection = new Map<string, GroupMember[]>();
  for (const m of members) {
    if (m.target.kind !== "node" || m.element === null) continue;
    const list = byCollection.get(m.target.collection) ?? [];
    list.push(m);
    byCollection.set(m.target.collection, list);
  }
  if (byCollection.size === 0) return out;

  const store = useAssetBrowserStore;
  const loader = loaderFor(store, assetsApi, sourceNodesApi);
  for (const [collection, list] of byCollection) {
    try {
      const first = store.getState();
      if (first.collection !== collection) {
        if (first.collections && !first.collections.includes(collection)) continue;
        await loader.chooseCollection(scope, collection);
      }
      if (store.getState().collection !== collection) continue;

      let builtAt = -1;
      let hierarchy: Hierarchy<AssetNode> | null = null;
      const current = (): Hierarchy<AssetNode> => {
        const s = store.getState();
        if (hierarchy && builtAt === s.forestVersion) return hierarchy;
        const built = buildAssetHierarchy(s.forest);
        hierarchy = built;
        builtAt = s.forestVersion;
        return built;
      };
      const held = (id: string | null | undefined) => (id && current().byId.has(id) ? id : null);

      for (const member of list) {
        if (member.target.kind !== "node" || member.element === null) continue;
        const anchor = held(member.target.node) ?? held(member.target.subject);
        if (!anchor) continue;
        const resolved = await resolvePickRow(
          {
            label: (id) => current().byId.get(id)?.data.label,
            children: (id) => current().childrenOf(id),
            ensureChildren: async (id) => {
              const s = store.getState();
              if (!s.index || !s.collection) return;
              const resolution = resolveCollection(s.index, s.collection, s.mode, { carriesContent });
              const req = levelOwner(resolution, s.forest.origins, id);
              const h = current();
              if (!req || !levelWanted(h.byId.get(id)?.data, req, s.levelLoaded, h.childrenOf(id).length > 0)) return;
              await loader.loadLevel(scope, req);
              const key = levelKey(req);
              if (!store.getState().levelLoading.has(key)) return;
              await new Promise<void>((resolve) => {
                const unsubscribe = store.subscribe((st) => {
                  if (st.levelLoading.has(key)) return;
                  unsubscribe();
                  resolve();
                });
              });
            },
          },
          anchor,
          member.path,
        );
        if (!resolved || resolved.matched === 0) continue;
        const label = current().byId.get(resolved.row)?.data.label;
        if (label !== undefined && matchRow([{ id: resolved.row, label }], member.element)) {
          out.set(memberKey(member), resolved.row);
        }
      }
    } catch (e) {
      console.warn(`could not place group members in ${collection}`, e);
    }
  }
  return out;
}
