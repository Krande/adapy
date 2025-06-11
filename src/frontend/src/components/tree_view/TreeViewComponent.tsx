import React, {useEffect, useRef, useState} from 'react';
import {useTreeViewStore} from '../../state/treeViewStore';
import {NodeApi, Tree} from "react-arborist";
import {CustomNode} from './CustomNode';
import {handleTreeSelectionChange} from "../../utils/tree_view/handleClickedNode";

const TreeViewComponent: React.FC = () => {
    const {treeData, setTree, searchTerm} = useTreeViewStore();
    const [treeHeight, setTreeHeight] = useState<number>(800); // Default height
    const treeRef = useRef<any>(null);  // Use 'any' to allow custom properties
    const containerRef = useRef<HTMLDivElement | null>(null);
    const headerRef = useRef<HTMLDivElement | null>(null);

    const treeNodes = treeData ? [{id: 'root', name: 'scene', children: [treeData]}] : [{
        id: 'root',
        name: 'scene',
        children: []
    }];

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
            <div ref={headerRef} className={"w-full pr-12 pt-1 "}>
                <input className={"w-full bg-gray-600 text-white rounded pl-1"} onInput={
                    (event) => {
                        useTreeViewStore.getState().setSearchTerm((event.target as HTMLInputElement).value);
                    }
                }/>
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
