import React, {useEffect, useMemo, useRef, useState, useSyncExternalStore} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {NodeApi, Tree} from "react-arborist";
import {CustomNode, SceneTreeRowContext, TreeNodeData, type SceneTreeRowOptions} from './CustomNode';
import {sceneTreeRow} from './SceneTreeRow';
import {useSceneMenuStore} from '@/state/sceneMenuStore';
import {handleTreeSelectionChange} from "@/utils/tree_view/handleClickedNode";
import {syncSceneTreeToSelection} from "@/utils/tree_view/revealPick";
import {closeTreeFromKeyboard, isTreeCloseKey} from "@/utils/tree_view/treeKeyboard";
import {clearScene} from "@/utils/scene/handlers/clear_scene";
import {claimSceneTreeSelection, sceneTreeContributedRoots, subscribeSceneTreeRoots} from "@/plugins/sceneTreeRoots";

/** What a `rootFilter` lists of the model roots, and how many it left out. */
export interface SceneTreeRootFilter {
    shown: TreeNodeData[];
    hidden: number;
}

/** The text of the root filter's toggle. */
export interface SceneTreeRootFilterLabels {
    showAll(hidden: number): string;
    filter: string;
}

const ROOT_FILTER_LABELS: SceneTreeRootFilterLabels = {
    showAll: (hidden) => `${hidden} more loaded — show all`,
    filter: "Showing all — filter",
};

/** Options of the Scene tree. Absent, each leaves the tree as it is. */
export interface SceneTreeProps {
    /** Narrows the model roots. Called with `showAll` true after the user asks for every root;
     *  while it hides some, a toggle under the search offers them back. */
    rootFilter?: (roots: TreeNodeData[], showAll: boolean) => SceneTreeRootFilter;
    /** That toggle's text. Default "N more loaded — show all" / "Showing all — filter". */
    rootFilterLabels?: SceneTreeRootFilterLabels;
    /** Rows appended after the model roots (and before any a plugin contributes). */
    extraRoots?: TreeNodeData[];
    /** Runs before the tree's own selection handler, with the selected row ids; true = handled. */
    onSelectOverride?: (ids: string[]) => boolean;
    /** Rendered under the search row. */
    header?: React.ReactNode;
    /** Drawn at each row's end, before its eye (a badge, a property chip). */
    rowExtras?: SceneTreeRowOptions["rowExtras"];
    /** Classes added to a row. */
    rowClassName?: SceneTreeRowOptions["rowClassName"];
}

