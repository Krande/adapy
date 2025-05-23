import React, {memo} from 'react';
import {ProcedureHeader} from "./ProcedureHeader";
import {ParameterList} from "./Parameterlist";
import DynamicHandle from "../DynamicHandle";
import {run_procedure} from "../../../../utils/node_editor/comms/run_procedure";
import {ProcedureT} from "../../../../flatbuffers/procedures/procedure";

export function ProcedureNode({id, data}: { id: string; data: Record<string, any | ProcedureT> }) {
    return (
        <div className="bg-bl-background text-gray-200 rounded min-w-40 h-30">
            {/* Header Row */}
            <ProcedureHeader is_component={data.procedure.isComponent} label={data.label as string} onRun={() => run_procedure({id, data})}/>

            <div className="flex flex-row w-full h-full py-2">
                {/* Handle Rows Left */}
                <div className="flex flex-1 flex-col">
                    <ParameterList id={id} data={data}/>
                </div>

                {/* Handle Rows Right */}
                <div className="flex flex-col">
                    <DynamicHandle
                        key={`procedure-${id}-output`}
                        id={`${id}-file_object_output`}
                        type="target"
                        label="output_file"
                        left_side={false}
                    />
                </div>
            </div>
        </div>
    );
}

export default memo(ProcedureNode);