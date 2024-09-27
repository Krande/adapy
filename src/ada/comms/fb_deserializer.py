from ada.comms.fb_model_gen import (
    CameraParamsDC,
    CommandTypeDC,
    ErrorDC,
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MeshInfoDC,
    MessageDC,
    ParameterDC,
    ProcedureDC,
    ProcedureStoreDC,
    SceneOperationDC,
    SceneOperationsDC,
    TargetTypeDC,
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
    )


def deserialize_meshinfo(fb_obj) -> MeshInfoDC | None:
    if fb_obj is None:
        return None

    return MeshInfoDC(
        object_name=fb_obj.ObjectName().decode("utf-8") if fb_obj.ObjectName() is not None else None,
        face_index=fb_obj.FaceIndex(),
        json_data=fb_obj.JsonData().decode("utf-8") if fb_obj.JsonData() is not None else None,
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


def deserialize_sceneoperation(fb_obj) -> SceneOperationDC | None:
    if fb_obj is None:
        return None

    return SceneOperationDC(
        operation=SceneOperationsDC(fb_obj.Operation()), camera_params=deserialize_cameraparams(fb_obj.CameraParams())
    )


def deserialize_procedurestore(fb_obj) -> ProcedureStoreDC | None:
    if fb_obj is None:
        return None

    return ProcedureStoreDC(
        procedures=(
            [deserialize_procedure(fb_obj.Procedures(i)) for i in range(fb_obj.ProceduresLength())]
            if fb_obj.ProceduresLength() > 0
            else None
        )
    )


def deserialize_procedure(fb_obj) -> ProcedureDC | None:
    if fb_obj is None:
        return None

    return ProcedureDC(
        id=fb_obj.Id().decode("utf-8") if fb_obj.Id() is not None else None,
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
        input_ifc_filepath=fb_obj.InputIfcFilepath().decode("utf-8") if fb_obj.InputIfcFilepath() is not None else None,
        output_ifc_filepath=(
            fb_obj.OutputIfcFilepath().decode("utf-8") if fb_obj.OutputIfcFilepath() is not None else None
        ),
        error=fb_obj.Error().decode("utf-8") if fb_obj.Error() is not None else None,
    )


def deserialize_parameter(fb_obj) -> ParameterDC | None:
    if fb_obj is None:
        return None

    return ParameterDC(
        name=fb_obj.Name().decode("utf-8") if fb_obj.Name() is not None else None,
        type=fb_obj.Type().decode("utf-8") if fb_obj.Type() is not None else None,
        value=fb_obj.Value().decode("utf-8") if fb_obj.Value() is not None else None,
    )


def deserialize_error(fb_obj) -> ErrorDC | None:
    if fb_obj is None:
        return None

    return ErrorDC(
        code=fb_obj.Code(), message=fb_obj.Message().decode("utf-8") if fb_obj.Message() is not None else None
    )


def deserialize_message(fb_obj) -> MessageDC | None:
    if fb_obj is None:
        return None

    return MessageDC(
        instance_id=fb_obj.InstanceId(),
        command_type=CommandTypeDC(fb_obj.CommandType()),
        file_object=deserialize_fileobject(fb_obj.FileObject()),
        mesh_info=deserialize_meshinfo(fb_obj.MeshInfo()),
        target_group=TargetTypeDC(fb_obj.TargetGroup()),
        client_type=TargetTypeDC(fb_obj.ClientType()),
        scene_operation=deserialize_sceneoperation(fb_obj.SceneOperation()),
        target_id=fb_obj.TargetId(),
        web_clients=(
            [deserialize_webclient(fb_obj.WebClients(i)) for i in range(fb_obj.WebClientsLength())]
            if fb_obj.WebClientsLength() > 0
            else None
        ),
        procedure_store=deserialize_procedurestore(fb_obj.ProcedureStore()),
    )


def deserialize_root_message(bytes_obj: bytes) -> MessageDC:
    fb_obj = Message.Message.GetRootAsMessage(bytes_obj, 0)
    return deserialize_message(fb_obj)
