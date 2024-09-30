import React, {memo} from 'react';
import {Handle, NodeProps, Position, Connection} from '@xyflow/react';
import {run_sequence} from "../../utils/node_editor/run_sequence";

function ProcedureNode(props:{id: string, data:Record<string, string>}) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };
    // console.log("ProcedureNode data:", data);
    return (
        <div className="bg-gray-200 text-gray-800 rounded-md w-56 h-30">
            {/* Header Row */}
            <div className="flex flex-col justify-center items-center mb-4">
                <div className="font-bold text-center text-xs">{props.data.label}</div>
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
