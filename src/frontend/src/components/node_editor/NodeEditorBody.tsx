// The node editor's content: the flow graph of procedure and file nodes. Its
// frames -- the floating window of the classic UI, or the whole page in
// node-editor-only mode -- are `NodeEditorComponent`'s.

import React from 'react';
import {Background, Controls, MiniMap, ReactFlow} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {useNodeEditorStore} from '@/state/useNodeEditorStore';
import ProcedureNode from './nodes/procedure_node/ProcedureNode';
import CustomFileObjectNode from './nodes/file_node/CustomFileObjectNode';
import {onDelete} from "@/utils/node_editor/handlers/on_delete";

const nodeTypes = {
    procedure: ProcedureNode,
    file_object: CustomFileObjectNode,
};

const NodeEditorBody: React.FC = () => {
    const {nodes, edges, onNodesChange, onEdgesChange, onConnect} = useNodeEditorStore();
    return (
        <ReactFlow
            colorMode={"dark"}
            nodes={nodes}
            edges={edges}
            onNodesChange={(changes) => onNodesChange(changes)}
            onEdgesChange={(changes) => onEdgesChange(changes)}
            onConnect={onConnect}
            nodeTypes={nodeTypes}
            onDelete={onDelete}
            fitView
        >
            <Background/>
            <Controls/>
            <MiniMap/>
        </ReactFlow>
    );
};

export default NodeEditorBody;
