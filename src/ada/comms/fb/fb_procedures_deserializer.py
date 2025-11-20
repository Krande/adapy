from ada.comms.fb.fb_base_deserializer import (
    deserialize_filearg,
    deserialize_parameter,
    deserialize_procedurestart,
)
from ada.comms.fb.fb_procedures_gen import (
    ProcedureDC,
    ProcedureStateDC,
    ProcedureStoreDC,
)


def deserialize_procedurestore(fb_obj) -> ProcedureStoreDC | None:
    if fb_obj is None:
        return None

    return ProcedureStoreDC(
        procedures=(
            [deserialize_procedure(fb_obj.Procedures(i)) for i in range(fb_obj.ProceduresLength())]
            if fb_obj.ProceduresLength() > 0
            else None
        ),
        start_procedure=deserialize_procedurestart(fb_obj.StartProcedure()),
    )


def deserialize_procedure(fb_obj) -> ProcedureDC | None:
    if fb_obj is None:
        return None

    return ProcedureDC(
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        description=fb_obj.Description().decode("utf-8") if fb_obj.Description() is not None else None,
        script_file_location=(
            fb_obj.ScriptFileLocation().decode("utf-8") if fb_obj.ScriptFileLocation() is not None else None
        ),
        parameters=(
            [deserialize_parameter(fb_obj.Parameters(i)) for i in range(fb_obj.ParametersLength())]
            if fb_obj.ParametersLength() > 0
            else None
        ),
        file_inputs=(
            [deserialize_filearg(fb_obj.FileInputs(i)) for i in range(fb_obj.FileInputsLength())]
            if fb_obj.FileInputsLength() > 0
            else None
        ),
        file_outputs=(
            [deserialize_filearg(fb_obj.FileOutputs(i)) for i in range(fb_obj.FileOutputsLength())]
            if fb_obj.FileOutputsLength() > 0
            else None
        ),
        state=ProcedureStateDC(fb_obj.State()),
        is_component=fb_obj.IsComponent(),
    )
