import React from 'react';
import {request_list_of_nodes} from "@/utils/node_editor/handlers/request_list_of_nodes";
import {useNodeEditorStore} from '@/state/useNodeEditorStore';
import {start_new_node_editor} from "@/utils/node_editor/handlers/start_new_node_editor";
import {FloatingWindow} from "@/components/ui/FloatingWindow";
import {TransformedHost} from "@/components/ui/OverlayPortal";
import ReloadIcon from "../icons/ReloadIcon";
import PopOutIcon from "../icons/PopOutIcon";
import NodeEditorBody from "./NodeEditorBody";
import {WindowDockActions, useWindowDock, windowDockAttrs} from "@/components/ui/PanelFrame";

// The node editor's two frames around one body (`NodeEditorBody`): the whole page
// in node-editor-only mode, else a floating window over the viewer, dragged by
// its header.
const NodeEditorComponent: React.FC = () => {
    const use_node_editor_only = useNodeEditorStore((s) => s.use_node_editor_only);
    const dock = useWindowDock();

    return use_node_editor_only ? (
        <div style={{width: '100%', height: '100%', background: 'white', border: '1px solid #ccc'}}>
            <NodeEditorBody/>
        </div>
    ) : (
        <FloatingWindow
            dragHandleClassName="node-editor-drag-handle" // Restrict dragging to the header
            zIndex={1000}
            initialRect={{x: 100, y: 100, width: 800, height: 600}}
            style={{background: 'white', border: '1px solid #ccc'}}
        >
            {/* Header Area */}
            <div className="node-editor-header node-editor-drag-handle bg-gray-800 text-white px-4 py-2 cursor-move" {...windowDockAttrs(dock)}>
                <div className={"flex flex-row"}>
                    <div className={"flex p-1"}>Node Editor</div>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded-sm"}
                        onClick={() => void request_list_of_nodes().catch((err) => console.error("Failed to reload nodes:", err))}
                    >
                        <ReloadIcon />
                    </button>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded-sm"}
                        onClick={() => start_new_node_editor()}
                    >
                        <PopOutIcon />
                    </button>
                    {/* A layout host's actions (move to the grid), when it offers some. */}
                    <WindowDockActions/>
                </div>
            </div>
            {/* Content Area. The window moves by a transform: fixed overlays of the content portal out (`OverlayPortal`). */}
            <div style={{width: '100%', height: 'calc(100% - 40px)'}}>
                <TransformedHost>
                    <NodeEditorBody/>
                </TransformedHost>
            </div>
        </FloatingWindow>
    );
};

export default NodeEditorComponent;
