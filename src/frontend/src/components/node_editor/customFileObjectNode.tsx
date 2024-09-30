import React, {memo} from 'react';
import {Handle, NodeProps, Position, Connection} from '@xyflow/react';
import {run_sequence} from "../../utils/node_editor/run_sequence";

function CustomFileObjectNode(props: { id: string, data: Record<string, string> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };
    // console.log("ProcedureNode data:", data);
    return (
        <div className="bg-gray-200 text-gray-800 rounded-md min-w-24">
            {/* Header Row */}
            <div className="flex justify-center items-center mb-4">
                <div className="font-bold text-center text-xs">"{props.data.label}"</div>
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
