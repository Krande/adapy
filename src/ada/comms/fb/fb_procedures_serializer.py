from typing import Optional

import flatbuffers
from ada.comms.fb.fb_base_serializer import (
    serialize_filearg,
    serialize_parameter,
    serialize_procedurestart,
)
from ada.comms.fb.fb_procedures_gen import ProcedureDC, ProcedureStoreDC
from ada.comms.fb.procedures import Procedure, ProcedureStore


def serialize_procedurestore(builder: flatbuffers.Builder, obj: Optional[ProcedureStoreDC]) -> Optional[int]:
    if obj is None:
        return None
    procedures_vector = None
    if obj.procedures is not None and len(obj.procedures) > 0:
        procedures_list = [serialize_procedure(builder, item) for item in obj.procedures]
        ProcedureStore.StartProceduresVector(builder, len(procedures_list))
        for item in reversed(procedures_list):
            builder.PrependUOffsetTRelative(item)
        procedures_vector = builder.EndVector()
    start_procedure_obj = None
    if obj.start_procedure is not None:
        start_procedure_obj = serialize_procedurestart(builder, obj.start_procedure)

    ProcedureStore.Start(builder)
    if obj.procedures is not None and len(obj.procedures) > 0:
        ProcedureStore.AddProcedures(builder, procedures_vector)
    if obj.start_procedure is not None:
        ProcedureStore.AddStartProcedure(builder, start_procedure_obj)
    return ProcedureStore.End(builder)


def serialize_procedure(builder: flatbuffers.Builder, obj: Optional[ProcedureDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    description_str = None
    if obj.description is not None:
        description_str = builder.CreateString(str(obj.description))
    script_file_location_str = None
    if obj.script_file_location is not None:
        script_file_location_str = builder.CreateString(str(obj.script_file_location))
    parameters_vector = None
    if obj.parameters is not None and len(obj.parameters) > 0:
        parameters_list = [serialize_parameter(builder, item) for item in obj.parameters]
        Procedure.StartParametersVector(builder, len(parameters_list))
        for item in reversed(parameters_list):
            builder.PrependUOffsetTRelative(item)
        parameters_vector = builder.EndVector()
    file_inputs_vector = None
    if obj.file_inputs is not None and len(obj.file_inputs) > 0:
        file_inputs_list = [serialize_filearg(builder, item) for item in obj.file_inputs]
        Procedure.StartFileInputsVector(builder, len(file_inputs_list))
        for item in reversed(file_inputs_list):
            builder.PrependUOffsetTRelative(item)
        file_inputs_vector = builder.EndVector()
    file_outputs_vector = None
    if obj.file_outputs is not None and len(obj.file_outputs) > 0:
        file_outputs_list = [serialize_filearg(builder, item) for item in obj.file_outputs]
        Procedure.StartFileOutputsVector(builder, len(file_outputs_list))
        for item in reversed(file_outputs_list):
            builder.PrependUOffsetTRelative(item)
        file_outputs_vector = builder.EndVector()

    Procedure.Start(builder)
    if name_str is not None:
        Procedure.AddName(builder, name_str)
    if description_str is not None:
        Procedure.AddDescription(builder, description_str)
    if script_file_location_str is not None:
        Procedure.AddScriptFileLocation(builder, script_file_location_str)
    if obj.parameters is not None and len(obj.parameters) > 0:
        Procedure.AddParameters(builder, parameters_vector)
    if obj.file_inputs is not None and len(obj.file_inputs) > 0:
        Procedure.AddFileInputs(builder, file_inputs_vector)
    if obj.file_outputs is not None and len(obj.file_outputs) > 0:
        Procedure.AddFileOutputs(builder, file_outputs_vector)
    if obj.state is not None:
        Procedure.AddState(builder, obj.state.value)
    if obj.is_component is not None:
        Procedure.AddIsComponent(builder, obj.is_component)
    return Procedure.End(builder)
