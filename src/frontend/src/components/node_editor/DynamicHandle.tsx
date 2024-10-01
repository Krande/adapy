// DynamicHandle.tsx
import React from 'react';
import {Connection, Handle, HandleType, Position} from '@xyflow/react';
import {FileObject} from "../../flatbuffers/wsock/file-object";

interface DynamicHandleProps {
    id: string;
    type: HandleType;
    label: string;
    isConnectable?: boolean;
    style?: React.CSSProperties;
    onConnect?: (params: Connection) => void;
    left_side?: boolean;
    datatype?: 'FileObject' | 'string' | 'float' | 'tuple'; // Define the supported data types
    value?: string | number | [number, number]; // Value prop for initial state
}

const DynamicHandle: React.FC<DynamicHandleProps> = ({
    id,
    type,
    label,
    isConnectable = true,
    style = { position: 'absolute', background: '#5aaf1a' },
    onConnect,
    left_side = true,
}) => {
    return (
        left_side ? (
            <div className="flex flex-row items-center">
                <span className="m-auto ml-0 mr-0 relative">
                    <Handle
                        type={type}
                        id={id}
                        position={Position.Left}
                        style={style}
                        isConnectable={isConnectable}
                        onConnect={onConnect}
                    />
                </span>
                <span className="pl-2 text-xs text-gray-300">{label}</span>
            </div>
        ) : (
            <div className="flex flex-row items-center">
                <span className="pl-1 text-center text-xs text-gray-300">{label}</span>
                <span className="m-auto ml-1 mr-0 relative">
                    <Handle
                        type={type}
                        id={id}
                        position={Position.Right}
                        style={style}
                        isConnectable={isConnectable}
                        onConnect={onConnect}
                    />
                </span>
            </div>
        )
    );
};

export default DynamicHandle;
