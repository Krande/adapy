import React, {memo} from 'react';
import {Handle, NodeProps, Position, Connection} from '@xyflow/react';
import {run_sequence} from "../../utils/node_editor/run_sequence";

const procedure_icon = <svg width="12px" height="12px" viewBox="0 0 24 24" fill="none"
                            xmlns="http://www.w3.org/2000/svg">
    <path d="M17 15H14.5H12" stroke="#1C274C" stroke-width="1.5" stroke-linecap="round"/>
    <path
        d="M7 10L7.2344 10.1953C8.51608 11.2634 9.15693 11.7974 9.15693 12.5C9.15693 13.2026 8.51608 13.7366 7.2344 14.8047L7 15"
        stroke="#1C274C" stroke-width="1.5" stroke-linecap="round"/>
    <path
        d="M22 12C22 16.714 22 19.0711 20.5355 20.5355C19.0711 22 16.714 22 12 22C7.28595 22 4.92893 22 3.46447 20.5355C2 19.0711 2 16.714 2 12C2 7.28595 2 4.92893 3.46447 3.46447C4.92893 2 7.28595 2 12 2C16.714 2 19.0711 2 20.5355 3.46447C21.5093 4.43821 21.8356 5.80655 21.9449 8"
        stroke="#1C274C" stroke-width="1.5" stroke-linecap="round"/>
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
                <div className={"flex flex-row"}>
                    <div>{procedure_icon}</div>
                    <div className="font-bold text-center text-xs">{props.data.label}</div>
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
