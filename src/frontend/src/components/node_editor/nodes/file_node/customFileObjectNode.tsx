import React, {memo} from 'react';
import {Handle, Position} from '@xyflow/react';
import {view_file_object_from_server} from "../../../../utils/scene/comms/view_file_object_from_server";
import {FileObject} from "../../../../flatbuffers/wsock/file-object";
import DocumentIcon from "../../../icons/DocumentIcon";
import ViewIcon from "../../../icons/ViewIcon";


function CustomFileObjectNode(props: { id: string, data: Record<string, string | FileObject> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        // Todo: Implement custom connection validation logic for `file_object` handle
        console.log("isValidConnection");
        return true;
    };
    return (
        <div className="bg-bl-background text-gray-200 rounded-md min-w-24">
            {/* Header Row */}
            <div className="bg-gray-800 flex flex-row justify-center items-center px-1 rounded-t">
                <div className={"flex-1"}><DocumentIcon/></div>
                <div className={"flex-1 text-center text-xs"}>{props.data.label.toString()}</div>
                <div className={"flex-1 text-xs"}> [{props.data.filetype.toString()}]</div>
            </div>

            <div className={"flex justify-center items-center text-xs p-1"}>
                <button
                    className="nodrag bg-blue-500 hover:bg-blue-700 text-white p-1 rounded"
                    onClick={() => {
                        view_file_object_from_server(props.data.fileobject as FileObject);
                    }}
                >
                    <ViewIcon/>
                </button>
            </div>

            {/* Handle Rows */}
            <div className="flex flex-col">
                {/* File Object Source Handle Row */}
                <div className={"flex flex-row items-center"}>
                    <span className={"m-auto mr-0"}>
                    <Handle
                        type="target"
                        id={`${props.id}-file_object_out`}
                        position={Position.Right}
                        style={{position: "absolute", background: '#555'}}
                        isConnectable={true}
                    />
                    </span>
                </div>
                {
                    props.data.isProcedureOutput &&
                    <span className={"m-auto mr-0"}>
                    <Handle
                        type="source"
                        id={`${props.id}-procedure_in`}
                        position={Position.Left}
                        style={{position: "absolute", background: '#555'}}
                        isConnectable={true}
                    />
                    </span>
                }
            </div>
        </div>
    );
}

export default memo(CustomFileObjectNode);
