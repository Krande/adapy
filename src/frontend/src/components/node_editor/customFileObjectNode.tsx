import React, {memo} from 'react';
import {Handle, NodeProps, Position, Connection} from '@xyflow/react';
import {run_sequence} from "../../utils/node_editor/run_sequence";
import {get_file_object_from_server} from "../../utils/scene/get_file_object_from_server";


const doc_icon = <svg width="12px" height="24px" viewBox="0 0 24 24" strokeWidth="1.5" fill="none" xmlns="http://www.w3.org/2000/svg" color="#000000"><path d="M4 21.4V2.6C4 2.26863 4.26863 2 4.6 2H16.2515C16.4106 2 16.5632 2.06321 16.6757 2.17574L19.8243 5.32426C19.9368 5.43679 20 5.5894 20 5.74853V21.4C20 21.7314 19.7314 22 19.4 22H4.6C4.26863 22 4 21.7314 4 21.4Z" stroke="#000000" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"></path><path d="M16 2V5.4C16 5.73137 16.2686 6 16.6 6H20" stroke="#000000" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"></path></svg>

function CustomFileObjectNode(props: { id: string, data: Record<string, string> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };
    return (
        <div className="bg-gray-200 text-gray-800 rounded-md min-w-24">
            {/* Header Row */}
            <div className="flex flex-row justify-center items-center p-2">
                <div className={"flex-1"}>{doc_icon} </div>
                <div className={"flex-1 text-center text-xs"}>{props.data.label}</div>
                <div className={"flex-1 text-xs"}> [{props.data.filetype}]</div>
            </div>
            <div className={"flex justify-center items-center text-xs"}>
                <button
                    className="bg-blue-500 hover:bg-blue-700 text-white p-2 rounded"
                    onClick={() => {
                        get_file_object_from_server(props.data.fileobject);
                    }}
                >
                    View
                </button>
            </div>

            {/* Handle Rows */}
            <div className="flex flex-col">
                {/* File Object Source Handle Row */}
                <div className={"flex flex-row items-center"}>
                    <span className={"m-auto relative"}>
                    <Handle
                        type="source"
                        id="file_object_out"
                        position={Position.Bottom}
                        style={{position: "absolute", background: '#555'}}
                        isConnectable={true}
                    />
                    </span>
                </div>
            </div>
        </div>
    );
}

export default memo(CustomFileObjectNode);
