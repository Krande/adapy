import React from 'react';
import {SimpleTreeView, TreeItem} from '@mui/x-tree-view'; // Ensure you have @mui/lab installed
import {useTreeViewStore, TreeNode} from '../../state/treeViewStore';

const TreeViewComponent: React.FC = () => {
    const { treeData, selectedNodeId, setSelectedNodeId } = useTreeViewStore();

    const handleNodeSelect = (event: React.SyntheticEvent, itemIds: string | null) => {
        if (itemIds !== null) {
            setSelectedNodeId(itemIds);
        }
    };


    const renderTree = (nodes: TreeNode) => (
        <TreeItem key={nodes.id} itemId={nodes.id} label={nodes.name} sx={{ color: "white"}}>
            {Array.isArray(nodes.children)
                ? nodes.children.map((node: TreeNode) => renderTree(node))
                : null}
        </TreeItem>
    );

    return (
        <div className={"h-full max-h-screen overflow-y-auto"}>
            <SimpleTreeView selectedItems={selectedNodeId || ''} onSelectedItemsChange={handleNodeSelect}>
                {treeData && renderTree(treeData)}
            </SimpleTreeView>
        </div>

    );
};

export default TreeViewComponent;
