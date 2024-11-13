from ada.comms.fb_model_gen import (
    ArrayTypeDC,
    CameraParamsDC,
    CommandTypeDC,
    ErrorDC,
    FileArgDC,
    FileObjectDC,
    FileObjectRefDC,
    FilePurposeDC,
    FileTypeDC,
    MeshInfoDC,
    MessageDC,
    ParameterDC,
    ParameterTypeDC,
    ProcedureDC,
    ProcedureStartDC,
    ProcedureStateDC,
    ProcedureStoreDC,
    SceneDC,
    SceneOperationsDC,
    ServerDC,
    ServerReplyDC,
    TargetTypeDC,
    ValueDC,
    WebClientDC,
)
from ada.comms.wsock import Message


def deserialize_webclient(fb_obj) -> WebClientDC | None:
    if fb_obj is None:
        return None

    return WebClientDC(
        instance_id=fb_obj.InstanceId(),
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        address=fb_obj.Address().decode("utf-8") if fb_obj.Address() is not None else None,
        port=fb_obj.Port(),
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


def deserialize_meshinfo(fb_obj) -> MeshInfoDC | None:
    if fb_obj is None:
        return None

    return MeshInfoDC(
        object_name=fb_obj.ObjectName().decode("utf-8") if fb_obj.ObjectName() is not None else None,
        face_index=fb_obj.FaceIndex(),
        json_data=fb_obj.JsonData().decode("utf-8") if fb_obj.JsonData() is not None else None,
        file_name=fb_obj.FileName().decode("utf-8") if fb_obj.FileName() is not None else None,
    )


def deserialize_cameraparams(fb_obj) -> CameraParamsDC | None:
    if fb_obj is None:
        return None

    return CameraParamsDC(
        position=[fb_obj.Position(i) for i in range(fb_obj.PositionLength())] if fb_obj.PositionLength() > 0 else None,
        look_at=[fb_obj.LookAt(i) for i in range(fb_obj.LookAtLength())] if fb_obj.LookAtLength() > 0 else None,
        up=[fb_obj.Up(i) for i in range(fb_obj.UpLength())] if fb_obj.UpLength() > 0 else None,
        fov=fb_obj.Fov(),
        near=fb_obj.Near(),
        far=fb_obj.Far(),
        force_camera=fb_obj.ForceCamera(),
    )


def deserialize_scene(fb_obj) -> SceneDC | None:
    if fb_obj is None:
        return None

    return SceneDC(
        operation=SceneOperationsDC(fb_obj.Operation()),
        camera_params=deserialize_cameraparams(fb_obj.CameraParams()),
        current_file=deserialize_fileobject(fb_obj.CurrentFile()),
    )


def deserialize_server(fb_obj) -> ServerDC | None:
    if fb_obj is None:
        return None

    return ServerDC(
        new_file_object=deserialize_fileobject(fb_obj.NewFileObject()),
        all_file_objects=(
            [deserialize_fileobject(fb_obj.AllFileObjects(i)) for i in range(fb_obj.AllFileObjectsLength())]
            if fb_obj.AllFileObjectsLength() > 0
            else None
        ),
        get_file_object_by_name=(
            fb_obj.GetFileObjectByName().decode("utf-8") if fb_obj.GetFileObjectByName() is not None else None
        ),
        get_file_object_by_path=(
            fb_obj.GetFileObjectByPath().decode("utf-8") if fb_obj.GetFileObjectByPath() is not None else None
        ),
        delete_file_object=deserialize_fileobject(fb_obj.DeleteFileObject()),
        start_file_in_local_app=deserialize_fileobject(fb_obj.StartFileInLocalApp()),
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


def deserialize_filearg(fb_obj) -> FileArgDC | None:
    if fb_obj is None:
        return None

    return FileArgDC(
        arg_name=fb_obj.ArgName().decode("utf-8") if fb_obj.ArgName() is not None else None,
        file_type=FileTypeDC(fb_obj.FileType()),
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


def deserialize_error(fb_obj) -> ErrorDC | None:
    if fb_obj is None:
        return None

    return ErrorDC(
        code=fb_obj.Code(), message=fb_obj.Message().decode("utf-8") if fb_obj.Message() is not None else None
    )


def deserialize_serverreply(fb_obj) -> ServerReplyDC | None:
    if fb_obj is None:
        return None

    return ServerReplyDC(
        message=fb_obj.Message().decode("utf-8") if fb_obj.Message() is not None else None,
        file_objects=(
            [deserialize_fileobject(fb_obj.FileObjects(i)) for i in range(fb_obj.FileObjectsLength())]
            if fb_obj.FileObjectsLength() > 0
            else None
        ),
        reply_to=CommandTypeDC(fb_obj.ReplyTo()),
        error=deserialize_error(fb_obj.Error()),
    )


def deserialize_message(fb_obj) -> MessageDC | None:
    if fb_obj is None:
        return None

    return MessageDC(
        instance_id=fb_obj.InstanceId(),
        command_type=CommandTypeDC(fb_obj.CommandType()),
        scene=deserialize_scene(fb_obj.Scene()),
        server=deserialize_server(fb_obj.Server()),
        mesh_info=deserialize_meshinfo(fb_obj.MeshInfo()),
        target_group=TargetTypeDC(fb_obj.TargetGroup()),
        client_type=TargetTypeDC(fb_obj.ClientType()),
        target_id=fb_obj.TargetId(),
        web_clients=(
            [deserialize_webclient(fb_obj.WebClients(i)) for i in range(fb_obj.WebClientsLength())]
            if fb_obj.WebClientsLength() > 0
            else None
        ),
        procedure_store=deserialize_procedurestore(fb_obj.ProcedureStore()),
        server_reply=deserialize_serverreply(fb_obj.ServerReply()),
    )


def deserialize_root_message(bytes_obj: bytes) -> MessageDC:
    fb_obj = Message.Message.GetRootAsMessage(bytes_obj, 0)
    return deserialize_message(fb_obj)
