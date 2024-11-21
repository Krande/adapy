import React from "react";
import ProcedureIcon from "../../../icons/ProcedureIcon";
import ComponentIcon from "../../../icons/ComponentIcon";


export function ProcedureHeader({label, onRun, is_component}: {
    label: string;
    onRun: () => void,
    is_component: boolean
}) {
    return (
        <div className="bg-gray-800 flex flex-col justify-center items-center rounded-t">
            <div className="flex flex-row items-center">
                <div className="flex px-1">{is_component ? <ComponentIcon/> : <ProcedureIcon/>}</div>
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
