import React, {useEffect, useRef, useState} from 'react';
import {useTreeViewStore} from '../../state/treeViewStore';
import {Tree} from "react-arborist";
import {CustomNode} from './CustomNode';

const TreeViewComponent: React.FC = () => {
    const { treeData, selectedNodeId, setSelectedNodeId } = useTreeViewStore();
    const containerRef = useRef<HTMLDivElement | null>(null);
    const [treeHeight, setTreeHeight] = useState<number>(800); // Default height

    const treeNodes = treeData ?  [{id: 'root', name: 'scene', children: [treeData]}] : [{id: 'root', name: 'root', children: []}];

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

    return (
        <div ref={containerRef} className="h-full max-h-screen overflow-y-auto pr-2">
            <Tree
                className={"text-white"}
                width={"100%"}
                height={treeHeight} // Use the dynamic height
                selectionFollowsFocus={true}
                data={treeNodes}
                selection={selectedNodeId ? selectedNodeId : "root"}
                disableDrag={true}
                disableDrop={true}
                disableEdit={true}
                openByDefault={false}
                disableMultiSelection={false}

                onSelect={(ids) => {
                  if (ids.length > 0) {
                    setSelectedNodeId(ids[0].id);
                  } else {
                    setSelectedNodeId(null);
                  }
                }}
            >
                {CustomNode}
            </Tree>
        </div>
    );
};

export default TreeViewComponent;
