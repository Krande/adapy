import React from 'react';
import { ReactFlowProvider } from '@xyflow/react';
import NodeEditorComponentWSidebar from './NodeEditorComponentWSidebar';
import { DnDProvider } from './DnDContext';

// Parent wrapper for NodeEditorComponent
const NodeEditorWrapper: React.FC = () => {
  return (
    <ReactFlowProvider>
      <DnDProvider>
        <NodeEditorComponentWSidebar />
      </DnDProvider>
    </ReactFlowProvider>
  );
};

export default NodeEditorWrapper;
