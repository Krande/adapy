// The tab shell inside the tree drawer: `Scene | Systems | Sources` (ids `files` / `systems` /
// `assets`), a fixed set. Systems is core's listing of the relations index (`SystemsTab`).
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

import React, { useEffect, useState } from "react";

import { isMirroredWindow } from "@/layout/sceneActions";
import { runtime } from "@/runtime/config";
import { useViewerStores } from "@/state/AdaViewerContext";
import type { AssetBrowserTab } from "@/state/assetBrowserStore";
import type { SceneTreeProps } from "@/components/tree_view/TreeViewComponent";

import AssetsTabView from "./AssetsTab";
import FilesTabView from "./FilesTab";
import SystemsTab from "./SystemsTab";

// Memoized: neither takes props, and both re-render from their own stores. Without it every tab
// switch -- and every drawer resize, which a switch also makes (a width per tab) -- re-rendered both
// whole trees from here.
const AssetsTab = React.memo(AssetsTabView);
const FilesTab = React.memo(FilesTabView);

export function assetsTabAvailable(): boolean {
    return runtime.isRestMode();
}

/** Is this tab offered in this window? A pop-out window has the Scene and Systems tabs: Sources
 *  loads models into a scene it does not have. */
function offered(tab: AssetBrowserTab): boolean {
    return !(isMirroredWindow() && tab === "assets");
}

/** The tab to show: the stored one, or the Scene tab when that is not offered here. */
function shownTab(stored: AssetBrowserTab): AssetBrowserTab {
    return offered(stored) ? stored : "files";
}

const TABS: readonly { id: AssetBrowserTab; label: string; title: string }[] = [
    // Ids kept from the first names ("files" / "assets"): they are the store's values and nobody
    // reads them. The labels say what a user sees -- what is in the scene, and the provider-backed
    // trees it can be loaded from -- not where either was stored.
    { id: "files", label: "Scene", title: "What is loaded in the scene" },
    { id: "systems", label: "Systems", title: "The systems of the loaded collections -- HVAC, piping -- from the relations providers publish" },
    { id: "assets", label: "Sources", title: "Project trees from the providers, published in this scope" },
];

/** The header strip. Rendered in the drawer's title slot so the tree below keeps
 *  the height it had. */
export const AssetBrowserTabs: React.FC = () => {
    const { useAssetBrowserStore } = useViewerStores();
    const stored = useAssetBrowserStore((s) => s.tab);
    const setTab = useAssetBrowserStore((s) => s.setTab);
    const tab = shownTab(stored);
    const tabs = TABS.filter((t) => offered(t.id));
    return (
        <div role="tablist" aria-label="Browser" className="flex items-center gap-0.5 rounded-md bg-gray-900/60 p-0.5">
            {tabs.map((t) => (
                <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={tab === t.id}
                    title={t.title}
                    onClick={() => setTab(t.id)}
                    className={`h-7 px-3 rounded text-xs font-semibold transition-colors ${
                        tab === t.id ? "bg-gray-700 text-white shadow-sm" : "text-gray-400 hover:text-gray-100"
                    }`}
                >
                    {t.label}
                </button>
            ))}
        </div>
    );
};

export interface TreeBodyProps {
    /** Options of the Scene tab's tree. */
    sceneTree?: SceneTreeProps;
}

const AssetBrowser: React.FC<TreeBodyProps> = ({ sceneTree }) => {
    const { useAssetBrowserStore } = useViewerStores();
    const stored = useAssetBrowserStore((s) => s.tab);
    const withAssets = assetsTabAvailable();
    const tab: AssetBrowserTab = withAssets ? shownTab(stored) : "files";
    // THE ASSETS TAB, TOO, IS KEPT once opened. Mounted only while selected, every click on it
    // re-read the collections, re-merged every collection index and rebuilt the whole view on the
    // main thread -- the freeze on switching to it. Mounted on the first visit (a viewer who never
    // opens it pays nothing), then hidden like the Files tab above.
    const [assetsVisited, setAssetsVisited] = useState(tab === "assets");
    useEffect(() => {
        if (tab === "assets") setAssetsVisited(true);
    }, [tab]);
    return (
        <>
            <div className="flex-1 overflow-auto" hidden={tab !== "files"}>
                <FilesTab {...sceneTree} />
            </div>
            {withAssets && tab === "systems" && (
                <div className="flex-1 min-h-0 flex flex-col">
                    <SystemsTab />
                </div>
            )}
            {withAssets && offered("assets") && (assetsVisited || tab === "assets") && (
                <div className={`flex-1 min-h-0 flex-col ${tab === "assets" ? "flex" : "hidden"}`} hidden={tab !== "assets"}>
                    <AssetsTab />
                </div>
            )}
        </>
    );
};

export default AssetBrowser;
