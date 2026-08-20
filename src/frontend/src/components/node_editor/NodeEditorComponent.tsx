import React from 'react';
import {Rnd} from 'react-rnd';
import {request_list_of_nodes} from "@/utils/node_editor/handlers/request_list_of_nodes";
import {useNodeEditorStore} from '@/state/useNodeEditorStore';
import NodeEditorBody from './NodeEditorBody';
import {start_new_node_editor} from "@/utils/node_editor/handlers/start_new_node_editor";
import ReloadIcon from "../icons/ReloadIcon";
import PopOutIcon from "../icons/PopOutIcon";


const NodeEditorComponent: React.FC = () => {
    // Only the frame decision is made here now; the editor's own state lives in
    // NodeEditorBody, which both this window and the shell's dock panel render.
    const {use_node_editor_only} = useNodeEditorStore();

    // Shared with the shell's dock panel so the two cannot drift.
    const editorContent = <NodeEditorBody />;

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
            <div className="node-editor-header node-editor-drag-handle bg-surface-0 text-white px-4 py-2 cursor-move">
                <div className={"flex flex-row"}>
                    <div className={"flex p-1"}>Node Editor</div>
                    <button
                        className={"flex relative bg-accent hover:bg-accent-subtle text-white p-1 ml-2 rounded-sm"}
                        onClick={() => request_list_of_nodes()}
                    >
                        <ReloadIcon />
                    </button>
                    <button
                        className={"flex relative bg-accent hover:bg-accent-subtle text-white p-1 ml-2 rounded-sm"}
                        onClick={() => start_new_node_editor()}
                    >
                        <PopOutIcon />
                    </button>
                    {/*<div className={"flex relative bg-accent hover:bg-accent-subtle text-white p-1 ml-2 rounded-sm"}>*/}
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
