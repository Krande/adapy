import React, {memo} from 'react';
import {Connection, Handle, Position} from '@xyflow/react';
import {run_procedure} from "../../utils/node_editor/run_procedure";
import {Procedure, ProcedureT} from "../../flatbuffers/wsock/procedure";
import DynamicHandle from "./DynamicHandle";
import {Parameter} from "../../flatbuffers/wsock/parameter";
import {FileObject} from "../../flatbuffers/wsock/file-object";
import LeftSideHandles from "./LeftSideHandles";

const procedure_icon = <svg fill="#ffffff" width="12px" height="12px" viewBox="0 0 24 24"
                            xmlns="http://www.w3.org/2000/svg" enableBackground="new 0 0 24 24">
    <path
        d="M8.5,6H6.7C8.2,4.7,10,4,12,4c0.3,0,0.6,0,0.9,0.1c0,0,0,0,0,0c0.5,0.1,1-0.3,1.1-0.9c0.1-0.5-0.3-1-0.9-1.1C12.7,2,12.4,2,12,2C9.6,2,7.3,2.9,5.5,4.4V3c0-0.6-0.4-1-1-1s-1,0.4-1,1v4c0,0.6,0.4,1,1,1h4c0.6,0,1-0.4,1-1S9.1,6,8.5,6z M7,14.5c-0.6,0-1,0.4-1,1v1.8C4.7,15.8,4,14,4,12c0-0.3,0-0.6,0.1-0.9c0,0,0,0,0,0c0.1-0.5-0.3-1-0.9-1.1c-0.5-0.1-1,0.3-1.1,0.9C2,11.3,2,11.6,2,12c0,2.4,0.9,4.7,2.4,6.5H3c-0.6,0-1,0.4-1,1s0.4,1,1,1h4c0.3,0,0.6-0.2,0.8-0.4c0,0,0,0,0,0c0,0,0,0,0,0c0-0.1,0.1-0.2,0.1-0.3c0-0.1,0-0.1,0-0.2c0,0,0-0.1,0-0.1v-4C8,14.9,7.6,14.5,7,14.5z M21,5.5c0.6,0,1-0.4,1-1s-0.4-1-1-1h-4c-0.1,0-0.1,0-0.2,0c0,0,0,0,0,0c-0.1,0-0.2,0.1-0.3,0.1c0,0,0,0,0,0c-0.1,0.1-0.2,0.1-0.2,0.2c0,0,0,0,0,0c0,0,0,0,0,0c0,0.1-0.1,0.2-0.1,0.2c0,0.1,0,0.1,0,0.2c0,0,0,0.1,0,0.1v4c0,0.6,0.4,1,1,1s1-0.4,1-1V6.7c1.3,1.4,2,3.3,2,5.3c0,0.3,0,0.6-0.1,0.9c-0.1,0.5,0.3,1,0.9,1.1c0,0,0.1,0,0.1,0c0.5,0,0.9-0.4,1-0.9c0-0.4,0.1-0.7,0.1-1.1c0-2.4-0.9-4.7-2.4-6.5H21z M20.3,16.5c-0.1-0.1-0.2-0.2-0.3-0.3c0,0,0,0,0,0c0,0,0,0,0,0c-0.1-0.1-0.2-0.1-0.3-0.1c0,0-0.1,0-0.1,0c0,0-0.1,0-0.1,0h-4c-0.6,0-1,0.4-1,1s0.4,1,1,1h1.8c-1.4,1.3-3.3,2-5.3,2c-0.3,0-0.6,0-0.9-0.1c0,0,0,0,0,0c-0.5-0.1-1,0.3-1.1,0.9s0.3,1,0.9,1.1c0.4,0,0.7,0.1,1.1,0.1c2.4,0,4.7-0.9,6.5-2.4V21c0,0.6,0.4,1,1,1s1-0.4,1-1v-4C20.5,16.8,20.4,16.6,20.3,16.5C20.3,16.5,20.3,16.5,20.3,16.5z"/>
