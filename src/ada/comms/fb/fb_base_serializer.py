from typing import Optional

import flatbuffers
from ada.comms.fb.base import (
    Error,
    FileArg,
    FileObject,
    FileObjectRef,
    Parameter,
    ProcedureStart,
    Value,
)
from ada.comms.fb.fb_base_gen import (
    ErrorDC,
    FileArgDC,
    FileObjectDC,
    FileObjectRefDC,
    ParameterDC,
    ProcedureStartDC,
    ValueDC,
)


def serialize_value(builder: flatbuffers.Builder, obj: Optional[ValueDC]) -> Optional[int]:
    if obj is None:
        return None
    string_value_str = None
    if obj.string_value is not None:
        string_value_str = builder.CreateString(str(obj.string_value))
    array_value_vector = None
    if obj.array_value is not None and len(obj.array_value) > 0:
        array_value_list = [serialize_value(builder, item) for item in obj.array_value]
        Value.StartArrayValueVector(builder, len(array_value_list))
        for item in reversed(array_value_list):
            builder.PrependUOffsetTRelative(item)
        array_value_vector = builder.EndVector()

    Value.Start(builder)
    if string_value_str is not None:
        Value.AddStringValue(builder, string_value_str)
    if obj.float_value is not None:
        Value.AddFloatValue(builder, obj.float_value)
    if obj.integer_value is not None:
        Value.AddIntegerValue(builder, obj.integer_value)
    if obj.boolean_value is not None:
        Value.AddBooleanValue(builder, obj.boolean_value)
    if obj.array_value is not None and len(obj.array_value) > 0:
        Value.AddArrayValue(builder, array_value_vector)
    if obj.array_value_type is not None:
        Value.AddArrayValueType(builder, obj.array_value_type.value)
    if obj.array_length is not None:
        Value.AddArrayLength(builder, obj.array_length)
    if obj.array_type is not None:
        Value.AddArrayType(builder, obj.array_type.value)
    if obj.array_any_length is not None:
        Value.AddArrayAnyLength(builder, obj.array_any_length)
    return Value.End(builder)


