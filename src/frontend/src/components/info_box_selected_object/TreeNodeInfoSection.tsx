import React, {useMemo} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {buildTreeIndices, describeNode} from '@/utils/tree_view/treeGraph';

/**
 * Structural facts about the selected hierarchy node.
 *
 * The rest of this panel describes a *physical object*: it looks metadata up by
 * display name in a map that only holds geometry-bearing entries. Selecting a
 * **spatial container** therefore produced an empty panel — the container has
 * no draw range and no metadata row, so every section below it rendered
 * nothing, and the user got a name and two buttons.
 *
 * Everything shown here is already in the client: the selection tree the model
 * loader built carries each node's id, parent, children and whether it maps to
 * a merged mesh. No request, no new server field. It renders for any tree
 * selection — a container gains a panel where it had none, and a physical
 * object gains its position in the hierarchy, which is useful on its own.
 */
const TreeNodeInfoSection: React.FC = () => {
    const {useObjectInfoStore, useTreeViewStore} = useViewerStores();
    const selectedNodeId = useObjectInfoStore((s) => s.selectedNodeId);
    const treeData = useTreeViewStore((s) => s.treeData);

    // Keyed on treeData identity: the loader replaces the whole tree on every
    // model load, so this rebuilds exactly when the tree actually changes
    // rather than on every selection.
    const indices = useMemo(() => (treeData ? buildTreeIndices(treeData) : null), [treeData]);

    const facts = useMemo(() => {
        if (!indices || !selectedNodeId) return null;
        const node = indices.byId.get(selectedNodeId);
        return node ? describeNode(node, indices) : null;
    }, [indices, selectedNodeId]);

    if (!facts) return null;

    // A node with no geometry of its own is a container. Say which it is
    // plainly: "nothing rendered" and "this is a grouping level" look identical
    // in a blank panel, and only one of them is a problem.
    const isContainer = !facts.hasGeometry;

    return (
        <div className="mt-2 border-t border-gray-500/40 pt-2 text-xs">
            <div className="font-semibold mb-1">
                {isContainer ? "Spatial container" : "Hierarchy"}
            </div>
            <div className="table w-full">
                {isContainer && (
                    <div className="table-row">
                        <div className="table-cell w-32 opacity-70">Contains:</div>
                        <div className="table-cell">
                            {facts.childCount} direct{facts.childCount === 1 ? " child" : " children"}
                            {", "}
                            {facts.descendantGeometryCount} with geometry
                        </div>
                    </div>
                )}
                {facts.ancestry.length > 0 && (
                    <div className="table-row">
                        <div className="table-cell w-32 opacity-70 align-top">In:</div>
                        <div className="table-cell break-all">
                            {/* Nearest first, so the immediately-containing level
                                is the one the eye lands on. */}
                            {facts.ancestry.map((a) => (
                                <div key={a.id}>{a.name}</div>
                            ))}
                        </div>
                    </div>
                )}
                <div className="table-row">
                    <div className="table-cell w-32 opacity-70">Node id:</div>
                    <div className="table-cell break-all select-text opacity-70">{facts.id}</div>
                </div>
            </div>
        </div>
    );
};

export default TreeNodeInfoSection;
