import React from 'react';
import {Background, Controls, MiniMap, ReactFlow} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {useNodeEditorStore} from '@/state/useNodeEditorStore';
import ProcedureNode from './nodes/procedure_node/ProcedureNode';
import CustomFileObjectNode from './nodes/file_node/CustomFileObjectNode';
import {onDelete} from "@/utils/node_editor/handlers/on_delete";

// The ReactFlow canvas, with no chrome of its own.
//
// Extracted VERBATIM from NodeEditorComponent so the classic floating window and the
// shell's dock panel render the same editor rather than two copies that drift. Only the
// surrounding frame differs: the classic UI wraps this in its react-rnd window, the
// shell lets the dock provide the frame.

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
