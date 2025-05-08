from ada.comms.fb.fb_base_gen import (
    ArrayTypeDC,
    ErrorDC,
    FileArgDC,
    FileObjectDC,
    FileObjectRefDC,
    FilePurposeDC,
    FileTypeDC,
    ParameterDC,
    ParameterTypeDC,
    ProcedureStartDC,
    ValueDC,
)


def deserialize_value(fb_obj) -> ValueDC | None:
    if fb_obj is None:
        return None

    return ValueDC(
        string_value=fb_obj.StringValue().decode("utf-8") if fb_obj.StringValue() is not None else None,
        float_value=fb_obj.FloatValue(),
        integer_value=fb_obj.IntegerValue(),
        boolean_value=fb_obj.BooleanValue(),
        array_value=(
            [deserialize_value(fb_obj.ArrayValue(i)) for i in range(fb_obj.ArrayValueLength())]
            if fb_obj.ArrayValueLength() > 0
            else None
        ),
        array_value_type=ParameterTypeDC(fb_obj.ArrayValueType()),
        array_length=fb_obj.ArrayLength(),
        array_type=ArrayTypeDC(fb_obj.ArrayType()),
        array_any_length=fb_obj.ArrayAnyLength(),
    )


def deserialize_parameter(fb_obj) -> ParameterDC | None:
    if fb_obj is None:
        return None

    return ParameterDC(
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        type=ParameterTypeDC(fb_obj.Type()),
        value=deserialize_value(fb_obj.Value()),
        default_value=deserialize_value(fb_obj.DefaultValue()),
        options=(
            [deserialize_value(fb_obj.Options(i)) for i in range(fb_obj.OptionsLength())]
            if fb_obj.OptionsLength() > 0
            else None
        ),
    )


def deserialize_error(fb_obj) -> ErrorDC | None:
    if fb_obj is None:
        return None

    return ErrorDC(
        code=fb_obj.Code(), message=fb_obj.Message().decode("utf-8") if fb_obj.Message() is not None else None
    )


def deserialize_procedurestart(fb_obj) -> ProcedureStartDC | None:
    if fb_obj is None:
        return None

    return ProcedureStartDC(
        procedure_name=fb_obj.ProcedureName().decode("utf-8") if fb_obj.ProcedureName() is not None else None,
        procedure_id_string=(
            fb_obj.ProcedureIdString().decode("utf-8") if fb_obj.ProcedureIdString() is not None else None
        ),
        parameters=(
            [deserialize_parameter(fb_obj.Parameters(i)) for i in range(fb_obj.ParametersLength())]
            if fb_obj.ParametersLength() > 0
            else None
        ),
    )


def deserialize_fileobject(fb_obj) -> FileObjectDC | None:
    if fb_obj is None:
        return None

    return FileObjectDC(
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        file_type=FileTypeDC(fb_obj.FileType()),
        purpose=FilePurposeDC(fb_obj.Purpose()),
        filepath=fb_obj.Filepath().decode("utf-8") if fb_obj.Filepath() is not None else None,
        filedata=bytes(fb_obj.FiledataAsNumpy()) if fb_obj.FiledataLength() > 0 else None,
        glb_file=deserialize_fileobject(fb_obj.GlbFile()),
        ifcsqlite_file=deserialize_fileobject(fb_obj.IfcsqliteFile()),
        is_procedure_output=fb_obj.IsProcedureOutput(),
        procedure_parent=deserialize_procedurestart(fb_obj.ProcedureParent()),
        compressed=fb_obj.Compressed(),
    )


def deserialize_fileobjectref(fb_obj) -> FileObjectRefDC | None:
    if fb_obj is None:
        return None

    return FileObjectRefDC(
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        file_type=FileTypeDC(fb_obj.FileType()),
        purpose=FilePurposeDC(fb_obj.Purpose()),
        filepath=fb_obj.Filepath().decode("utf-8") if fb_obj.Filepath() is not None else None,
        glb_file=deserialize_fileobjectref(fb_obj.GlbFile()),
        ifcsqlite_file=deserialize_fileobjectref(fb_obj.IfcsqliteFile()),
        is_procedure_output=fb_obj.IsProcedureOutput(),
        procedure_parent=deserialize_procedurestart(fb_obj.ProcedureParent()),
    )


def deserialize_filearg(fb_obj) -> FileArgDC | None:
    if fb_obj is None:
        return None

    return FileArgDC(
        arg_name=fb_obj.ArgName().decode("utf-8") if fb_obj.ArgName() is not None else None,
        file_type=FileTypeDC(fb_obj.FileType()),
    )
