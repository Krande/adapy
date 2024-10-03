import React from 'react';
import {Procedure} from "../../../../flatbuffers/wsock/procedure";
import DynamicHandle from "../DynamicHandle";
import {ParameterItem} from "./ParameterItem";

export function ParameterList({ procedure, paramIds }: { procedure: Procedure; paramIds: string[] }) {
    const parametersLength = procedure.parametersLength();

    if (!parametersLength) return null;

    return (
        <>
            {Array.from({ length: parametersLength }).map((_, index) => {
                const param = procedure.parameters(index);
                if (!param) return null;

                const paramName = param.name();
                if (!paramName) return null;

                if (paramName === 'output_file') return null;

                const paramDefaultValue = param.defaultValue();
                const procedureName = procedure.name();
                const paramKey = `${procedureName}-${index}`;
                const paramId = `param-${paramKey}`;
                paramIds.push(paramId);

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
