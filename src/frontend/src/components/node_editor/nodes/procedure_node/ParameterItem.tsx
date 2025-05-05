import React from 'react';
import {ParameterT, ParameterType, ValueT} from "../../../../flatbuffers/base";


export function ParameterItem({
                                  param,
                                  paramId,
                                  paramName,
                                  paramDefaultValue,
                                  paramOptions,
                              }: {
    param: ParameterT;
    paramId: string;
    paramName: string;
    paramDefaultValue: any;
    paramOptions: any;
}) {
    switch (param.type) {
        case ParameterType.STRING:
            const defaultStringValue = paramDefaultValue?.stringValue?.toString() || '';
            return (
                <div className="flex flex-row items-center ml-1" id={paramId}>
                    {paramOptions && paramOptions.length > 0 ? (
                        <select
                            defaultValue={defaultStringValue}
                            className="nodrag m-auto ml-0 mr-0 text-gray-800 text-xs w-24 border border-gray-400 rounded"
                        >
                            {paramOptions.map((option: ValueT, index: number) => (
                                <option key={index} value={option.stringValue as string}>
                                    {option.stringValue}
                                </option>
                            ))}
                        </select>
                    ) : (
                        <input
                            type="text"
                            defaultValue={defaultStringValue}
                            className="nodrag m-auto ml-0 mr-0 text-gray-200 text-xs w-20 border border-gray-400 rounded bg-gray-800 p-0.5"
                        />
                    )}
                    <span className="pl-2 text-xs text-gray-300">{paramName}</span>
                </div>
            );
        case ParameterType.FLOAT:
            const defaultFloatValue = paramDefaultValue?.floatValue;
            return (
                <div className="flex flex-row items-center w-26 ml-1" id={paramId}>
                    {paramOptions && paramOptions.length > 0 ? (
                        <select
                            defaultValue={defaultFloatValue}

                            className="nodrag m-auto ml-0 mr-0 text-xs text-gray-200 bg-gray-800 p-0.5 w-24 border border-gray-400 rounded"
                        >
                            {paramOptions.map((option: ValueT, index: number) => (
                                <option key={index} value={option.floatValue as number}>
                                    {option.floatValue}
                                </option>
                            ))}
                        </select>
                    ) : (
                        <input
                            type="number"
                            defaultValue={defaultFloatValue != null ? defaultFloatValue.toFixed(4) : ''}
                            className="flex flex-1 nodrag m-auto ml-0 mr-0 text-xs text-gray-200 bg-gray-800 p-0.5 w-20 border border-gray-400 rounded"
                        />
                    )}
                    <span className="flex-1 pl-2 text-xs text-gray-300">{paramName}</span>

                </div>
            );
        case ParameterType.ARRAY:
            const arrayValueType = paramDefaultValue?.arrayValueType;
            if (param.defaultValue?.arrayAnyLength === false) {
                const values = [];
                for (let i = 0; i < paramDefaultValue?.arrayLength; i++) {
                    const value = paramDefaultValue.arrayValue[i];
                    if (value) {
                        values.push(
                            arrayValueType === ParameterType.FLOAT ? value.floatValue : value.stringValue
                        );
                    }
                }
                return (
                    <div className="flex flex-row items-center w-24 ml-1" id={paramId}>
                        {values.slice(0, 3).map((val, idx) => (
                            <input
                                key={idx}
                                type="number"
                                defaultValue={typeof val === 'number' ? val.toFixed(3) : ''}
                                className="nodrag flex m-auto text-gray-800 border text-xs border-gray-400 rounded max-w-8"
                            />
                        ))}
                        <span className="flex flex-1 pl-2 text-xs text-gray-300">{paramName}</span>
                    </div>
                );
            } else {
                let placeholder = '';
                if (arrayValueType === ParameterType.FLOAT)
                    placeholder = '1.0;2.0;3.0';
                else if (arrayValueType === ParameterType.INTEGER)
                    placeholder = '1;2;3';
                else {
                    console.error(`Unknown array value type: ${arrayValueType}`);
                }

                return (
                    <div className="flex flex-row items-center ml-1" id={paramId}>
                        <input
                            type="text"
                            pattern="[0-9]+(\.[0-9]+)?(;\s*[0-9]+(\.[0-9]+)?)*"
                            defaultValue={paramDefaultValue?.stringValue()}
                            placeholder={placeholder}
                            className="nodrag m-auto ml-0 mr-0 text-gray-800 text-xs w-24 border border-gray-400 rounded"
                        />
                        <span className="pl-2 text-xs text-gray-300">{paramName}</span>
                    </div>
                );
            }

        default:
            console.error(`Unknown parameter type: ${param.type}`);
            return null;
    }
}
