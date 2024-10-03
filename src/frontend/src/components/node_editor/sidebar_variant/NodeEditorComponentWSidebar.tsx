import React, { useCallback, useRef } from 'react';
import { Rnd } from 'react-rnd';
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  addEdge,
  Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { request_list_of_nodes } from "../../../utils/node_editor/request_list_of_nodes";
import { useNodeEditorStore } from '../../../state/useNodeEditorStoreWSidebar';
import ProcedureNode from '../nodes/customProcedureNode';
import CustomFileObjectNode from '../nodes/customFileObjectNode';
import Sidebar from './Sidebar';
import { useDnD } from './DnDContext';

// Define custom data types for nodes and edges
type CustomNodeData = Record<string, unknown>;
type NodeType = Node<CustomNodeData>;

const nodeTypes = {
  procedure: ProcedureNode,
  file_object: CustomFileObjectNode,
};

const NodeEditorComponentWSidebar: React.FC = () => {
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();
  const [type] = useDnD();

  // Access Zustand state and actions using hooks, ensure nodes and edges are correctly typed
  const {
    nodes = [], // Ensure nodes is an array of Node<CustomNodeData>
    edges,
    setNodes,
    setEdges,
    onNodesChange,
    onEdgesChange,
    onConnect,
  } = useNodeEditorStore();

  // Handle drag over event
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  // Handle drop event
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();

      // Check if the dropped element is valid
      if (!type) {
        return;
      }

      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      // Create a new node using the correct type for `nodes` with generic data
      const newNode: NodeType = {
        id: `dndnode_${nodes.length}`, // Use a unique ID for the new node
        type,
        position,
        data: { label: `${type} node` },
      };

      // Use functional update to ensure that setNodes is updating the latest state correctly
      //setNodes((nds: NodeType[]) => [...nds, newNode]);
    },
    [type, screenToFlowPosition, setNodes, nodes.length]
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
      style={{ zIndex: 1000, background: 'white', border: '1px solid #ccc' }}
      dragHandleClassName="node-editor-drag-handle"
    >
      {/* Header Area */}
      <div className="node-editor-header node-editor-drag-handle bg-gray-800 text-white px-4 py-2 cursor-move">
        <div className={"flex flex-row"}>
          <div className={"flex"}>Node Editor</div>
          <button
            className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white px-4 ml-2 rounded"}
            onClick={() => request_list_of_nodes()}
          >
            Update
          </button>
        </div>
      </div>

      {/* Content Area */}
      <div style={{ width: '100%', height: 'calc(100% - 40px)', display: 'flex' }}>
        {/* React Flow Editor */}
        <div
          className="reactflow-wrapper"
          ref={reactFlowWrapper}
          style={{ width: '80%', height: '100%' }}
        >
          <ReactFlow
              colorMode={"dark"}
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onDrop={onDrop}
            onDragOver={onDragOver}
            nodeTypes={nodeTypes}
            fitView
          >
            <Background />
            <Controls />
            <MiniMap />
          </ReactFlow>
        </div>

        {/* Sidebar Component */}
        <div style={{ width: '20%', height: '100%', backgroundColor: '#f0f0f0', padding: '10px' }}>
          <Sidebar />
        </div>
      </div>
    </Rnd>
  );
};

export default NodeEditorComponentWSidebar;
