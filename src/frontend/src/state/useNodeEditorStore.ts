import {create} from 'zustand';
import { Node, Edge, OnConnect, addEdge, NodeChange, EdgeChange, applyNodeChanges, applyEdgeChanges } from '@xyflow/react';

// Define the type for the store's state
type NodeEditorState = {
    nodes: Node[];
    edges: Edge[];
    setNodes: (nodes: Node[]) => void;
    setEdges: (edges: Edge[]) => void;
    onNodesChange: (changes: NodeChange[]) => void; // Use NodeChange type for onNodesChange
    onEdgesChange: (changes: EdgeChange[]) => void; // Use EdgeChange type for onEdgesChange
    onConnect: OnConnect;
};

// Initialize the store with Zustand
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
    setNodes: (nodes: Node[]) => set({ nodes }),
    setEdges: (edges: Edge[]) => set({ edges }),
    onNodesChange: (changes) =>
        set((state) => ({
            nodes: applyNodeChanges(changes, state.nodes), // Apply NodeChange[]
        })),
    onEdgesChange: (changes) =>
        set((state) => ({
            edges: applyEdgeChanges(changes, state.edges), // Apply EdgeChange[]
        })),
    onConnect: (params) =>
        set((state) => ({
            edges: addEdge(params, state.edges),
        })),
}));