// The tab shell inside the tree drawer: `Files | Assets`, a fixed pair.
//
// NOT A REGISTRY. Out-of-tree code extends the browser through asset providers
// (collections inside `Assets`) and the existing `panels` slot, never by adding
// a tab; a tab slot would be a seam nothing pays for.
//
// THE FILES TAB IS TODAY'S TREE, UNTOUCHED, and it stays MOUNTED while hidden.
// Its react-arborist instance is what 3D picking selects into
// (`treeViewStore.tree`), so unmounting it on a tab switch would break
// pick -> row sync for as long as the Assets tab is open. Hidden, it keeps
// listening, and a node loaded from Assets appears in it as a normal source.
//
// The Assets tab needs a server (a scope, the asset routes), so outside REST
// mode -- the notebook and websocket viewers -- there is no tab strip at all and
// the drawer is exactly what it was.

import React from "react";

import { runtime } from "@/runtime/config";
import { useViewerStores } from "@/state/AdaViewerContext";
import type { AssetBrowserTab } from "@/state/assetBrowserStore";

import AssetsTab from "./AssetsTab";
import FilesTab from "./FilesTab";

export function assetsTabAvailable(): boolean {
    return runtime.isRestMode();
}

const TABS: readonly { id: AssetBrowserTab; label: string; title: string }[] = [
    { id: "files", label: "Files", title: "The loaded models' selection tree" },
    { id: "assets", label: "Assets", title: "Published asset collections in this scope" },
];

/** The header strip. Rendered in the drawer's title slot so the tree below keeps
 *  the height it had. */
export const AssetBrowserTabs: React.FC = () => {
    const { useAssetBrowserStore } = useViewerStores();
    const tab = useAssetBrowserStore((s) => s.tab);
    const setTab = useAssetBrowserStore((s) => s.setTab);
    return (
        <div role="tablist" aria-label="Browser" className="flex items-center gap-1">
            {TABS.map((t) => (
                <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === t.id}
                    title={t.title}
                    onClick={() => setTab(t.id)}
                    className={`px-2 -my-0.5 rounded-sm font-semibold ${
                        tab === t.id ? "bg-gray-700 text-white" : "text-gray-400 hover:text-white"
                    }`}
                >
                    {t.label}
                </button>
            ))}
        </div>
    );
};

const AssetBrowser: React.FC = () => {
    const { useAssetBrowserStore } = useViewerStores();
    const stored = useAssetBrowserStore((s) => s.tab);
    const withAssets = assetsTabAvailable();
    const tab: AssetBrowserTab = withAssets ? stored : "files";
    return (
        <>
            <div className="flex-1 overflow-auto" hidden={tab !== "files"}>
                <FilesTab />
            </div>
            {withAssets && tab === "assets" && (
                <div className="flex-1 min-h-0 flex flex-col">
                    <AssetsTab />
                </div>
            )}
        </>
    );
};

export default AssetBrowser;
