// The scope's saved view of a collection's tree (`@/assets/treeView`): read once per choice of
// collection into the asset-browser store, and written straight through on a change -- shown at
// once, put back if the write fails.

import { useEffect, useState } from "react";

import type { TreeViewDoc } from "@/assets/treeView";
import { readViewDoc, writeViewDoc } from "@/services/assetView";
import type { useViewerStores } from "@/state/AdaViewerContext";

type AssetBrowserStore = ReturnType<typeof useViewerStores>["useAssetBrowserStore"];

export function useSavedTreeView(
    useAssetBrowserStore: AssetBrowserStore,
    scope: string,
    storeScope: string | null,
    collection: string | null,
): { viewBusy: boolean; viewError: string | null; saveView: (doc: TreeViewDoc) => Promise<void> } {
    const [viewBusy, setViewBusy] = useState(false);
    const [viewError, setViewError] = useState<string | null>(null);

    // The scope's saved view for this collection, read once per choice of it.
    useEffect(() => {
        if (!collection || storeScope !== scope) return;
        let live = true;
        void readViewDoc(scope, collection).then((doc) => {
            if (live && useAssetBrowserStore.getState().collection === collection) {
                useAssetBrowserStore.getState().setViewDoc(doc);
            }
        });
        return () => {
            live = false;
        };
    }, [scope, collection, storeScope, useAssetBrowserStore]);

    // Written straight through, like every other control in the tab, and shown
    // at once; put back if the write fails.
    const saveView = async (doc: TreeViewDoc) => {
        if (!collection) return;
        const store = useAssetBrowserStore.getState();
        const previous = store.viewDoc;
        setViewBusy(true);
        setViewError(null);
        store.setViewDoc(doc);
        try {
            await writeViewDoc(scope, collection, doc);
        } catch (e) {
            useAssetBrowserStore.getState().setViewDoc(previous);
            setViewError(`Could not save the view: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setViewBusy(false);
        }
    };
    return { viewBusy, viewError, saveView };
}
