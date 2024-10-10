import React from 'react';
import DynamicHandle from "../DynamicHandle";
import {ParameterItem} from "./ParameterItem";
import {Parameter} from "../../../../flatbuffers/wsock/parameter";

export function ParameterList({id, data}: { id: string; data: Record<string, any> }) {
    const parametersLength = data.procedure.parametersLength();

    if (!parametersLength) return null;

    return (
        <>
            {Array.from({length: parametersLength}).map((_, index) => {
                const param: Parameter = data.procedure.parameters(index);
                if (!param) return null;

                const paramName = param.name();
                if (!paramName) return null;

                if (paramName === 'output_file') return null;
                if (!data.paramids)
                    data.paramids = [];

                const procedure = data.procedure;
                const paramIds = data.paramids;
                const paramDefaultValue = param.defaultValue();
                let param_length = param.optionsLength();
                let paramOptions = null;

                if (param_length > 0) {
                    paramOptions = [];
                    for (let i = 0; i < param_length; i++) {
                        paramOptions[i] = param.options(i)?.unpack();
                    }
                }
                const procedureName = data.procedure.name();
                const paramKey = `${procedureName}-${index}`;
                const paramId = `param-${paramName}-${paramKey}`;

                if (!paramIds.includes(paramId)) {
                    paramIds.push(paramId);
                }

                if (paramName === procedure.inputFileVar()) {
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
