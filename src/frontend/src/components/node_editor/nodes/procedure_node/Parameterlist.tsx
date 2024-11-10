import React from 'react';
import DynamicHandle from "../DynamicHandle";
import {ParameterItem} from "./ParameterItem";
import {ParameterT} from "../../../../flatbuffers/wsock/parameter";
import {ProcedureT} from "../../../../flatbuffers/wsock/procedure";

export function ParameterList({id, data}: { id: string; data: Record<string, any> }) {
    const procedure: ProcedureT = data.procedure;
    const parametersLength = procedure.parameters.length;
    const fileInputsMap = data.fileInputsMap;
    const fileOutputsMap = data.fileOutputsMap;
    // sort elements in procedure.parameters by having file objects first
    const parameters: ParameterT[] = [];
    for (let i = 0; i < parametersLength; i++) {
        const param = procedure.parameters[i];
        const pname = param.name
        if (!pname) continue
        if (fileInputsMap[pname.toString()]) {
            parameters.unshift(param);
        } else {
            parameters.push(param);
        }
    }



    if (!parametersLength && procedure.fileInputs.length === 0) return null;

    return (
        <>
            {Array.from({length: parametersLength}).map((_, index) => {
                const param: ParameterT = parameters[index];
                if (!param) return null;

                const paramName = param.name?.toString();
                if (!paramName) return null;
                // if the parameter is an output file, don't render it here
                if (fileOutputsMap[paramName]) return null;
                if (!data.paramids)
                    data.paramids = [];

                const paramIds = data.paramids;
                const paramDefaultValue = param.defaultValue;
                let param_length = param.options.length;
                let paramOptions = null;

                if (param_length > 0) {
                    paramOptions = [];
                    for (let i = 0; i < param_length; i++) {
                        paramOptions[i] = param.options[i];
                    }
                }
                const procedureName = procedure.name;
                if (!procedureName) {
                    console.error("Procedure name is not defined");
                    return null;
                }
                const paramKey = `${procedureName}-${index}`;
                const paramId = `param-${paramName}-${paramKey}`;

                if (!paramIds.includes(paramId)) {
                    paramIds.push(paramId);
                }

                if (fileInputsMap[paramName]) {
                    return (
                        <DynamicHandle
                            key={paramKey}
                            id={paramId}
                            type="source"
                            label={paramName || `Param ${index + 1}`}
                        />
                    );
                } else {
                    return (
                        <ParameterItem
                            key={paramKey}
                            param={param}
                            paramId={paramId}
                            paramName={paramName}
                            paramDefaultValue={paramDefaultValue}
                            paramOptions={paramOptions}
                        />
                    );
                }
            })}
        </>
    );
}
