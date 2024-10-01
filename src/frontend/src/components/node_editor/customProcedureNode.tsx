import React, {memo} from 'react';
import {Handle, NodeProps, Position, Connection} from '@xyflow/react';
import {run_sequence} from "../../utils/node_editor/run_sequence";

const procedure_icon = <svg fill="#000000" width="12px" height="12px" viewBox="0 0 24 24"
                            xmlns="http://www.w3.org/2000/svg" enableBackground="new 0 0 24 24">
    <path
        d="M8.5,6H6.7C8.2,4.7,10,4,12,4c0.3,0,0.6,0,0.9,0.1c0,0,0,0,0,0c0.5,0.1,1-0.3,1.1-0.9c0.1-0.5-0.3-1-0.9-1.1C12.7,2,12.4,2,12,2C9.6,2,7.3,2.9,5.5,4.4V3c0-0.6-0.4-1-1-1s-1,0.4-1,1v4c0,0.6,0.4,1,1,1h4c0.6,0,1-0.4,1-1S9.1,6,8.5,6z M7,14.5c-0.6,0-1,0.4-1,1v1.8C4.7,15.8,4,14,4,12c0-0.3,0-0.6,0.1-0.9c0,0,0,0,0,0c0.1-0.5-0.3-1-0.9-1.1c-0.5-0.1-1,0.3-1.1,0.9C2,11.3,2,11.6,2,12c0,2.4,0.9,4.7,2.4,6.5H3c-0.6,0-1,0.4-1,1s0.4,1,1,1h4c0.3,0,0.6-0.2,0.8-0.4c0,0,0,0,0,0c0,0,0,0,0,0c0-0.1,0.1-0.2,0.1-0.3c0-0.1,0-0.1,0-0.2c0,0,0-0.1,0-0.1v-4C8,14.9,7.6,14.5,7,14.5z M21,5.5c0.6,0,1-0.4,1-1s-0.4-1-1-1h-4c-0.1,0-0.1,0-0.2,0c0,0,0,0,0,0c-0.1,0-0.2,0.1-0.3,0.1c0,0,0,0,0,0c-0.1,0.1-0.2,0.1-0.2,0.2c0,0,0,0,0,0c0,0,0,0,0,0c0,0.1-0.1,0.2-0.1,0.2c0,0.1,0,0.1,0,0.2c0,0,0,0.1,0,0.1v4c0,0.6,0.4,1,1,1s1-0.4,1-1V6.7c1.3,1.4,2,3.3,2,5.3c0,0.3,0,0.6-0.1,0.9c-0.1,0.5,0.3,1,0.9,1.1c0,0,0.1,0,0.1,0c0.5,0,0.9-0.4,1-0.9c0-0.4,0.1-0.7,0.1-1.1c0-2.4-0.9-4.7-2.4-6.5H21z M20.3,16.5c-0.1-0.1-0.2-0.2-0.3-0.3c0,0,0,0,0,0c0,0,0,0,0,0c-0.1-0.1-0.2-0.1-0.3-0.1c0,0-0.1,0-0.1,0c0,0-0.1,0-0.1,0h-4c-0.6,0-1,0.4-1,1s0.4,1,1,1h1.8c-1.4,1.3-3.3,2-5.3,2c-0.3,0-0.6,0-0.9-0.1c0,0,0,0,0,0c-0.5-0.1-1,0.3-1.1,0.9s0.3,1,0.9,1.1c0.4,0,0.7,0.1,1.1,0.1c2.4,0,4.7-0.9,6.5-2.4V21c0,0.6,0.4,1,1,1s1-0.4,1-1v-4C20.5,16.8,20.4,16.6,20.3,16.5C20.3,16.5,20.3,16.5,20.3,16.5z"/>
</svg>


function ProcedureNode(props: { id: string, data: Record<string, string> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };
    // console.log("ProcedureNode data:", data);
    return (
        <div className="bg-gray-200 text-gray-800 rounded-md min-w-40 h-30">
            {/* Header Row */}
            <div className="flex flex-col justify-center items-center mb-4">
                <div className={"flex flex-row items-center"}>
                    <div className={"flex px-1"}>{procedure_icon}</div>
                    <div className="flex text-center text-xs">{props.data.label}</div>
                </div>

                <button
                    className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white text-xs px-4 rounded"}
                    onClick={() => run_sequence(props.id)}
                >Run
                </button>
            </div>

            {/* Handle Rows */}
            <div className="flex flex-col">
                {/* File Object Source Handle Row */}
                <div className={"flex flex-row items-center"}>
                     <span className={"m-auto ml-0 mr-0 relative"}>
                    <Handle
                        type="target"
                        id="file_object"
                        position={Position.Left}
                        style={{marginTop: "auto", position: "absolute", background: '#555'}}
                        isConnectable={true}
                        isValidConnection={isValidConnection}
                        onConnect={(params: Connection) => console.log("onConnect", params)}
                    />
                        </span>
                    <span className="pl-1 text-center text-xs text-gray-600">File Object</span>
                </div>
                <div className={"flex flex-row items-center"}>
                    <span className={"m-auto ml-0 mr-0 relative"}>
                    <Handle
                        type="target"
                        id="param1"
                        position={Position.Left}
                        style={{position: "absolute", background: '#555'}}
                        isConnectable={true}
                    />
                            </span>
                    <div className="pl-1 text-xs text-gray-600">Param 1</div>
                </div>
                <div className={"flex flex-row items-center"}>
                    <span className={"m-auto ml-0 mr-0 relative"}>
                    <Handle
                        type="target"
                        id="param2"
                        position={Position.Left}
                        style={{position: "absolute", background: '#555'}}
                        isConnectable={true}
                    />
                            </span>
                    <div className="pl-1 text-xs text-gray-600">Param 1</div>
                </div>
            </div>
        </div>
    );
}

export default memo(ProcedureNode);
