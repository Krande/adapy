// LeftSideHandles.tsx
import React from 'react';
import { Position, Connection } from '@xyflow/react';
import DynamicHandle from './DynamicHandle';
import {FileObject} from "../../flatbuffers/wsock/file-object";
import {Procedure} from "../../flatbuffers/wsock/procedure";

interface Parameter {
    name: () => string;
    type: () => 'FileObject' | 'string' | 'float' | 'tuple';
}

interface LeftSideHandlesProps {
    inputFileVar?: string;
    parameters?: (index: number) => Parameter | undefined;
    parametersLength?: () => number;
    onConnect: (params: Connection) => void;
}

const LeftSideHandles: React.FC<LeftSideHandlesProps> = ({
    inputFileVar,
    parameters,
    parametersLength,
    onConnect,
}) => {
    return (
        <div className="flex flex-1 flex-col">
            {/* File Object Source Handle Row */}
            {inputFileVar && (
                <DynamicHandle
                    id="file_object"
                    type="target"
                    label="File Object"
                    style={{ marginTop: 'auto', position: 'absolute', background: '#555' }}
                    isConnectable={true}
                    onConnect={onConnect}
                />
            )}

            {/* Other dynamic inputs */}
            {parameters && parametersLength && (
                Array.from({ length: parametersLength() }).map((_, index) => {
                    const param = parameters(index);
                    if (!param) return null;
                    const paramName = param.name();
                    const paramType = param.type();

                    return (
                        <DynamicHandle
                            key={index}
                            id={`param${index}`}
                            type="target"
                            label={paramName || `Param ${index + 1}`}
                            datatype={paramType}
                            value=""
                            onConnect={onConnect}
                        />
                    );
                })
            )}
        </div>
    );
};

export default LeftSideHandles;
