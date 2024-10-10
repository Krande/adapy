import React from "react";

const procedure_icon = <svg fill="#ffffff" width="12px" height="12px" viewBox="0 0 24 24"
                            xmlns="http://www.w3.org/2000/svg" enableBackground="new 0 0 24 24">
    <path
        d="M8.5,6H6.7C8.2,4.7,10,4,12,4c0.3,0,0.6,0,0.9,0.1c0,0,0,0,0,0c0.5,0.1,1-0.3,1.1-0.9c0.1-0.5-0.3-1-0.9-1.1C12.7,2,12.4,2,12,2C9.6,2,7.3,2.9,5.5,4.4V3c0-0.6-0.4-1-1-1s-1,0.4-1,1v4c0,0.6,0.4,1,1,1h4c0.6,0,1-0.4,1-1S9.1,6,8.5,6z M7,14.5c-0.6,0-1,0.4-1,1v1.8C4.7,15.8,4,14,4,12c0-0.3,0-0.6,0.1-0.9c0,0,0,0,0,0c0.1-0.5-0.3-1-0.9-1.1c-0.5-0.1-1,0.3-1.1,0.9C2,11.3,2,11.6,2,12c0,2.4,0.9,4.7,2.4,6.5H3c-0.6,0-1,0.4-1,1s0.4,1,1,1h4c0.3,0,0.6-0.2,0.8-0.4c0,0,0,0,0,0c0,0,0,0,0,0c0-0.1,0.1-0.2,0.1-0.3c0-0.1,0-0.1,0-0.2c0,0,0-0.1,0-0.1v-4C8,14.9,7.6,14.5,7,14.5z M21,5.5c0.6,0,1-0.4,1-1s-0.4-1-1-1h-4c-0.1,0-0.1,0-0.2,0c0,0,0,0,0,0c-0.1,0-0.2,0.1-0.3,0.1c0,0,0,0,0,0c-0.1,0.1-0.2,0.1-0.2,0.2c0,0,0,0,0,0c0,0,0,0,0,0c0,0.1-0.1,0.2-0.1,0.2c0,0.1,0,0.1,0,0.2c0,0,0,0.1,0,0.1v4c0,0.6,0.4,1,1,1s1-0.4,1-1V6.7c1.3,1.4,2,3.3,2,5.3c0,0.3,0,0.6-0.1,0.9c-0.1,0.5,0.3,1,0.9,1.1c0,0,0.1,0,0.1,0c0.5,0,0.9-0.4,1-0.9c0-0.4,0.1-0.7,0.1-1.1c0-2.4-0.9-4.7-2.4-6.5H21z M20.3,16.5c-0.1-0.1-0.2-0.2-0.3-0.3c0,0,0,0,0,0c0,0,0,0,0,0c-0.1-0.1-0.2-0.1-0.3-0.1c0,0-0.1,0-0.1,0c0,0-0.1,0-0.1,0h-4c-0.6,0-1,0.4-1,1s0.4,1,1,1h1.8c-1.4,1.3-3.3,2-5.3,2c-0.3,0-0.6,0-0.9-0.1c0,0,0,0,0,0c-0.5-0.1-1,0.3-1.1,0.9s0.3,1,0.9,1.1c0.4,0,0.7,0.1,1.1,0.1c2.4,0,4.7-0.9,6.5-2.4V21c0,0.6,0.4,1,1,1s1-0.4,1-1v-4C20.5,16.8,20.4,16.6,20.3,16.5C20.3,16.5,20.3,16.5,20.3,16.5z"/>
</svg>

const component_icon = <svg width="12px" height="12px" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M4.70711 12.7071L8 16L11.2929 12.7071L8 9.41421L4.70711 12.7071Z" fill="#ffffff"/>
    <path d="M3.29289 11.2929L6.58579 8L3.29289 4.70711L0 8L3.29289 11.2929Z" fill="#ffffff"/>
    <path d="M4.70711 3.29289L8 0L11.2929 3.29289L8 6.58579L4.70711 3.29289Z" fill="#ffffff"/>
    <path d="M12.7071 4.70711L9.41421 8L12.7071 11.2929L16 8L12.7071 4.70711Z" fill="#ffffff"/>
</svg>

export function ProcedureHeader({label, onRun, is_component}: {
    label: string;
    onRun: () => void,
    is_component: boolean
}) {
    return (
        <div className="bg-gray-800 flex flex-col justify-center items-center rounded-t">
            <div className="flex flex-row items-center">
                <div className="flex px-1">{is_component ? component_icon : procedure_icon}</div>
                <div className="flex text-center text-xs">{label}</div>
            </div>
            <button
                className="nodrag flex relative bg-blue-700 hover:bg-blue-700/50 text-white text-xs px-4 rounded"
                onClick={onRun}
            >
                Run
            </button>
        </div>
    );
}