def serialize_parameter(builder: flatbuffers.Builder, obj: Optional[ParameterDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    value_obj = None
    if obj.value is not None:
        value_obj = serialize_value(builder, obj.value)
    default_value_obj = None
    if obj.default_value is not None:
        default_value_obj = serialize_value(builder, obj.default_value)
    options_vector = None
    if obj.options is not None and len(obj.options) > 0:
        options_list = [serialize_value(builder, item) for item in obj.options]
        Parameter.StartOptionsVector(builder, len(options_list))
        for item in reversed(options_list):
            builder.PrependUOffsetTRelative(item)
        options_vector = builder.EndVector()

    Parameter.Start(builder)
    if name_str is not None:
        Parameter.AddName(builder, name_str)
    if obj.type is not None:
        Parameter.AddType(builder, obj.type.value)
    if obj.value is not None:
        Parameter.AddValue(builder, value_obj)
    if obj.default_value is not None:
        Parameter.AddDefaultValue(builder, default_value_obj)
    if obj.options is not None and len(obj.options) > 0:
        Parameter.AddOptions(builder, options_vector)
    return Parameter.End(builder)


def serialize_error(builder: flatbuffers.Builder, obj: Optional[ErrorDC]) -> Optional[int]:
    if obj is None:
        return None
    message_str = None
    if obj.message is not None:
        message_str = builder.CreateString(str(obj.message))

    Error.Start(builder)
    if obj.code is not None:
        Error.AddCode(builder, obj.code)
    if message_str is not None:
        Error.AddMessage(builder, message_str)
    return Error.End(builder)


def serialize_procedurestart(builder: flatbuffers.Builder, obj: Optional[ProcedureStartDC]) -> Optional[int]:
    if obj is None:
        return None
    procedure_name_str = None
    if obj.procedure_name is not None:
        procedure_name_str = builder.CreateString(str(obj.procedure_name))
    procedure_id_string_str = None
    if obj.procedure_id_string is not None:
        procedure_id_string_str = builder.CreateString(str(obj.procedure_id_string))
    parameters_vector = None
    if obj.parameters is not None and len(obj.parameters) > 0:
        parameters_list = [serialize_parameter(builder, item) for item in obj.parameters]
        ProcedureStart.StartParametersVector(builder, len(parameters_list))
        for item in reversed(parameters_list):
            builder.PrependUOffsetTRelative(item)
        parameters_vector = builder.EndVector()

    ProcedureStart.Start(builder)
    if procedure_name_str is not None:
        ProcedureStart.AddProcedureName(builder, procedure_name_str)
    if procedure_id_string_str is not None:
        ProcedureStart.AddProcedureIdString(builder, procedure_id_string_str)
    if obj.parameters is not None and len(obj.parameters) > 0:
        ProcedureStart.AddParameters(builder, parameters_vector)
    return ProcedureStart.End(builder)


def serialize_fileobject(builder: flatbuffers.Builder, obj: Optional[FileObjectDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    filepath_str = None
    if obj.filepath is not None:
        filepath_str = builder.CreateString(str(obj.filepath))
    filedata_vector = None
    if obj.filedata is not None:
        filedata_vector = builder.CreateByteVector(obj.filedata)
    glb_file_obj = None
    if obj.glb_file is not None:
        glb_file_obj = serialize_fileobject(builder, obj.glb_file)
    ifcsqlite_file_obj = None
    if obj.ifcsqlite_file is not None:
        ifcsqlite_file_obj = serialize_fileobject(builder, obj.ifcsqlite_file)
    procedure_parent_obj = None
    if obj.procedure_parent is not None:
        procedure_parent_obj = serialize_procedurestart(builder, obj.procedure_parent)

    FileObject.Start(builder)
    if name_str is not None:
        FileObject.AddName(builder, name_str)
    if obj.file_type is not None:
        FileObject.AddFileType(builder, obj.file_type.value)
    if obj.purpose is not None:
        FileObject.AddPurpose(builder, obj.purpose.value)
    if filepath_str is not None:
        FileObject.AddFilepath(builder, filepath_str)
    if filedata_vector is not None:
        FileObject.AddFiledata(builder, filedata_vector)
    if obj.glb_file is not None:
        FileObject.AddGlbFile(builder, glb_file_obj)
    if obj.ifcsqlite_file is not None:
        FileObject.AddIfcsqliteFile(builder, ifcsqlite_file_obj)
    if obj.is_procedure_output is not None:
        FileObject.AddIsProcedureOutput(builder, obj.is_procedure_output)
    if obj.procedure_parent is not None:
        FileObject.AddProcedureParent(builder, procedure_parent_obj)
    if obj.compressed is not None:
        FileObject.AddCompressed(builder, obj.compressed)
    return FileObject.End(builder)


def serialize_fileobjectref(builder: flatbuffers.Builder, obj: Optional[FileObjectRefDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    filepath_str = None
    if obj.filepath is not None:
        filepath_str = builder.CreateString(str(obj.filepath))
    glb_file_obj = None
    if obj.glb_file is not None:
        glb_file_obj = serialize_fileobjectref(builder, obj.glb_file)
    ifcsqlite_file_obj = None
    if obj.ifcsqlite_file is not None:
        ifcsqlite_file_obj = serialize_fileobjectref(builder, obj.ifcsqlite_file)
    procedure_parent_obj = None
    if obj.procedure_parent is not None:
        procedure_parent_obj = serialize_procedurestart(builder, obj.procedure_parent)

    FileObjectRef.Start(builder)
    if name_str is not None:
        FileObjectRef.AddName(builder, name_str)
    if obj.file_type is not None:
        FileObjectRef.AddFileType(builder, obj.file_type.value)
    if obj.purpose is not None:
        FileObjectRef.AddPurpose(builder, obj.purpose.value)
    if filepath_str is not None:
        FileObjectRef.AddFilepath(builder, filepath_str)
    if obj.glb_file is not None:
        FileObjectRef.AddGlbFile(builder, glb_file_obj)
    if obj.ifcsqlite_file is not None:
        FileObjectRef.AddIfcsqliteFile(builder, ifcsqlite_file_obj)
    if obj.is_procedure_output is not None:
        FileObjectRef.AddIsProcedureOutput(builder, obj.is_procedure_output)
    if obj.procedure_parent is not None:
        FileObjectRef.AddProcedureParent(builder, procedure_parent_obj)
    return FileObjectRef.End(builder)


def serialize_filearg(builder: flatbuffers.Builder, obj: Optional[FileArgDC]) -> Optional[int]:
    if obj is None:
        return None
    arg_name_str = None
    if obj.arg_name is not None:
        arg_name_str = builder.CreateString(str(obj.arg_name))

    FileArg.Start(builder)
    if arg_name_str is not None:
        FileArg.AddArgName(builder, arg_name_str)
    if obj.file_type is not None:
        FileArg.AddFileType(builder, obj.file_type.value)
    return FileArg.End(builder)
