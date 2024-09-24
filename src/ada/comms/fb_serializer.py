import flatbuffers
from typing import Optional

from ada.comms.wsock import WebClient, FileObject, MeshInfo, SceneOperation, ProcedureStore, Procedure, Parameter, Message

from ada.comms.fb_model_gen import WebClientDC, FileObjectDC, MeshInfoDC, SceneOperationDC, ProcedureStoreDC, ProcedureDC, ParameterDC, MessageDC

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


def serialize_sceneoperation(builder: flatbuffers.Builder, obj: Optional[SceneOperationDC]) -> Optional[int]:
    if obj is None:
        return None

    SceneOperation.Start(builder)
    if obj.operation is not None:
        SceneOperation.AddOperation(builder, obj.operation.value)
    if obj.camera_position is not None:
        SceneOperation.AddCameraPosition(builder, builder.CreateFloatVector(obj.camera_position))
    if obj.look_at_position is not None:
        SceneOperation.AddLookAtPosition(builder, builder.CreateFloatVector(obj.look_at_position))
    return SceneOperation.End(builder)


def serialize_procedurestore(builder: flatbuffers.Builder, obj: Optional[ProcedureStoreDC]) -> Optional[int]:
    if obj is None:
        return None

    ProcedureStore.Start(builder)
    if obj.procedures is not None:
        procedures_list = [serialize_procedure(builder, item) for item in obj.procedures]
        ProcedureStore.AddProcedures(builder, builder.CreateByteVector(procedures_list))
    return ProcedureStore.End(builder)


def serialize_procedure(builder: flatbuffers.Builder, obj: Optional[ProcedureDC]) -> Optional[int]:
    if obj is None:
        return None
    id_str = None
    if obj.id is not None:
        id_str = builder.CreateString(str(obj.id))
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(str(obj.name))
    description_str = None
    if obj.description is not None:
        description_str = builder.CreateString(str(obj.description))
    script_file_location_str = None
    if obj.script_file_location is not None:
        script_file_location_str = builder.CreateString(str(obj.script_file_location))
    input_ifc_filepath_str = None
    if obj.input_ifc_filepath is not None:
        input_ifc_filepath_str = builder.CreateString(str(obj.input_ifc_filepath))
    output_ifc_filepath_str = None
    if obj.output_ifc_filepath is not None:
        output_ifc_filepath_str = builder.CreateString(str(obj.output_ifc_filepath))
    error_str = None
    if obj.error is not None:
        error_str = builder.CreateString(str(obj.error))

    Procedure.Start(builder)
    if id_str is not None:
        Procedure.AddId(builder, id_str)
    if name_str is not None:
        Procedure.AddName(builder, name_str)
    if description_str is not None:
        Procedure.AddDescription(builder, description_str)
    if script_file_location_str is not None:
        Procedure.AddScriptFileLocation(builder, script_file_location_str)
    if obj.parameters is not None:
        parameters_list = [serialize_parameter(builder, item) for item in obj.parameters]
        Procedure.AddParameters(builder, builder.CreateByteVector(parameters_list))
    if input_ifc_filepath_str is not None:
        Procedure.AddInputIfcFilepath(builder, input_ifc_filepath_str)
    if output_ifc_filepath_str is not None:
        Procedure.AddOutputIfcFilepath(builder, output_ifc_filepath_str)
    if error_str is not None:
        Procedure.AddError(builder, error_str)
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


def serialize_message(message: MessageDC, builder: flatbuffers.Builder=None) -> bytes:
    if builder is None:
        builder = flatbuffers.Builder(1024)
    file_object_obj = None
    if message.file_object is not None:
        file_object_obj = serialize_fileobject(builder, message.file_object)
    mesh_info_obj = None
    if message.mesh_info is not None:
        mesh_info_obj = serialize_meshinfo(builder, message.mesh_info)
    scene_operation_obj = None
    if message.scene_operation is not None:
        scene_operation_obj = serialize_sceneoperation(builder, message.scene_operation)
    procedure_store_obj = None
    if message.procedure_store is not None:
        procedure_store_obj = serialize_procedurestore(builder, message.procedure_store)

    Message.Start(builder)
    if message.instance_id is not None:
        Message.AddInstanceId(builder, message.instance_id)
    if message.command_type is not None:
        Message.AddCommandType(builder, message.command_type.value)
    if message.file_object is not None:
        Message.AddFileObject(builder, file_object_obj)
    if message.mesh_info is not None:
        Message.AddMeshInfo(builder, mesh_info_obj)
    if message.target_group is not None:
        Message.AddTargetGroup(builder, message.target_group.value)
    if message.client_type is not None:
        Message.AddClientType(builder, message.client_type.value)
    if message.scene_operation is not None:
        Message.AddSceneOperation(builder, scene_operation_obj)
    if message.target_id is not None:
        Message.AddTargetId(builder, message.target_id)
    if message.web_clients is not None:
        webclient_list = [serialize_webclient(builder, item) for item in message.web_clients]
        Message.AddWebClients(builder, builder.CreateByteVector(webclient_list))
    if message.procedure_store is not None:
        Message.AddProcedureStore(builder, procedure_store_obj)

    message_flatbuffer = Message.End(builder)
    builder.Finish(message_flatbuffer)
    return bytes(builder.Output())
