import React from 'react';
import { useDnD } from './DnDContext';
import { useNodeEditorStore } from '../../../state/useNodeEditorStoreWSidebar'; // Import Zustand store

type DragEvent = React.DragEvent<HTMLDivElement>;

export default function Sidebar() {
  const [_, setType] = useDnD();
  const availableNodes = useNodeEditorStore((state) => state.availableNodes); // Get available nodes from the store

  const onDragStart = (event: DragEvent, nodeType: string) => {
    setType(nodeType);
    event.dataTransfer.effectAllowed = 'move';
  };

  return (
    <aside>
      <div className="description">You can drag these nodes to the pane on the right.</div>
      {/* Dynamically render available node instances */}
      {availableNodes.length > 0 ? (
        availableNodes.map((node) => (
          <div
            key={node.type}
            className="dndnode"
            onDragStart={(event) => onDragStart(event, node.type)}
            draggable
          >
            {node.label}
            <div style={{ border: '1px solid #ccc', padding: '10px', margin: '5px 0' }}>
              <node.instance />
            </div>
          </div>
        ))
      ) : (
        <div>No nodes available. Click "Update" to load nodes.</div>
      )}
    </aside>
  );
}
