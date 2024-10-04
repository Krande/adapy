import React, {memo} from 'react';
import {Handle, Position} from '@xyflow/react';
import {view_file_object_from_server} from "../../../../utils/scene/view_file_object_from_server";
import {FileObject} from "../../../../flatbuffers/wsock/file-object";


const doc_icon = <svg width="12px" height="24px" viewBox="0 0 24 24" strokeWidth="1.5" fill="none"
                      xmlns="http://www.w3.org/2000/svg" color="#000000">
    <path
        d="M4 21.4V2.6C4 2.26863 4.26863 2 4.6 2H16.2515C16.4106 2 16.5632 2.06321 16.6757 2.17574L19.8243 5.32426C19.9368 5.43679 20 5.5894 20 5.74853V21.4C20 21.7314 19.7314 22 19.4 22H4.6C4.26863 22 4 21.7314 4 21.4Z"
        stroke="#ffffff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"></path>
    <path d="M16 2V5.4C16 5.73137 16.2686 6 16.6 6H20" stroke="#ffffff" strokeWidth="1.5" strokeLinecap="round"
          strokeLinejoin="round"></path>
</svg>
const view_set_icon = <svg width="12px" height="12px" viewBox="0 0 24 24" fill="none"
                           xmlns="http://www.w3.org/2000/svg">
    <path
        d="M15.0007 12C15.0007 13.6569 13.6576 15 12.0007 15C10.3439 15 9.00073 13.6569 9.00073 12C9.00073 10.3431 10.3439 9 12.0007 9C13.6576 9 15.0007 10.3431 15.0007 12Z"
        stroke="#ffffff" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round"/>
    <path
        d="M12.0012 5C7.52354 5 3.73326 7.94288 2.45898 12C3.73324 16.0571 7.52354 19 12.0012 19C16.4788 19 20.2691 16.0571 21.5434 12C20.2691 7.94291 16.4788 5 12.0012 5Z"
        stroke="#ffffff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
</svg>
const view_icon = <svg width="12px" height="12px" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path
        d="M2.99902 3L20.999 21M9.8433 9.91364C9.32066 10.4536 8.99902 11.1892 8.99902 12C8.99902 13.6569 10.3422 15 11.999 15C12.8215 15 13.5667 14.669 14.1086 14.133M6.49902 6.64715C4.59972 7.90034 3.15305 9.78394 2.45703 12C3.73128 16.0571 7.52159 19 11.9992 19C13.9881 19 15.8414 18.4194 17.3988 17.4184M10.999 5.04939C11.328 5.01673 11.6617 5 11.9992 5C16.4769 5 20.2672 7.94291 21.5414 12C21.2607 12.894 20.8577 13.7338 20.3522 14.5"
        stroke="#ffffff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
</svg>

function CustomFileObjectNode(props: { id: string, data: Record<string, string | FileObject> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };
    return (
        <div className="bg-bl-background text-gray-200 rounded-md min-w-24">
            {/* Header Row */}
            <div className="bg-gray-800 flex flex-row justify-center items-center px-1 rounded-t">
                <div className={"flex-1"}>{doc_icon} </div>
                <div className={"flex-1 text-center text-xs"}>{props.data.label.toString()}</div>
                <div className={"flex-1 text-xs"}> [{props.data.filetype.toString()}]</div>
            </div>

            <div className={"flex justify-center items-center text-xs p-1"}>
                <button
                    className="bg-blue-500 hover:bg-blue-700 text-white p-1 rounded"
                    onClick={() => {
                        view_file_object_from_server(props.data.fileobject as FileObject);
                    }}
                >
                    {view_set_icon}
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
