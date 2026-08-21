import React, {useEffect, useMemo, useRef, useState} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {NodeApi, Tree} from "react-arborist";
import {CustomNode} from './CustomNode';
import {handleTreeSelectionChange} from "@/utils/tree_view/handleClickedNode";
import {useModeStore} from "@/shell/modeStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {isFEAResult, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {filterRoots} from "@/shell/outlinerFilter";
import {Splitter} from "@/components/ui";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {applyFeaGroupVisibility, clearFeaGroupVisibility} from "@/shell/feaSetIsolation";
import {type FeaSet, unionMembers} from "@/shell/feaSets";
import {OutlinerGroups} from "./OutlinerGroups";

const nf = new Intl.NumberFormat();

const TreeViewComponent: React.FC = () => {
    const {useTreeViewStore} = useViewerStores();
    const {treeData, setTree, searchTerm, scopeNodeId, scopeNodeName, setScope} = useTreeViewStore();
    const [treeHeight, setTreeHeight] = useState<number>(800); // Default height
    const treeRef = useRef<any>(null);  // Use 'any' to allow custom properties
    const containerRef = useRef<HTMLDivElement | null>(null);
    // The tree's own area is measured directly rather than derived as
    // container-minus-header. The old arithmetic only re-ran when the CONTAINER resized,
    // so anything that changed the header's height — the scope chip, the "N more loaded"
    // button, and now the Groups section below — left the tree sized for a layout that no
    // longer existed, either clipped or overflowing.
    const treeAreaRef = useRef<HTMLDivElement | null>(null);

    // Named sets from the loaded result. The whole manifest is already in this store, so
    // the Groups section needs no store, no fetch and no loading state of its own.
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const sets: FeaSet[] = useMemo(() => (manifest?.groups as FeaSet[] | undefined) ?? [], [manifest]);
    const modelInfo = manifest?.model_info ?? null;
    const [selectedGroups, setSelectedGroups] = useState<ReadonlySet<string>>(() => new Set<string>());
    const [wireframeRest, setWireframeRest] = useState(true);
    const [groupsCollapsed, setGroupsCollapsed] = useState(false);
    const [groupsHeight, setGroupsHeight] = useState(240);

    // A result swap must not leave the previous model's groups selected — the names would
    // be meaningless against the new mesh and the isolation would survive as hidden
    // geometry nobody could account for.
    useEffect(() => {
        setSelectedGroups(new Set());
        clearFeaGroupVisibility();
    }, [manifest]);

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

    // react-arborist needs an explicit pixel height, so measure the flex track it sits in
    // and hand back what the browser already worked out.
    useEffect(() => {
        const el = treeAreaRef.current;
        if (!el) return;
        const update = () => setTreeHeight(el.clientHeight);
        const resizeObserver = new ResizeObserver(update);
        resizeObserver.observe(el);
        update();
        return () => resizeObserver.disconnect();
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
            <div className={"w-full shrink-0 pr-1 pt-1"}>
                <input
                    className={"w-full bg-surface-3 text-white rounded-sm pl-1"}
                    placeholder={scopeNodeId ? `Search in ${scopeNodeName ?? "selection"}` : "Search here"}
                    onInput={
                    (event) => {
                        useTreeViewStore.getState().setSearchTerm((event.target as HTMLInputElement).value);
                    }
                }/>
                {/* What the analysis is made of. One line, under the search, because it is
                    reference material you glance at -- not something worth a panel. Sizes
                    the work before any group is picked: a mesh reports triangles, this
                    reports the NODES and ELEMENTS the analysis actually had. */}
                {modelInfo && (
                    <p className="mt-1 truncate text-[10px] tabular-nums text-content-subtle"
                       title={`${nf.format(modelInfo.n_nodes)} nodes, ${nf.format(modelInfo.n_elements)} elements${
                           modelInfo.super_elements.length > 1
                               ? `, ${modelInfo.super_elements.length} super-elements`
                               : ""
                       }`}>
                        {nf.format(modelInfo.n_nodes)} nodes · {nf.format(modelInfo.n_elements)} elements
                        {modelInfo.super_elements.length > 1 && ` · ${modelInfo.super_elements.length} SE`}
                    </p>
                )}
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
                                className="ml-1 font-bold pointer-fine:hover:text-fail"
                                onClick={() => setScope(null, null)}
                                aria-label="Clear search scope"
                            >
                                ×
                            </button>
                        </span>
                    </div>
                )}
            </div>
            <div ref={treeAreaRef} className="min-h-0 flex-1">
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

            {/* Groups sits under the tree rather than inside it: a section can carry its
                own controls, scroll independently of a 2,461-element tree, and be resized.
                A tree row can do none of those. The search box above filters both. */}
            {sets.length > 0 && (
                <>
                    {!groupsCollapsed && (
                        <Splitter
                            orientation="horizontal"
                            side="after"
                            value={groupsHeight}
                            onChange={setGroupsHeight}
                            min={96}
                            max={640}
                            label="Resize the Groups list"
                        />
                    )}
                    <OutlinerGroups
                        sets={sets}
                        query={searchTerm ?? ""}
                        collapsed={groupsCollapsed}
                        onToggleCollapsed={() => setGroupsCollapsed((c) => !c)}
                        selected={selectedGroups}
                        onSelectedChange={setSelectedGroups}
                        wireframeRest={wireframeRest}
                        onWireframeRestChange={setWireframeRest}
                        height={groupsHeight}
                    />
                </>
            )}
        </div>
    );
};

export default TreeViewComponent;
