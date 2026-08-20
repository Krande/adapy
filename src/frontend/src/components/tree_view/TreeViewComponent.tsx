import React, {useEffect, useRef, useState} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {NodeApi, Tree} from "react-arborist";
import {CustomNode} from './CustomNode';
import {handleTreeSelectionChange} from "@/utils/tree_view/handleClickedNode";
import {useModeStore} from "@/shell/modeStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {isFEAResult, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {filterRoots} from "@/shell/outlinerFilter";

const TreeViewComponent: React.FC = () => {
    const {useTreeViewStore} = useViewerStores();
    const {treeData, setTree, searchTerm, scopeNodeId, scopeNodeName, setScope} = useTreeViewStore();
    const [treeHeight, setTreeHeight] = useState<number>(800); // Default height
    const treeRef = useRef<any>(null);  // Use 'any' to allow custom properties
    const containerRef = useRef<HTMLDivElement | null>(null);
    const headerRef = useRef<HTMLDivElement | null>(null);

    // Top level = one root per loaded model (labelled by GLB filename). The
    // store keeps them under a synthetic container; render its children.
    const allRoots = treeData?.children ?? [];

    // Each mode lists the models it is about: Build the procedural one, Results the ones
    // carrying results, Inspect everything.
    //
    // The LIST only. Nothing is hidden from the 3D view and nothing is unloaded — a model
    // that silently vanished from the scene on a mode switch would leave its reason
    // off-screen, and "my model disappeared" is a worse problem than the one this solves.
    // It also keeps the non-modality contract in modeStore: a mode changes what is
    // OFFERED, never what is loaded.
    const mode = useModeStore((s) => s.mode);
    const proceduralName = useCellBuilderStore((s) => s.active?.name ?? null);
    const [showAllRoots, setShowAllRoots] = useState(false);
    const {shown: treeNodes, hidden: hiddenRoots} = filterRoots(
        allRoots,
        (n: {name?: string; id?: string}) => n.name ?? n.id ?? "",
        mode,
        {proceduralName, isResult: (n) => isFEAResult(n) || isStreamingFEAResult(n)},
        showAllRoots,
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

    const handleSelect = (ids: NodeApi[]) => {
        if (!treeRef.current?.isProgrammaticChange) {
            (async () => {
                await handleTreeSelectionChange(ids);
            })();
        }
    };

    return (
        <div ref={containerRef} className="h-full w-full flex flex-col max-h-screen pl-1 pr-2">
            <div ref={headerRef} className={"w-full pr-1 pt-1"}>
                <input
                    className={"w-full bg-surface-3 text-white rounded-sm pl-1"}
                    placeholder={scopeNodeId ? `Search in ${scopeNodeName ?? "selection"}` : "Search here"}
                    onInput={
                    (event) => {
                        useTreeViewStore.getState().setSearchTerm((event.target as HTMLInputElement).value);
                    }
                }/>
                {/* A list that quietly drops rows is indistinguishable from one that
                    failed to load, so say how many and offer them back. */}
                {hiddenRoots > 0 && (
                    <button
                        type="button"
                        onClick={() => setShowAllRoots(true)}
                        className="mt-1 w-full rounded-sm px-1 py-0.5 text-left text-xs text-content-muted pointer-fine:hover:text-content pointer-fine:hover:bg-surface-2"
                        title={`This mode lists only its own models. ${hiddenRoots} other loaded model(s) are still in the 3D view.`}
                    >
                        {hiddenRoots} more loaded — show all
                    </button>
                )}
                {showAllRoots && (
                    <button
                        type="button"
                        onClick={() => setShowAllRoots(false)}
                        className="mt-1 w-full rounded-sm px-1 py-0.5 text-left text-xs text-content-muted pointer-fine:hover:text-content pointer-fine:hover:bg-surface-2"
                    >
                        Showing all — filter to this mode
                    </button>
                )}
                {scopeNodeId && (
                    <div className="mt-1 flex items-center">
                        <span
                            className="inline-flex items-center max-w-full text-xs bg-accent text-white rounded-full px-2 py-0.5"
                            title={`Search scoped to ${scopeNodeName ?? "selection"}`}
                        >
                            <span className="truncate">scope: {scopeNodeName ?? "selection"}</span>
                            <button
                                className="ml-1 font-bold hover:text-fail"
                                onClick={() => setScope(null, null)}
                                aria-label="Clear search scope"
                            >
                                ×
                            </button>
                        </span>
                    </div>
                )}
            </div>
            <div>
                <Tree
                    className={"text-white scrollbar"}
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
