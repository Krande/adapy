from typing import Optional

import flatbuffers
from ada.comms.fb_model_gen import (
    CameraParamsDC,
    ErrorDC,
    FileArgDC,
    FileObjectDC,
    FileObjectRefDC,
    MeshInfoDC,
    MessageDC,
    ParameterDC,
    ProcedureDC,
    ProcedureStartDC,
    ProcedureStoreDC,
    SceneDC,
    ServerDC,
    ServerReplyDC,
    ValueDC,
    WebClientDC,
)
from ada.comms.wsock import (
    CameraParams,
    Error,
    FileArg,
    FileObject,
    FileObjectRef,
    MeshInfo,
    Message,
    Parameter,
    Procedure,
    ProcedureStart,
    ProcedureStore,
    Scene,
    Server,
    ServerReply,
    Value,
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


def serialize_meshinfo(builder: flatbuffers.Builder, obj: Optional[MeshInfoDC]) -> Optional[int]:
    if obj is None:
        return None
    object_name_str = None
    if obj.object_name is not None:
        object_name_str = builder.CreateString(str(obj.object_name))
    json_data_str = None
    if obj.json_data is not None:
        json_data_str = builder.CreateString(str(obj.json_data))
    file_name_str = None
    if obj.file_name is not None:
        file_name_str = builder.CreateString(str(obj.file_name))

    MeshInfo.Start(builder)
    if object_name_str is not None:
        MeshInfo.AddObjectName(builder, object_name_str)
    if obj.face_index is not None:
        MeshInfo.AddFaceIndex(builder, obj.face_index)
    if json_data_str is not None:
        MeshInfo.AddJsonData(builder, json_data_str)
    if file_name_str is not None:
        MeshInfo.AddFileName(builder, file_name_str)
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
    current_file_obj = None
    if obj.current_file is not None:
        current_file_obj = serialize_fileobject(builder, obj.current_file)

    Scene.Start(builder)
    if obj.operation is not None:
        Scene.AddOperation(builder, obj.operation.value)
    if obj.camera_params is not None:
        Scene.AddCameraParams(builder, camera_params_obj)
    if obj.current_file is not None:
        Scene.AddCurrentFile(builder, current_file_obj)
    return Scene.End(builder)


def serialize_server(builder: flatbuffers.Builder, obj: Optional[ServerDC]) -> Optional[int]:
    if obj is None:
        return None
    new_file_object_obj = None
    if obj.new_file_object is not None:
        new_file_object_obj = serialize_fileobject(builder, obj.new_file_object)
    all_file_objects_vector = None
    if obj.all_file_objects is not None and len(obj.all_file_objects) > 0:
        all_file_objects_list = [serialize_fileobject(builder, item) for item in obj.all_file_objects]
        Server.StartAllFileObjectsVector(builder, len(all_file_objects_list))
        for item in reversed(all_file_objects_list):
            builder.PrependUOffsetTRelative(item)
        all_file_objects_vector = builder.EndVector(len(all_file_objects_list))
    get_file_object_by_name_str = None
    if obj.get_file_object_by_name is not None:
        get_file_object_by_name_str = builder.CreateString(str(obj.get_file_object_by_name))
    get_file_object_by_path_str = None
    if obj.get_file_object_by_path is not None:
        get_file_object_by_path_str = builder.CreateString(str(obj.get_file_object_by_path))
    delete_file_object_obj = None
    if obj.delete_file_object is not None:
        delete_file_object_obj = serialize_fileobject(builder, obj.delete_file_object)
    start_file_in_local_app_obj = None
    if obj.start_file_in_local_app is not None:
        start_file_in_local_app_obj = serialize_fileobject(builder, obj.start_file_in_local_app)

    Server.Start(builder)
    if obj.new_file_object is not None:
        Server.AddNewFileObject(builder, new_file_object_obj)
    if obj.all_file_objects is not None and len(obj.all_file_objects) > 0:
        Server.AddAllFileObjects(builder, all_file_objects_vector)
    if get_file_object_by_name_str is not None:
        Server.AddGetFileObjectByName(builder, get_file_object_by_name_str)
    if get_file_object_by_path_str is not None:
        Server.AddGetFileObjectByPath(builder, get_file_object_by_path_str)
    if obj.delete_file_object is not None:
        Server.AddDeleteFileObject(builder, delete_file_object_obj)
    if obj.start_file_in_local_app is not None:
        Server.AddStartFileInLocalApp(builder, start_file_in_local_app_obj)
    return Server.End(builder)


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
    file_inputs_vector = None
    if obj.file_inputs is not None and len(obj.file_inputs) > 0:
        file_inputs_list = [serialize_filearg(builder, item) for item in obj.file_inputs]
        Procedure.StartFileInputsVector(builder, len(file_inputs_list))
        for item in reversed(file_inputs_list):
            builder.PrependUOffsetTRelative(item)
        file_inputs_vector = builder.EndVector(len(file_inputs_list))
    file_outputs_vector = None
    if obj.file_outputs is not None and len(obj.file_outputs) > 0:
        file_outputs_list = [serialize_filearg(builder, item) for item in obj.file_outputs]
        Procedure.StartFileOutputsVector(builder, len(file_outputs_list))
        for item in reversed(file_outputs_list):
            builder.PrependUOffsetTRelative(item)
        file_outputs_vector = builder.EndVector(len(file_outputs_list))

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
        array_value_vector = builder.EndVector(len(array_value_list))

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
        options_vector = builder.EndVector(len(options_list))

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
        parameters_vector = builder.EndVector(len(parameters_list))

    ProcedureStart.Start(builder)
    if procedure_name_str is not None:
        ProcedureStart.AddProcedureName(builder, procedure_name_str)
    if procedure_id_string_str is not None:
        ProcedureStart.AddProcedureIdString(builder, procedure_id_string_str)
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
    file_objects_vector = None
    if obj.file_objects is not None and len(obj.file_objects) > 0:
        file_objects_list = [serialize_fileobject(builder, item) for item in obj.file_objects]
        ServerReply.StartFileObjectsVector(builder, len(file_objects_list))
        for item in reversed(file_objects_list):
            builder.PrependUOffsetTRelative(item)
        file_objects_vector = builder.EndVector(len(file_objects_list))
    error_obj = None
    if obj.error is not None:
        error_obj = serialize_error(builder, obj.error)

    ServerReply.Start(builder)
    if message_str is not None:
        ServerReply.AddMessage(builder, message_str)
    if obj.file_objects is not None and len(obj.file_objects) > 0:
        ServerReply.AddFileObjects(builder, file_objects_vector)
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
    server_obj = None
    if message.server is not None:
        server_obj = serialize_server(builder, message.server)
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
    if message.server is not None:
        Message.AddServer(builder, server_obj)
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
