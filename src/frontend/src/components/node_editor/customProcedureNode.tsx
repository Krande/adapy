import React, {memo} from 'react';
import {run_procedure} from "../../utils/node_editor/run_procedure";
import {Procedure} from "../../flatbuffers/wsock/procedure";
import DynamicHandle from "./DynamicHandle";
import {Parameter} from "../../flatbuffers/wsock/parameter";
import {ParameterType} from "../../flatbuffers/wsock";

const procedure_icon = <svg fill="#ffffff" width="12px" height="12px" viewBox="0 0 24 24"
                            xmlns="http://www.w3.org/2000/svg" enableBackground="new 0 0 24 24">
    <path
        d="M8.5,6H6.7C8.2,4.7,10,4,12,4c0.3,0,0.6,0,0.9,0.1c0,0,0,0,0,0c0.5,0.1,1-0.3,1.1-0.9c0.1-0.5-0.3-1-0.9-1.1C12.7,2,12.4,2,12,2C9.6,2,7.3,2.9,5.5,4.4V3c0-0.6-0.4-1-1-1s-1,0.4-1,1v4c0,0.6,0.4,1,1,1h4c0.6,0,1-0.4,1-1S9.1,6,8.5,6z M7,14.5c-0.6,0-1,0.4-1,1v1.8C4.7,15.8,4,14,4,12c0-0.3,0-0.6,0.1-0.9c0,0,0,0,0,0c0.1-0.5-0.3-1-0.9-1.1c-0.5-0.1-1,0.3-1.1,0.9C2,11.3,2,11.6,2,12c0,2.4,0.9,4.7,2.4,6.5H3c-0.6,0-1,0.4-1,1s0.4,1,1,1h4c0.3,0,0.6-0.2,0.8-0.4c0,0,0,0,0,0c0,0,0,0,0,0c0-0.1,0.1-0.2,0.1-0.3c0-0.1,0-0.1,0-0.2c0,0,0-0.1,0-0.1v-4C8,14.9,7.6,14.5,7,14.5z M21,5.5c0.6,0,1-0.4,1-1s-0.4-1-1-1h-4c-0.1,0-0.1,0-0.2,0c0,0,0,0,0,0c-0.1,0-0.2,0.1-0.3,0.1c0,0,0,0,0,0c-0.1,0.1-0.2,0.1-0.2,0.2c0,0,0,0,0,0c0,0,0,0,0,0c0,0.1-0.1,0.2-0.1,0.2c0,0.1,0,0.1,0,0.2c0,0,0,0.1,0,0.1v4c0,0.6,0.4,1,1,1s1-0.4,1-1V6.7c1.3,1.4,2,3.3,2,5.3c0,0.3,0,0.6-0.1,0.9c-0.1,0.5,0.3,1,0.9,1.1c0,0,0.1,0,0.1,0c0.5,0,0.9-0.4,1-0.9c0-0.4,0.1-0.7,0.1-1.1c0-2.4-0.9-4.7-2.4-6.5H21z M20.3,16.5c-0.1-0.1-0.2-0.2-0.3-0.3c0,0,0,0,0,0c0,0,0,0,0,0c-0.1-0.1-0.2-0.1-0.3-0.1c0,0-0.1,0-0.1,0c0,0-0.1,0-0.1,0h-4c-0.6,0-1,0.4-1,1s0.4,1,1,1h1.8c-1.4,1.3-3.3,2-5.3,2c-0.3,0-0.6,0-0.9-0.1c0,0,0,0,0,0c-0.5-0.1-1,0.3-1.1,0.9s0.3,1,0.9,1.1c0.4,0,0.7,0.1,1.1,0.1c2.4,0,4.7-0.9,6.5-2.4V21c0,0.6,0.4,1,1,1s1-0.4,1-1v-4C20.5,16.8,20.4,16.6,20.3,16.5C20.3,16.5,20.3,16.5,20.3,16.5z"/>
</svg>


