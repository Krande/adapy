import flatbuffers
from typing import Optional

from ada.comms.wsock import WebClient, FileObject, MeshInfo, Message

from ada.comms.fb_model_gen import WebClientDC, FileObjectDC, MeshInfoDC, MessageDC

def serialize_webclient(builder: flatbuffers.Builder, obj: Optional[WebClientDC]) -> Optional[int]:
    if obj is None:
        return None
    name_str = None
    if obj.name is not None:
        name_str = builder.CreateString(obj.name)
    address_str = None
    if obj.address is not None:
        address_str = builder.CreateString(obj.address)

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
    filepath_str = None
    if obj.filepath is not None:
        filepath_str = builder.CreateString(obj.filepath)
    filedata_vector = None
    if obj.filedata is not None:
        filedata_vector = builder.CreateByteVector(obj.filedata)

    FileObject.Start(builder)
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
        object_name_str = builder.CreateString(obj.object_name)

    MeshInfo.Start(builder)
    if object_name_str is not None:
        MeshInfo.AddObjectName(builder, object_name_str)
    if obj.face_index is not None:
        MeshInfo.AddFaceIndex(builder, obj.face_index)
    return MeshInfo.End(builder)


def serialize_message(message: MessageDC, builder: flatbuffers.Builder=None) -> bytes:
    if builder is None:
        builder = flatbuffers.Builder(1024)
    file_object_obj = None
    if message.file_object is not None:
        file_object_obj = serialize_fileobject(builder, message.file_object)
    mesh_info_obj = None
    if message.mesh_info is not None:
        mesh_info_obj = serialize_meshinfo(builder, message.mesh_info)
    target_group_str = None
    if message.target_group is not None:
        target_group_str = builder.CreateString(message.target_group)
    client_type_str = None
    if message.client_type is not None:
        client_type_str = builder.CreateString(message.client_type)

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
        Message.AddTargetGroup(builder, target_group_str)
    if message.client_type is not None:
        Message.AddClientType(builder, client_type_str)
    if message.scene_operation is not None:
        Message.AddSceneOperation(builder, message.scene_operation.value)
    if message.target_id is not None:
        Message.AddTargetId(builder, message.target_id)
    if message.web_clients is not None:
        webclient_list = [serialize_webclient(builder, item) for item in message.web_clients]
        Message.AddWebClients(builder, builder.CreateByteVector(webclient_list))

    message_flatbuffer = Message.End(builder)
    builder.Finish(message_flatbuffer)
    return bytes(builder.Output())
