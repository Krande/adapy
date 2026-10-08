// The Scene tree's right-click menu: visibility, framing and copying for the selected rows, and
// expanding or collapsing the row it was opened on.

import React, {useMemo} from "react";
import type {NodeApi} from "react-arborist";

import {KebabMenuItem, PositionedMenu} from "@/components/common/PositionedMenu";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {copySelectionNames} from "@/utils/clipboard/copySelectionNames";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";
import {forestNodeFor, providerAlternatives, type ProviderAlternative} from "@/assets/otherProviders";
import {realDeliveryDeps} from "@/components/asset_browser/AssetsTab";
import {getSingletonViewerStores} from "@/state/AdaViewerContext";
import {requestRender} from "@/state/perfStore";
import {elementPath, modelRootOf, parseAssetSourceName} from "@/utils/export/selectionExport";
import {loadAssetNode} from "@/utils/groups/groupLoad";
import {buildTreeIndices} from "@/utils/tree_view/treeGraph";
import {
    frameSelection,
    hiddenCount,
    hideRows,
    isolateRows,
    setSubtreeOpen,
    showAll,
    showRows,
} from "@/utils/tree_view/sceneTreeActions";

import type {TreeNodeData} from "./CustomNode";

/** "Load from <provider>" for a row of a model loaded from a provider: null for a row of anything
 *  else (a file), else the options -- or why there are none. */
function otherProviderLoads(row: TreeNodeData): {
    options: ProviderAlternative[];
    why: string;
    load: (o: ProviderAlternative) => Promise<void>;
} | null {
    const stores = getSingletonViewerStores();
    const treeData = stores.useTreeViewStore.getState().treeData;
    if (!treeData) return null;
    const idx = buildTreeIndices(treeData);
    const root = modelRootOf(row, idx);
    const ref = root?.source_name ? parseAssetSourceName(root.source_name) : null;
    if (!root || !ref) return null;
    const browser = stores.useAssetBrowserStore.getState();
    const scope = browser.scope;
    const load = async (o: ProviderAlternative) => {
        if (!scope) return;
        const deps = realDeliveryDeps((name) => stores.useModelState.getState().loadedSourceNames.has(name));
        try {
            await loadAssetNode(scope, deps, {
                kind: "node",
                provider: o.provider,
                collection: ref.collection,
                subject: o.subject,
                revision: o.revision,
                node: o.node !== o.subject ? o.node : null,
            });
            requestRender();
        } catch (e) {
            // Recorded on the row in the Sources tab as well (the shared load does that).
            console.error(`Load from ${o.provider} failed:`, e);
        }
    };
    if (!scope || browser.collection !== ref.collection) {
        return { options: [], why: `Open collection ${ref.collection.toUpperCase()} in the Sources tab to find this node's other providers`, load };
    }
    const start = ref.node ?? ref.subject;
    const id = row.id === root.id ? start : forestNodeFor(browser.forest, start, elementPath(row, root, idx));
    if (!id) {
        return { options: [], why: "This row is not in the Sources tree yet -- open its branch there first", load };
    }
    const options = providerAlternatives(browser.index, browser.forest, ref.collection, id, ref.provider);
    return { options, why: `No other provider publishes geometry for ${row.name}`, load };
}

export interface SceneTreeMenuState {
    /** The row right-clicked. */
    node: NodeApi<TreeNodeData>;
    x: number;
    y: number;
}

const SceneTreeMenu: React.FC<SceneTreeMenuState & {onClose: () => void}> = ({node, x, y, onClose}) => {
    // The right-click made the row part of the selection, so the selection is what the menu acts on.
    const selected = node.tree.selectedNodes.length ? node.tree.selectedNodes : [node];
    const rows = selected.map((n) => n.data);
    const {hidden, total} = useMemo(() => hiddenCount(rows), [selected]); // eslint-disable-line react-hooks/exhaustive-deps
    const what = selected.length === 1 ? "" : ` ${selected.length} rows`;
    const noGeometry = total === 0 ? "Nothing under the selection is drawn in the scene" : undefined;

    const items: KebabMenuItem[] = [
        {
            key: "goto",
            label: "Go to  (Shift+F)",
            onClick: frameSelection,
            disabled: total === 0,
            title: noGeometry,
        },
        {
            key: "hide",
            label: `Hide${what}  (Shift+H)`,
            onClick: () => hideRows(rows),
            disabled: total === 0 || hidden === total,
            title: noGeometry,
            separatorBefore: true,
        },
        {
            key: "show",
            label: hidden && hidden < total ? `Unhide${what}  (${hidden} hidden)` : `Unhide${what}`,
            onClick: () => showRows(rows),
            disabled: hidden === 0,
            title: hidden === 0 ? "Nothing under the selection is hidden" : undefined,
        },
        {
            key: "isolate",
            label: "Hide all others  (Shift+I)",
            onClick: () => isolateRows(rows),
            disabled: total === 0,
            title: noGeometry,
        },
        {key: "show-all", label: "Unhide all  (Shift+U)", onClick: showAll},
        {
            key: "copy",
            label: "Copy names  (Ctrl+C)",
            separatorBefore: true,
            onClick: () => void copySelectionNames(useSelectedObjectStore.getState().selectedObjects),
        },
    ];
    // The same node from the other providers that publish it -- for the row right-clicked.
    const alt = useMemo(() => otherProviderLoads(node.data), [node]);
    if (alt) {
        if (alt.options.length) {
            alt.options.forEach((o, i) =>
                items.push({
                    key: `load-from-${o.provider}`,
                    label: `Load from ${o.provider}`,
                    title: `${node.data.name} as ${o.provider} publishes it${o.subject !== o.node ? ` (under ${o.subject})` : ""}`,
                    onClick: () => void alt.load(o),
                    separatorBefore: i === 0,
                }),
            );
        } else {
            items.push({
                key: "load-from-none",
                label: "Load from another provider",
                onClick: () => undefined,
                disabled: true,
                title: alt.why,
                separatorBefore: true,
            });
        }
    }
    // Unload acts on whole models: the loaded model each selected row belongs to, each once.
    const models = new Map<string, string>();
    for (const n of selected) {
        let top: NodeApi<TreeNodeData> = n;
        while (top.level > 0 && top.parent) top = top.parent;
        if (top.data.source_name) models.set(top.data.source_name, top.data.name);
    }
    items.push({
        key: "unload",
        label: models.size > 1 ? `Unload ${models.size} models` : "Unload model",
        onClick: () => {
            for (const name of models.keys()) void unload_any_source(name);
        },
        disabled: models.size === 0,
        title: models.size ? [...models.values()].join("\n") : "Not part of a loaded model that can be unloaded",
        destructive: true,
        separatorBefore: true,
    });
    if (node.isInternal) {
        items.push(
            {key: "expand", label: "Expand all below", onClick: () => setSubtreeOpen(node, true), separatorBefore: true},
            {key: "collapse", label: "Collapse all below", onClick: () => setSubtreeOpen(node, false)},
        );
    }

    return <PositionedMenu items={items} anchor={{kind: "point", x, y}} onClose={onClose} header={node.data.name}/>;
};

export default SceneTreeMenu;
