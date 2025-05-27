import React from 'react';
import {Rnd} from 'react-rnd';
import {Background, Controls, MiniMap, ReactFlow} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {request_list_of_nodes} from "../../utils/node_editor/comms/request_list_of_nodes";
import {useNodeEditorStore} from '../../state/useNodeEditorStore'; // Import the Zustand store
import ProcedureNode from './nodes/procedure_node/ProcedureNode';
import CustomFileObjectNode from './nodes/file_node/customFileObjectNode';
import {onDelete} from "../../utils/node_editor/comms/on_delete";
import {start_new_node_editor} from "../../utils/node_editor/comms/start_new_node_editor";
import ReloadIcon from "../icons/ReloadIcon";
import PopOutIcon from "../icons/PopOutIcon";

const nodeTypes = {
    procedure: ProcedureNode,
    file_object: CustomFileObjectNode,
};

const NodeEditorComponent: React.FC = () => {
    // Access Zustand state and actions using hooks
    const {
        nodes,
        edges,
        setNodes,
        setEdges,
        onNodesChange,
        onEdgesChange,
        onConnect,
        use_node_editor_only
    } = useNodeEditorStore();

    const editorContent = (
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

    return use_node_editor_only ? (
        <div style={{width: '100%', height: '100%', background: 'white', border: '1px solid #ccc'}}>
            {editorContent}
        </div>
    ) : (
        <Rnd
            default={{
                x: 100,
                y: 100,
                width: 800,
                height: 600,
            }}
            bounds="window"
            style={{zIndex: 1000, background: 'white', border: '1px solid #ccc'}}
            dragHandleClassName="node-editor-drag-handle" // Restrict dragging to the header
        >
            {/* Header Area */}
            <div className="node-editor-header node-editor-drag-handle bg-gray-800 text-white px-4 py-2 cursor-move">
                <div className={"flex flex-row"}>
                    <div className={"flex p-1"}>Node Editor</div>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded"}
                        onClick={() => request_list_of_nodes()}
                    >
                        <ReloadIcon />
                    </button>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded"}
                        onClick={() => start_new_node_editor()}
                    >
                        <PopOutIcon />
                    </button>
                    {/*<div className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded"}>*/}
                    {/*    <input type="file" onChange={handleFileUpload}/>*/}
                    {/*</div>*/}
                </div>
            </div>
            {/* Content Area */}
            <div style={{width: '100%', height: 'calc(100% - 40px)'}}>
                {editorContent}
            </div>
        </Rnd>
    );
};

export default NodeEditorComponent;
