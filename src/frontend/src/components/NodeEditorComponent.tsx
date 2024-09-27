import React from 'react';
import {Rnd} from 'react-rnd';
import {
    ReactFlow,
    addEdge,
    Background,
    Controls,
    MiniMap,
    useEdgesState,
    useNodesState,
    OnConnect,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

const NodeEditorComponent: React.FC = () => {
    const initialNodes = [
        {
            id: '1',
            type: 'input',
            data: {label: 'Start Procedure'},
            position: {x: 250, y: 0},
        },
    ];

    const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);

    const onConnect: OnConnect = React.useCallback(
        (params) => setEdges((eds) => addEdge(params, eds)),
        [setEdges]
    );

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
                Node Editor
            </div>
            {/* Content Area */}
            <div style={{width: '100%', height: 'calc(100% - 40px)'}}>
                <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
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