function ProcedureNode(props: { id: string, data: Record<string, string | Procedure | string[]> }) {
    // Custom connection validation function for `file_object` handle
    const isValidConnection = () => {
        console.log("isValidConnection");
        return true;
    };

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
                    {/* Other dynamic inputs */}
                    {(props.data.procedure as Procedure).parameters && (props.data.procedure as Procedure).parametersLength && (
                        Array.from({length: (props.data.procedure as Procedure).parametersLength()}).map((_, index) => {
                            const param = (props.data.procedure as Procedure).parameters(index); // Get parameter by index
                            if (!param) return null;
                            const paramName = (param as Parameter)?.name(); // Replace with the correct getter for parameter name
                            const paramDefaultValue = param?.defaultValue(); // Replace with the correct getter for parameter value
                            const procedure = props.data.procedure as Procedure;
                            const procedure_name = procedure.name();
                            const input_file_var = procedure.inputFileVar();
                            const paramKey = `${procedure_name}-${index}`;
                            const paramId = `param-${paramKey}`;

                            if (!props.data.paramids) {
                                props.data.paramids = [];
                            }
                            (props.data.paramids as string[]).push(paramId);

                            if (paramName === input_file_var) {
                                return (
                                    <DynamicHandle
                                        key={paramKey}
                                        id={paramId}
                                        type="source"
                                        label={paramName || `Param ${index + 1}`}
                                    />
                                );
                            } else if (param.type() === ParameterType.STRING || param.type() === ParameterType.STRING) {
                                let default_value = paramDefaultValue ? paramDefaultValue.stringValue()?.toString() : "";
                                return (
                                    <div className="flex flex-row items-center" key={paramKey} id={paramId}>
                                        <input
                                            type="text" // Use text input for strings
                                            defaultValue={default_value}
                                            className="nodrag m-auto ml-0 mr-0 relative text-gray-800 text-xs w-24 border border-gray-400 rounded"
                                        />
                                        <span className="pl-2 text-xs text-gray-300">{paramName}</span>
                                    </div>
                                );
                            } else if (param.type() === ParameterType.FLOAT) {
                                // Return regular float input box
                                let default_value = paramDefaultValue ? paramDefaultValue.floatValue() : null;
                                return (
                                    <div className="flex flex-row items-center w-26" key={paramKey} id={paramId}>
                                        <input
                                            type="number" // Use number input for floats
                                            defaultValue={default_value != null ? default_value.toString() : ""}
                                            className="flex flex-1 nodrag m-auto ml-0 mr-0 relative text-xs text-gray-800 w-24 border border-gray-400 rounded"
                                        />
                                        <span className="flex-1 pl-2 text-xs text-gray-300">{paramName}</span>
                                    </div>
                                );
                            } else if (param.type() === ParameterType.ARRAY) {
                                let default_value = null;
                                let values = [];
                                if (paramDefaultValue) {
                                    let array_value_type = paramDefaultValue.arrayValueType()
                                    for (let i = 0; i < paramDefaultValue?.arrayLength(); i++) {
                                        let value = paramDefaultValue.arrayValue(i)
                                        if (value) {
                                            if (array_value_type === ParameterType.FLOAT) {
                                                values.push(value.floatValue());
                                            } else if (array_value_type === ParameterType.STRING) {
                                                values.push(value.stringValue());
                                            } else {
                                                console.error(`Unknown array value type: ${array_value_type}`);
                                            }
                                        }

                                    }
                                }
                                return (
                                    <div className="flex flex-row items-center w-24" key={paramKey} id={paramId}>
                                        <input
                                            type="number"
                                            defaultValue={values.length > 0 ? values[0]?.toString() : ""}
                                            className="nodrag flex m-auto text-gray-800 border text-xs border-gray-400 rounded max-w-8"
                                        />
                                        <input
                                            type="number"
                                            defaultValue={values.length > 0 ? values[1]?.toString() : ""}
                                            className="nodrag flex-1 m-auto text-gray-800 border text-xs border-gray-400 rounded max-w-8"
                                        />
                                        <input
                                            type="number"
                                            defaultValue={values.length > 0 ? values[2]?.toString() : ""}
                                            className="nodrag flex flex-1 m-auto relative text-gray-800 text-xs border border-gray-400 rounded max-w-8"
                                        />
                                        <span className="flex flex-1 pl-2 text-xs text-gray-300">{paramName}</span>
                                    </div>
                                );
                            } else {
                                console.error(`Unknown parameter type: ${param.type()}`);
                            }
                        })
                    )}
                </div>

                {/* Handle Rows Right*/}
                <div className="flex flex-col">
                    <DynamicHandle
                        key={`procedure-${props.id}-output`}
                        id={`${props.id}-file_object_output`}
                        type={"target"}
                        label={"Output File"}
                        left_side={false}/>
                </div>
            </div>
        </div>
    );
}

export default memo(ProcedureNode);
