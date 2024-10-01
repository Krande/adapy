// RightSideHandle.tsx
import React from 'react';
import { Handle, Position, Connection } from '@xyflow/react';

interface RightSideHandleProps {
    onConnect: (params: Connection) => void;
}

const RightSideHandle: React.FC<RightSideHandleProps> = ({ onConnect }) => {
    return (
        <div className="flex flex-row items-center">
            <span className="pl-1 text-center text-xs text-gray-300">Output File</span>
            <span className="m-auto ml-1 mr-0 relative">
                <Handle
                    type="target"
                    id="file_object_output"
                    position={Position.Right}
                    style={{ marginTop: 'auto', position: 'absolute', background: '#555' }}
                    isConnectable={true}
                    onConnect={onConnect}
                />
            </span>
        </div>
    );
};

export default RightSideHandle;
