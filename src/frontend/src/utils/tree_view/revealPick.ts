// A pick in the 3D view, shown in whichever tree the drawer has open.
//
//   Scene    the react-arborist tree of what is loaded: select the picked ranges' rows and scroll
//            to the picked one (`scrollTo` opens its parents). Done whichever tab is showing --
//            hidden, the tree is zero rows tall, so `TreeViewComponent` scrolls again when it is
//            shown; the selection is what has to be right meanwhile.
//   Sources  the asset browser's tree, ONLY while it is the tab showing (it fetches levels to get
//            there, which a closed tab has no business doing): find the row the picked element
//            came from (`@/assets/scenePick`), open the branch down to it, select it, scroll.
//            A model that was not loaded from a provider has no row there, and nothing happens.
//
// Both only while the drawer is open, as the Scene sync always was: a pick must not cost a tree
// update nobody can see.

import {buildAssetHierarchy, carriesContent} from "@/assets/assetView";
import {ancestorsOf, type Hierarchy} from "@/assets/hierarchy";
import {resolveCollection} from "@/assets/resolve";
import {resolvePickRow} from "@/assets/scenePick";
import {levelKey, levelOwner, levelWanted} from "@/assets/spines";
import type {AssetNode} from "@/assets/types";
import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {runtime} from "@/runtime/config";
import {assetsApi} from "@/services/api/assets";
import {sourceNodesApi} from "@/services/api/sourceNodes";
import {getSingletonViewerStores} from "@/state/AdaViewerContext";
import {loaderFor} from "@/state/assetBrowserLoader";
import {elementPath, modelRootOf, parseAssetSourceName} from "@/utils/export/selectionExport";
import {buildTreeIndices, type TreeIndices} from "./treeGraph";

let cachedRoot: TreeNodeData | null = null;
let cachedIndices: TreeIndices | null = null;

function sceneIndices(root: TreeNodeData): TreeIndices {
    if (root !== cachedRoot || !cachedIndices) {
        cachedIndices = buildTreeIndices(root);
        cachedRoot = root;
    }
    return cachedIndices;
}

/** Bumped per pick: a Sources walk still fetching levels for an older pick gives up. */
let generation = 0;

/** Select the picked ranges' rows in the Scene tree and scroll to the picked one. */
function syncSceneTree(modelKey: string, rangeId: string): TreeNodeData | null {
    const {useTreeViewStore, useSelectedObjectStore} = getSingletonViewerStores();
    const tv = useTreeViewStore.getState();
    const lastNode = tv.findNodeByRangeId(modelKey, rangeId);
    if (!tv.treeData || !tv.tree) return lastNode;
    const tree: any = tv.tree;
    // Flagged so the tree's onSelect does not treat this as a user click and re-select in 3D.
    tree.isProgrammaticChange = true;
    try {
        const ids: string[] = [];
        for (const [m, ranges] of useSelectedObjectStore.getState().selectedObjects) {
            const key: string | undefined = (m as any).unique_key ?? (m.userData ? m.userData["unique_hash"] : undefined);
            if (!key) continue;
            for (const rid of ranges) {
                // By (model key, range id) -- the unique node id -- never by display name, which
                // repeats thousands of times in real CAD models.
                const node = tv.findNodeByRangeId(key, rid);
                if (node) ids.push(node.id);
            }
        }
        tree.setSelection({ids, mostRecent: lastNode, anchor: lastNode});
        if (lastNode) tree.scrollTo({id: lastNode.id});
    } finally {
        tree.isProgrammaticChange = false;
    }
    return lastNode;
}

/** Find, open, select and scroll to the Sources row `node` (a Scene-tree row) came from. */
async function revealInSources(node: TreeNodeData, idx: TreeIndices, gen: number): Promise<void> {
    const store = getSingletonViewerStores().useAssetBrowserStore;
    const alive = () => gen === generation;
    const root = modelRootOf(node, idx);
    const sourceName = root?.source_name;
    if (!root || !sourceName) return;
    const first = store.getState();
    const ref = first.loaded.find((a) => a.sourceName === sourceName)?.ref ?? parseAssetSourceName(sourceName);
    const scope = first.scope;
    if (!ref || !scope) return;

    const loader = loaderFor(store, assetsApi, sourceNodesApi);
    if (first.collection !== ref.collection) {
        if (first.collections && !first.collections.includes(ref.collection)) return;
        await loader.chooseCollection(scope, ref.collection);
        if (!alive()) return;
    }

    // The forest as it stands; rebuilt only when a fetched level changed it.
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

    // The row the model was loaded at; its subject when that row is not held (yet).
    const held = (id: string | undefined) => (id && current().byId.has(id) ? id : null);
    const anchor = held(ref.node) ?? held(ref.subject);
    if (!anchor) return;

    // Names from the model's top down. `top_name` is what the model's top was called when loaded
    // (the row's label, for a load from Sources); the root row's own `name` is the viewer's label.
    const path = [
        ...(root.top_name ? [root.top_name] : []),
        ...(node.id === root.id ? [] : elementPath(node, root, idx)),
    ];

    const resolved = await resolvePickRow(
        {
            label: (id) => current().byId.get(id)?.data.label,
            children: (id) => current().childrenOf(id),
            ensureChildren: async (id) => {
                const s = store.getState();
                if (!s.index || !s.collection) return;
                const resolution = resolveCollection(s.index, s.collection, s.mode, {carriesContent});
                const req = levelOwner(resolution, s.forest.origins, id);
                const h = current();
                if (!req || !levelWanted(h.byId.get(id)?.data, req, s.levelLoaded, h.childrenOf(id).length > 0)) return;
                await loader.loadLevel(scope, req);
                // Already in flight when asked (the tab's own effect fetches expanded rows): the
                // call above returned at once, so wait for that fetch to land.
                const key = levelKey(req);
                if (!store.getState().levelLoading.has(key)) return;
                await new Promise<void>((resolve) => {
                    const unsubscribe = store.subscribe((st) => {
                        if (st.levelLoading.has(key) && alive()) return;
                        unsubscribe();
                        resolve();
                    });
                });
            },
            alive,
        },
        anchor,
        path,
    );
    if (!resolved || !alive()) return;
    const h = current();
    const open = [...ancestorsOf(h, resolved.row), ...resolved.chain.slice(0, -1)];
    store.getState().revealRow(resolved.row, open);
}

/** Show the element at (`modelKey`, `rangeId`) -- just picked in 3D -- in the open tree. Call
 *  once the 3D selection is settled; the Sources half runs on (it may fetch) after this returns. */
export function revealPickInTrees(modelKey: string, rangeId: string): void {
    const gen = ++generation;
    const {useTreeViewStore, useAssetBrowserStore} = getSingletonViewerStores();
    if (useTreeViewStore.getState().isTreeCollapsed) return;

    const node = syncSceneTree(modelKey, rangeId);

    const sourcesShowing = runtime.isRestMode() && useAssetBrowserStore.getState().tab === "assets";
    const treeData = useTreeViewStore.getState().treeData;
    if (!sourcesShowing || !node || !treeData) return;
    void revealInSources(node, sceneIndices(treeData), gen).catch((e) => {
        console.warn("could not reveal the picked element in Sources", e);
    });
}
