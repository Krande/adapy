import React from 'react';
import {Procedure} from "../../../../flatbuffers/wsock/procedure";
import DynamicHandle from "../DynamicHandle";
import {ParameterItem} from "./ParameterItem";

export function ParameterList({id, data}: { id: string; data: Record<string, any> }) {
    const parametersLength = data.procedure.parametersLength();

    if (!parametersLength) return null;

    return (
        <>
            {Array.from({length: parametersLength}).map((_, index) => {
                const param = data.procedure.parameters(index);
                if (!param) return null;

                const paramName = param.name();
                if (!paramName) return null;

                if (paramName === 'output_file') return null;
                if (!data.paramids)
                    data.paramids = [];

                const procedure = data.procedure;
                const paramIds = data.paramids;
                const paramDefaultValue = param.defaultValue();
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
                        />
                    );
                }
            })}
        </>
    );
}