</svg>


function ProcedureNode(props: { id: string, data: Record<string, string | Procedure> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };
    // console.log("ProcedureNode data:", data);
    return (
        <div className="bg-bl-background text-gray-200 rounded-md min-w-40 h-30">
            {/* Header Row */}
            <div className="bg-gray-800 flex flex-col justify-center items-center mb-4">
                <div className={"flex flex-row items-center"}>
                    <div className={"flex px-1"}>{procedure_icon}</div>
                    <div className="flex text-center text-xs">{props.data.label as string}</div>
                </div>

                <button
                    className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white text-xs px-4 rounded"}
                    onClick={() => run_procedure(props)}
                >Run
                </button>
            </div>


            <div className="flex flex-row w-full">
                {/* Handle Rows Left*/}
                <div className="flex flex-1 flex-col">
                    {/* File Object Source Handle Row */}
                    {props.data.inputFileVar &&
                        <DynamicHandle id={"file_object"} type={"source"} label={"File Object"}/>}
                    {/* Other dynamic inputs */}
                    {(props.data.procedure as Procedure).parameters && (props.data.procedure as Procedure).parametersLength && (
                        Array.from({length: (props.data.procedure as Procedure).parametersLength()}).map((_, index) => {
                            const param = (props.data.procedure as Procedure).parameters(index); // Get parameter by index
                            const paramName = (param as Parameter)?.name(); // Replace with the correct getter for parameter name
                            const paramType = param?.type(); // Replace with the correct getter for parameter type
                            const paramValue = param?.value(); // Replace with the correct getter for parameter value
                            const procedure = props.data.procedure as Procedure;
                            const input_file_var = procedure.inputFileVar();

                            // Determine datatype based on paramType
                            if (paramName === input_file_var) {
                                return (
                                    <DynamicHandle
                                        key={index}
                                        id={`param${index}`}
                                        type="target"
                                        label={paramName || `Param ${index + 1}`}
                                    />
                                );
                            } else if (paramType === 'str') {
                                return (
                                    <div className="flex flex-row items-center">
                                        <input
                                            type="text" // Use text input for strings
                                            className="m-auto ml-0 mr-0 relative text-gray-800 w-20 border border-gray-400 rounded"
                                        />
                                        <span className="pl-2 text-xs text-gray-300">{paramName}</span>
                                    </div>
                                );
                            } else if (paramType === 'float') {
                                // Return regular float input box
                                return (
                                    <div className="flex flex-row items-center">
                                        <input
                                            type="number" // Use number input for floats
                                            className="m-auto ml-0 mr-0 relative text-gray-800 w-20 border border-gray-400 rounded"
                                        />
                                        <span className="pl-2 text-xs text-gray-300">{paramName}</span>
                                    </div>
                                );
                            } else {// if (paramType === 'tuple') {
                                return (
                                    <div className="flex flex-row items-center">
                                        <input
                                            type="number"
                                            className="m-auto ml-0 mr-2 relative text-gray-800 w-5 border border-gray-400 rounded"

                                        />
                                        <input
                                            type="number"
                                            className="m-auto ml-1 mr-0 relative text-gray-800 w-5 border border-gray-400 rounded"

                                        />
                                        <input
                                            type="number"
                                            className="m-auto ml-2 mr-0 relative text-gray-800 w-5 border border-gray-400 rounded"
                                        />
                                        <span className="pl-2 text-xs text-gray-300">{paramName}</span>
                                    </div>
                                );
                            }
                        })
                    )}
                </div>

                {/* Handle Rows Right*/}
                <div className="flex flex-col">
                    <DynamicHandle id={"file_object_output"} type={"target"} label={"Output File"}
                                   left_side={false}/>
                </div>
            </div>
        </div>
    );
}

export default memo(ProcedureNode);