const TreeViewComponent: React.FC<SceneTreeProps> = ({rootFilter, rootFilterLabels = ROOT_FILTER_LABELS, extraRoots, onSelectOverride, header, rowExtras, rowClassName}) => {
    const rowOptions = React.useMemo(() => ({rowExtras, rowClassName}), [rowExtras, rowClassName]);
    const {useTreeViewStore} = useViewerStores();
    const {treeData, setTree, searchTerm, scopeNodeId, scopeNodeName, setScope, rootLabelMode, setRootLabelMode} = useTreeViewStore();
    const {useModelState} = useViewerStores();
    const loadedCount = useModelState((s) => s.loadedSourceNames.size);
    const [clearing, setClearing] = useState(false);
    const [showAllRoots, setShowAllRoots] = useState(false);
    const contributedRoots = useSyncExternalStore(subscribeSceneTreeRoots, sceneTreeContributedRoots, sceneTreeContributedRoots);
    const [treeHeight, setTreeHeight] = useState<number>(800); // Default height
    const treeRef = useRef<any>(null);  // Use 'any' to allow custom properties
    const containerRef = useRef<HTMLDivElement | null>(null);
    const headerRef = useRef<HTMLDivElement | null>(null);
    // One row component for the tree's life: arborist re-mounts every row when it changes.
    const Row = useMemo(
        () =>
            sceneTreeRow((node: NodeApi<TreeNodeData>, x, y) =>
                useSceneMenuStore.getState().open({
                    row: node.data,
                    rows: (node.tree.selectedNodes.length ? node.tree.selectedNodes : [node]).map((n) => n.data),
                    node,
                    x,
                    y,
                }),
            ),
        [],
    );

    // Top level = one root per loaded model (labelled by GLB filename). The
    // store keeps them under a synthetic container; render its children.
    const modelRoots = treeData?.children ?? [];
    const filtered = rootFilter ? rootFilter(modelRoots, showAllRoots) : null;
    const shownRoots = filtered?.shown ?? modelRoots;
    const hiddenRoots = filtered?.hidden ?? 0;
    const treeNodes = useMemo(
        () =>
            !extraRoots?.length && !contributedRoots.length
                ? shownRoots
                : [...shownRoots, ...(extraRoots ?? []), ...contributedRoots],
        [shownRoots, extraRoots, contributedRoots],
    );

    // Update the tree height based on the container size using ResizeObserver
    useEffect(() => {
        const updateTreeHeight = () => {
            if (containerRef.current && headerRef.current) {
                const containerHeight = containerRef.current.offsetHeight;
                const headerHeight = headerRef.current.offsetHeight;
                setTreeHeight(containerHeight - headerHeight);
            }
        };

        // Create a ResizeObserver to watch for changes in the container size
        const resizeObserver = new ResizeObserver(() => updateTreeHeight());
        if (containerRef.current) {
            resizeObserver.observe(containerRef.current);
        }
        // The header too: the scope chip, a `header` or the root-filter toggle change its height.
        if (headerRef.current) {
            resizeObserver.observe(headerRef.current);
        }

        // Set the initial height
        updateTreeHeight();

        // Cleanup the observer on component unmount
        return () => {
            resizeObserver.disconnect();
        };
    }, []);

    useEffect(() => {
        if (treeRef.current) {
            const tree = treeRef.current
            setTree(tree);
            // Opened for the first time: show what is already selected in 3D, rather than an empty
            // tree whose first selection event would clear it.
            syncSceneTreeToSelection();
        }
    }, []);

    // Back from hidden (the Sources tab was showing, or the drawer was shut): take the 3D view's
    // selection -- picks made while the drawer was shut did not reach this tree -- and bring it into
    // view again.
    const wasHidden = useRef(false);
    useEffect(() => {
        if (treeHeight <= 0) {
            wasHidden.current = true;
            return;
        }
        if (!wasHidden.current) return;
        wasHidden.current = false;
        if (syncSceneTreeToSelection()) return;
        const tree = treeRef.current;
        const node = tree?.mostRecentNode ?? tree?.selectedNodes?.[0];
        if (node) tree.scrollTo(node.id);
    }, [treeHeight]);

    const handleSelect = (ids: NodeApi[]) => {
        // An EMPTY selection clears the 3D view only when it is the user's doing in this tree. The
        // tree also reports one on its own -- opening, focus arriving -- and that must not throw away
        // what was picked in 3D.
        if (ids.length === 0 && !containerRef.current?.contains(document.activeElement)) return;
        if (!treeRef.current?.isProgrammaticChange) {
            const rowIds = ids.map((n) => String(n.id));
            if (onSelectOverride?.(rowIds) || claimSceneTreeSelection(rowIds)) return;
            (async () => {
                await handleTreeSelectionChange(ids);
            })();
        }
    };

    return (
        <div ref={containerRef} className="h-full w-full flex flex-col max-h-screen pl-1 pr-2">
            <div ref={headerRef} className={"w-full pr-1 pt-2 pb-1"}>
                {/* The same controls as the Sources tab's: one height, one border, one radius. */}
                <div className="flex items-center gap-1.5">
                    <div className="h-7 flex-1 min-w-0 flex items-center gap-2 px-2 rounded-md border border-gray-700 bg-gray-800 focus-within:border-gray-500">
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" className="text-gray-400 shrink-0">
                            <circle cx="7" cy="7" r="4.5" />
                            <path d="M10.5 10.5 14 14" />
                        </svg>
                        <input
                            aria-label="Search the scene"
                            className="flex-1 min-w-0 bg-transparent outline-none text-[13px] text-gray-100 placeholder:text-gray-500"
                            placeholder={scopeNodeId ? `Search in ${scopeNodeName ?? "selection"}` : "Search names"}
                            value={searchTerm ?? ""}
                            onChange={(event) => useTreeViewStore.getState().setSearchTerm(event.target.value)}
                        />
                        {searchTerm && (
                            <button
                                type="button"
                                className="shrink-0 text-gray-400 hover:text-white"
                                aria-label="Clear search"
                                title="Clear search"
                                onClick={() => useTreeViewStore.getState().setSearchTerm("")}
                            >
                                ×
                            </button>
                        )}
                    </div>
                    {/* What each loaded model's root row is called: its top-level name, or the
                        unique id it was loaded under. */}
                    <button
                        type="button"
                        className="h-7 shrink-0 rounded-md border border-gray-700 bg-gray-800 px-2 text-xs text-gray-300 hover:bg-gray-700 hover:text-white"
                        title={
                            rootLabelMode === "name"
                                ? "Model roots show their top-level name. Click to show the unique id each was loaded under."
                                : "Model roots show the unique id each was loaded under. Click to show their top-level name."
                        }
                        onClick={() => setRootLabelMode(rootLabelMode === "name" ? "id" : "name")}
                    >
                        {rootLabelMode === "name" ? "Names" : "IDs"}
                    </button>
                    {/* The storage browser's Clear, from here: every model out of the scene. */}
                    {(modelRoots.length > 0 || loadedCount > 0) && (
                        <button
                            type="button"
                            className="h-7 shrink-0 rounded-md border border-gray-700 bg-gray-800 px-2 text-xs text-gray-300 hover:bg-gray-700 hover:text-white disabled:opacity-60"
                            title="Unload every model in the scene, and close any open procedural model"
                            aria-busy={clearing}
                            disabled={clearing}
                            onClick={() => {
                                setClearing(true);
                                void clearScene()
                                    .catch((err) => console.error("remove all failed", err))
                                    .finally(() => setClearing(false));
                            }}
                        >
                            {clearing ? "Removing…" : "Remove all"}
                        </button>
                    )}
                </div>
                {header}
                {/* A list that drops roots says so, and offers them back. */}
                {(hiddenRoots > 0 || (!!rootFilter && showAllRoots)) && (
                    <button
                        type="button"
                        className="mt-1.5 w-full rounded px-1 py-0.5 text-left text-xs text-gray-400 hover:bg-white/5 hover:text-gray-100"
                        title={showAllRoots ? "List only the filtered models" : `${hiddenRoots} other loaded model(s) are not listed; they are still in the 3D view`}
                        onClick={() => setShowAllRoots(!showAllRoots)}
                    >
                        {showAllRoots ? rootFilterLabels.filter : rootFilterLabels.showAll(hiddenRoots)}
                    </button>
                )}
                {scopeNodeId && (
                    <div className="mt-1.5 flex items-center">
                        <span
                            className="inline-flex items-center max-w-full text-[11px] bg-blue-900/60 text-blue-100 rounded-full px-2 py-0.5"
                            title={`Search scoped to ${scopeNodeName ?? "selection"}`}
                        >
                            <span className="truncate">scope: {scopeNodeName ?? "selection"}</span>
                            <button
                                className="ml-1 font-bold hover:text-red-300"
                                onClick={() => setScope(null, null)}
                                aria-label="Clear search scope"
                            >
                                ×
                            </button>
                        </span>
                    </div>
                )}
            </div>
            <div
                // No focus outlines anywhere in the tree: arborist's own focusable container takes no
                // className, and the browser outlined it (or the cursor row) on any key, Shift alone
                // included -- a box drawn over the selection highlight.
                className="[&_*]:outline-none"
                // Esc / Alt+T close the drawer while the tree has focus. Caught here, in the
                // capture phase, and stopped: the tree consumes its own keys, and letting Alt+T go on
                // to the viewer's global handler would reopen what this just closed.
                onKeyDownCapture={(e) => {
                    if (!isTreeCloseKey(e)) return;
                    e.preventDefault();
                    e.stopPropagation();
                    closeTreeFromKeyboard();
                }}
            >
                <SceneTreeRowContext.Provider value={rowOptions}>
                <Tree
                    // No focus ring: arborist moves DOM focus onto the cursor row, and the browser's
                    // outline drew a box around it on top of the selection highlight.
                    className={"text-white scrollbar outline-none"}
                    rowClassName={"outline-none"}
                    width={"100%"}
                    height={treeHeight} // Use the dynamic height
                    selectionFollowsFocus={true}
                    data={treeNodes}
                    ref={treeRef}
                    disableDrag={true}
                    disableDrop={true}
                    disableEdit={true}
                    openByDefault={false}
                    disableMultiSelection={false}
                    // Ctrl-click adds or removes a row, as Cmd-click does; right-click opens the row
                    // menu (see SceneTreeRow).
                    renderRow={Row}
                    searchTerm={searchTerm}
                    searchMatch={
                        (node, term) => {
                            // Scope to the selected node's subtree when one is
                            // selected; otherwise search all roots (matches stay
                            // under their root, so hits group per root).
                            if (scopeNodeId) {
                                let inScope = false;
                                let n: NodeApi | null = node;
                                while (n) {
                                    if (n.id === scopeNodeId) { inScope = true; break; }
                                    n = n.parent;
                                }
                                if (!inScope) return false;
                            }
                            const name = (node?.data?.name ?? '').toString().toLowerCase();
                            const raw = (term ?? '').toString().toLowerCase();
                            const candidates: string[] = [raw];
                            // If user wrapped the term in single quotes, also search for the inner text
                            if (raw.length >= 2 && raw.startsWith("'") && raw.endsWith("'")) {
                                candidates.push(raw.slice(1, -1));
                            }
                            return candidates.some((c) => c !== '' && name.includes(c));
                        }
                    }

                    // If I use this, it will also trigger when I modify the selection programmatically. And bad things happen.
                    onSelect={(ids) => {
                        handleSelect(ids);
                    }}
                >
                    {CustomNode}
                </Tree>
                </SceneTreeRowContext.Provider>
            </div>

        </div>
    );
};

export default TreeViewComponent;
