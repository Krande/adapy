import React, {useEffect, useRef, useState} from 'react';
import {useTreeViewStore} from '../../state/treeViewStore';
import {NodeApi, Tree} from "react-arborist";
import {CustomNode} from './CustomNode';
import {handleTreeSelectionChange} from "../../utils/tree_view/handleClickedNode";

const TreeViewComponent: React.FC = () => {
    const {treeData, setTree, searchTerm} = useTreeViewStore();
    const containerRef = useRef<HTMLDivElement | null>(null);
    const [treeHeight, setTreeHeight] = useState<number>(800); // Default height
    const treeRef = useRef<any>();  // Use 'any' to allow custom properties

    const treeNodes = treeData ? [{id: 'root', name: 'scene', children: [treeData]}] : [{
        id: 'root',
        name: 'root',
        children: []
    }];

    // Update the tree height based on the container size using ResizeObserver
    useEffect(() => {
        const updateTreeHeight = () => {
            if (containerRef.current) {
                setTreeHeight(containerRef.current.clientHeight);
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
            handleTreeSelectionChange(ids);
        }
    };

    return (
        <div ref={containerRef} className="h-full max-h-screen overflow-y-auto pl-1 pt-1 pr-1">
            <div className={"h-10"}>
                <input className={"bg-blue-800 text-white"} onInput={
                    (event) => {
                        useTreeViewStore.getState().setSearchTerm((event.target as HTMLInputElement).value);
                    }
                }/>
            </div>
            <div>
                <Tree
                    className={"text-white"}
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
                        (node, term) => node.data.name.toLowerCase().includes(term.toLowerCase())
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
