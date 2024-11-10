import React from 'react';
import {Rnd} from 'react-rnd';
import {Background, Controls, MiniMap, ReactFlow} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {request_list_of_nodes} from "../../utils/node_editor/request_list_of_nodes";
import {useNodeEditorStore} from '../../state/useNodeEditorStore'; // Import the Zustand store
import ProcedureNode from './nodes/procedure_node/ProcedureNode';
import CustomFileObjectNode from './nodes/file_node/customFileObjectNode';
import {onDelete} from "../../utils/node_editor/on_delete";
import {start_new_node_editor} from "../../utils/node_editor/start_new_node_editor";
import {handleFileUpload} from "../../utils/node_editor/handleFileUpload";

const nodeTypes = {
    procedure: ProcedureNode,
    file_object: CustomFileObjectNode,
};
const update_icon = <svg width="24px" height="24px" viewBox="0 0 15 15" fill="none"
                         xmlns="http://www.w3.org/2000/svg">
    <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M1.90321 7.29677C1.90321 10.341 4.11041 12.4147 6.58893 12.8439C6.87255 12.893 7.06266 13.1627 7.01355 13.4464C6.96444 13.73 6.69471 13.9201 6.41109 13.871C3.49942 13.3668 0.86084 10.9127 0.86084 7.29677C0.860839 5.76009 1.55996 4.55245 2.37639 3.63377C2.96124 2.97568 3.63034 2.44135 4.16846 2.03202L2.53205 2.03202C2.25591 2.03202 2.03205 1.80816 2.03205 1.53202C2.03205 1.25588 2.25591 1.03202 2.53205 1.03202L5.53205 1.03202C5.80819 1.03202 6.03205 1.25588 6.03205 1.53202L6.03205 4.53202C6.03205 4.80816 5.80819 5.03202 5.53205 5.03202C5.25591 5.03202 5.03205 4.80816 5.03205 4.53202L5.03205 2.68645L5.03054 2.68759L5.03045 2.68766L5.03044 2.68767L5.03043 2.68767C4.45896 3.11868 3.76059 3.64538 3.15554 4.3262C2.44102 5.13021 1.90321 6.10154 1.90321 7.29677ZM13.0109 7.70321C13.0109 4.69115 10.8505 2.6296 8.40384 2.17029C8.12093 2.11718 7.93465 1.84479 7.98776 1.56188C8.04087 1.27898 8.31326 1.0927 8.59616 1.14581C11.4704 1.68541 14.0532 4.12605 14.0532 7.70321C14.0532 9.23988 13.3541 10.4475 12.5377 11.3662C11.9528 12.0243 11.2837 12.5586 10.7456 12.968L12.3821 12.968C12.6582 12.968 12.8821 13.1918 12.8821 13.468C12.8821 13.7441 12.6582 13.968 12.3821 13.968L9.38205 13.968C9.10591 13.968 8.88205 13.7441 8.88205 13.468L8.88205 10.468C8.88205 10.1918 9.10591 9.96796 9.38205 9.96796C9.65819 9.96796 9.88205 10.1918 9.88205 10.468L9.88205 12.3135L9.88362 12.3123C10.4551 11.8813 11.1535 11.3546 11.7585 10.6738C12.4731 9.86976 13.0109 8.89844 13.0109 7.70321Z"
        fill="#ffffff"
    />
</svg>
const popout_icon = <svg fill="#ffffff" width="24px" height="24px" viewBox="0 0 36 36" version="1.1"
                         preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">
    <title>pop-out-line</title>
    <path className="clr-i-outline clr-i-outline-path-1"
          d="M27,33H5a2,2,0,0,1-2-2V9A2,2,0,0,1,5,7H15V9H5V31H27V21h2V31A2,2,0,0,1,27,33Z"></path>
    <path className="clr-i-outline clr-i-outline-path-2"
          d="M18,3a1,1,0,0,0,0,2H29.59L15.74,18.85a1,1,0,1,0,1.41,1.41L31,6.41V18a1,1,0,0,0,2,0V3Z"></path>
    <rect x="0" y="0" width="36" height="36" fillOpacity="0"/>
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
                        {update_icon}
                    </button>
                    <button
                        className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded"}
                        onClick={() => start_new_node_editor()}
                    >
                        {popout_icon}
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
