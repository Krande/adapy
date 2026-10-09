import React, {useEffect, useMemo, useRef, useState} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {NodeApi, Tree} from "react-arborist";
import {CustomNode, TreeNodeData} from './CustomNode';
import {sceneTreeRow} from './SceneTreeRow';
import {useSceneMenuStore} from '@/state/sceneMenuStore';
import {handleTreeSelectionChange} from "@/utils/tree_view/handleClickedNode";
import {closeTreeFromKeyboard, isTreeCloseKey} from "@/utils/tree_view/treeKeyboard";

const TreeViewComponent: React.FC = () => {
    const {useTreeViewStore} = useViewerStores();
    const {treeData, setTree, searchTerm, scopeNodeId, scopeNodeName, setScope, rootLabelMode, setRootLabelMode} = useTreeViewStore();
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
    const treeNodes = treeData?.children ?? [];

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
        }
    }, []);

    // Back from hidden (the Sources tab was showing, or the drawer was shut): bring the selection
    // into view again. A pick made meanwhile selected its row here, but scrolled a list zero rows
    // tall, so the row is selected somewhere off screen.
    const wasHidden = useRef(false);
    useEffect(() => {
        if (treeHeight <= 0) {
            wasHidden.current = true;
            return;
        }
        if (!wasHidden.current) return;
        wasHidden.current = false;
        const tree = treeRef.current;
        const node = tree?.mostRecentNode ?? tree?.selectedNodes?.[0];
        if (node) tree.scrollTo(node.id);
    }, [treeHeight]);

    const handleSelect = (ids: NodeApi[]) => {
        if (!treeRef.current?.isProgrammaticChange) {
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
                </div>
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
            </div>

        </div>
    );
};

export default TreeViewComponent;
