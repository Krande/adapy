import { create } from 'zustand';
import { Node, Edge, OnConnect, addEdge, NodeChange, EdgeChange, applyNodeChanges, applyEdgeChanges } from '@xyflow/react';
import ProcedureNode from '../components/node_editor/nodes/customProcedureNode';
import CustomFileObjectNode from '../components/node_editor/nodes/customFileObjectNode';

// Define custom types for Node and Edge data
type CustomNodeData = Record<string, unknown>;
type CustomEdgeData = Record<string, unknown>;

// Define the type for available node options
type AvailableNodeType = {
  type: string;
  label: string;
  instance: React.FC; // Store the node instance as a React component
};

// Define the type for the store's state using generics for Node and Edge
type NodeEditorState = {
  nodes: Node<CustomNodeData>[]; // Use generics for Node type
  edges: Edge<CustomEdgeData>[]; // Use generics for Edge type
  availableNodes: AvailableNodeType[]; // Store the available nodes with instances
  setNodes: (nodes: Node<CustomNodeData>[]) => void;
  setEdges: (edges: Edge<CustomEdgeData>[]) => void;
  setAvailableNodes: (availableNodes: AvailableNodeType[]) => void; // Action to set available nodes
  onNodesChange: (changes: NodeChange[]) => void;
  onEdgesChange: (changes: EdgeChange[]) => void;
  onConnect: OnConnect;
};

// Initialize the store with Zustand and use the typed state
export const useNodeEditorStore = create<NodeEditorState>((set) => ({
  nodes: [
    {
      id: '1',
      type: 'input',
      data: { label: 'Start Procedure' },
      position: { x: 250, y: 0 },
    },
  ],
  edges: [],
  availableNodes: [], // Initialize available nodes as an empty array
  setNodes: (nodes) => set({ nodes }),
  setEdges: (edges) => set({ edges }),
  setAvailableNodes: (availableNodes) => set({ availableNodes }), // Set available nodes
  onNodesChange: (changes) =>
    set((state) => ({
      nodes: applyNodeChanges(changes, state.nodes), // Apply NodeChange[] to update nodes
    })),
  onEdgesChange: (changes) =>
    set((state) => ({
      edges: applyEdgeChanges(changes, state.edges), // Apply EdgeChange[] to update edges
    })),
  onConnect: (params) =>
    set((state) => ({
      edges: addEdge(params, state.edges),
    })),
}));
