import React from 'react';
import {Rnd} from 'react-rnd';
import {Background, Controls, MiniMap, ReactFlow,} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {request_list_of_nodes} from "../../utils/node_editor/request_list_of_nodes";
import {useNodeEditorStore} from '../../state/useNodeEditorStore'; // Import the Zustand store
import InfoPanel from './InfoPanel';
import {run_sequence} from "../../utils/node_editor/run_sequence"; // Import the InfoPanel component

const info_svg = <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}
                      stroke="currentColor" className="w-6 h-6">
    <path strokeLinecap="round" strokeLinejoin="round"
          d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"/>
</svg>

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
    } = useNodeEditorStore();

    return (
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
                    <div className={"flex"}>Node Editor</div>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white font-bold px-4 ml-2 rounded"}
                        onClick={() => request_list_of_nodes()}
                    >
                        Update
                    </button>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white font-bold px-4 ml-2 rounded"}
                        onClick={() => run_sequence()}
                    >
                        Run
                    </button>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white font-bold px-4 ml-2 rounded"}
                        onClick={() => console.log("Info Panel")}
                    >{info_svg}</button>
                </div>
            </div>
            {/* Content Area */}
            <div style={{width: '100%', height: 'calc(100% - 40px)'}}>
                <ReactFlow
                    colorMode={"dark"}
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={(changes) => onNodesChange(changes)}
                    onEdgesChange={(changes) => onEdgesChange(changes)}
                    onConnect={onConnect}
                    fitView
                >
                    <Background/>
                    <Controls/>
                    <MiniMap/>
                </ReactFlow>
            </div>
        </Rnd>

    );
};

export default NodeEditorComponent;
