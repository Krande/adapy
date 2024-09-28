from typing import Optional

import flatbuffers
from ada.comms.fb_model_gen import (
    CameraParamsDC,
    ErrorDC,
    FileObjectDC,
    MeshInfoDC,
    MessageDC,
    ParameterDC,
    ProcedureDC,
    ProcedureStartDC,
    ProcedureStoreDC,
    SceneDC,
    ServerReplyDC,
    WebClientDC,
)
from ada.comms.wsock import (
    CameraParams,
    Error,
    FileObject,
    MeshInfo,
    Message,
    Parameter,
    Procedure,
    ProcedureStart,
    ProcedureStore,
    Scene,
    ServerReply,
    WebClient,
)


def serialize_webclient(builder: flatbuffers.Builder, obj: Optional[WebClientDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    address_str = None
    if obj.address is not None:
        address_str = builder.CreateString(str(obj.address))

    WebClient.Start(builder)
    if obj.instance_id is not None:
        WebClient.AddInstanceId(builder, obj.instance_id)
    if name_str is not None:
        WebClient.AddName(builder, name_str)
    if address_str is not None:
        WebClient.AddAddress(builder, address_str)
    if obj.port is not None:
        WebClient.AddPort(builder, obj.port)
    return WebClient.End(builder)


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
    return FileObject.End(builder)


def serialize_meshinfo(builder: flatbuffers.Builder, obj: Optional[MeshInfoDC]) -> Optional[int]:
    if obj is None:
        return None
    object_name_str = None
    if obj.object_name is not None:
        object_name_str = builder.CreateString(str(obj.object_name))
    json_data_str = None
    if obj.json_data is not None:
        json_data_str = builder.CreateString(str(obj.json_data))

    MeshInfo.Start(builder)
    if object_name_str is not None:
        MeshInfo.AddObjectName(builder, object_name_str)
    if obj.face_index is not None:
        MeshInfo.AddFaceIndex(builder, obj.face_index)
    if json_data_str is not None:
        MeshInfo.AddJsonData(builder, json_data_str)
    return MeshInfo.End(builder)


def serialize_cameraparams(builder: flatbuffers.Builder, obj: Optional[CameraParamsDC]) -> Optional[int]:
    if obj is None:
        return None

    CameraParams.Start(builder)
    if obj.position is not None:
        CameraParams.AddPosition(builder, builder.CreateFloatVector(obj.position))
    if obj.look_at is not None:
        CameraParams.AddLookAt(builder, builder.CreateFloatVector(obj.look_at))
    if obj.up is not None:
        CameraParams.AddUp(builder, builder.CreateFloatVector(obj.up))
    if obj.fov is not None:
        CameraParams.AddFov(builder, obj.fov)
    if obj.near is not None:
        CameraParams.AddNear(builder, obj.near)
    if obj.far is not None:
        CameraParams.AddFar(builder, obj.far)
    if obj.force_camera is not None:
        CameraParams.AddForceCamera(builder, obj.force_camera)
    return CameraParams.End(builder)


def serialize_scene(builder: flatbuffers.Builder, obj: Optional[SceneDC]) -> Optional[int]:
    if obj is None:
        return None
    camera_params_obj = None
    if obj.camera_params is not None:
        camera_params_obj = serialize_cameraparams(builder, obj.camera_params)

    Scene.Start(builder)
    if obj.operation is not None:
        Scene.AddOperation(builder, obj.operation.value)
    if obj.camera_params is not None:
        Scene.AddCameraParams(builder, camera_params_obj)
    return Scene.End(builder)


def serialize_procedurestore(builder: flatbuffers.Builder, obj: Optional[ProcedureStoreDC]) -> Optional[int]:
    if obj is None:
        return None
    procedures_vector = None
    if obj.procedures is not None and len(obj.procedures) > 0:
        procedures_list = [serialize_procedure(builder, item) for item in obj.procedures]
        ProcedureStore.StartProceduresVector(builder, len(procedures_list))
        for item in reversed(procedures_list):
            builder.PrependUOffsetTRelative(item)
        procedures_vector = builder.EndVector(len(procedures_list))
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
        parameters_vector = builder.EndVector(len(parameters_list))
    input_file_var_str = None
    if obj.input_file_var is not None:
        input_file_var_str = builder.CreateString(str(obj.input_file_var))

    Procedure.Start(builder)
    if name_str is not None:
        Procedure.AddName(builder, name_str)
    if description_str is not None:
        Procedure.AddDescription(builder, description_str)
    if script_file_location_str is not None:
        Procedure.AddScriptFileLocation(builder, script_file_location_str)
    if obj.parameters is not None and len(obj.parameters) > 0:
        Procedure.AddParameters(builder, parameters_vector)
    if input_file_var_str is not None:
        Procedure.AddInputFileVar(builder, input_file_var_str)
    if obj.input_file_type is not None:
        Procedure.AddInputFileType(builder, obj.input_file_type.value)
    if obj.export_file_type is not None:
        Procedure.AddExportFileType(builder, obj.export_file_type.value)
    if obj.state is not None:
        Procedure.AddState(builder, obj.state.value)
    return Procedure.End(builder)


def serialize_parameter(builder: flatbuffers.Builder, obj: Optional[ParameterDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    type_str = None
    if obj.type is not None:
        type_str = builder.CreateString(str(obj.type))
    value_str = None
    if obj.value is not None:
        value_str = builder.CreateString(str(obj.value))

    Parameter.Start(builder)
    if name_str is not None:
        Parameter.AddName(builder, name_str)
    if type_str is not None:
        Parameter.AddType(builder, type_str)
    if value_str is not None:
        Parameter.AddValue(builder, value_str)
    return Parameter.End(builder)


def serialize_procedurestart(builder: flatbuffers.Builder, obj: Optional[ProcedureStartDC]) -> Optional[int]:
    if obj is None:
        return None
    procedure_name_str = None
    if obj.procedure_name is not None:
        procedure_name_str = builder.CreateString(str(obj.procedure_name))
    parameters_vector = None
    if obj.parameters is not None and len(obj.parameters) > 0:
        parameters_list = [serialize_parameter(builder, item) for item in obj.parameters]
        ProcedureStart.StartParametersVector(builder, len(parameters_list))
        for item in reversed(parameters_list):
            builder.PrependUOffsetTRelative(item)
        parameters_vector = builder.EndVector(len(parameters_list))

    ProcedureStart.Start(builder)
    if procedure_name_str is not None:
        ProcedureStart.AddProcedureName(builder, procedure_name_str)
    if obj.parameters is not None and len(obj.parameters) > 0:
        ProcedureStart.AddParameters(builder, parameters_vector)
    return ProcedureStart.End(builder)


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


def serialize_serverreply(builder: flatbuffers.Builder, obj: Optional[ServerReplyDC]) -> Optional[int]:
    if obj is None:
        return None
    message_str = None
    if obj.message is not None:
        message_str = builder.CreateString(str(obj.message))
    error_obj = None
    if obj.error is not None:
        error_obj = serialize_error(builder, obj.error)

    ServerReply.Start(builder)
    if message_str is not None:
        ServerReply.AddMessage(builder, message_str)
    if obj.reply_to is not None:
        ServerReply.AddReplyTo(builder, obj.reply_to.value)
    if obj.error is not None:
        ServerReply.AddError(builder, error_obj)
    return ServerReply.End(builder)


def serialize_message(message: MessageDC, builder: flatbuffers.Builder = None) -> bytes:
    if builder is None:
        builder = flatbuffers.Builder(1024)
    scene_obj = None
    if message.scene is not None:
        scene_obj = serialize_scene(builder, message.scene)
    file_object_obj = None
    if message.file_object is not None:
        file_object_obj = serialize_fileobject(builder, message.file_object)
    mesh_info_obj = None
    if message.mesh_info is not None:
        mesh_info_obj = serialize_meshinfo(builder, message.mesh_info)
    procedure_store_obj = None
    if message.procedure_store is not None:
        procedure_store_obj = serialize_procedurestore(builder, message.procedure_store)
    server_reply_obj = None
    if message.server_reply is not None:
        server_reply_obj = serialize_serverreply(builder, message.server_reply)

    Message.Start(builder)
    if message.instance_id is not None:
        Message.AddInstanceId(builder, message.instance_id)
    if message.command_type is not None:
        Message.AddCommandType(builder, message.command_type.value)
    if message.scene is not None:
        Message.AddScene(builder, scene_obj)
    if message.file_object is not None:
        Message.AddFileObject(builder, file_object_obj)
    if message.mesh_info is not None:
        Message.AddMeshInfo(builder, mesh_info_obj)
    if message.target_group is not None:
        Message.AddTargetGroup(builder, message.target_group.value)
    if message.client_type is not None:
        Message.AddClientType(builder, message.client_type.value)
    if message.target_id is not None:
        Message.AddTargetId(builder, message.target_id)
    if message.web_clients is not None:
        webclient_list = [serialize_webclient(builder, item) for item in message.web_clients]
        Message.AddWebClients(builder, builder.CreateByteVector(webclient_list))
    if message.procedure_store is not None:
        Message.AddProcedureStore(builder, procedure_store_obj)
    if message.server_reply is not None:
        Message.AddServerReply(builder, server_reply_obj)

    message_flatbuffer = Message.End(builder)
    builder.Finish(message_flatbuffer)
    return bytes(builder.Output())
