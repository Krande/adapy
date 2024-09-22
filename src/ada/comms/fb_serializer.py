import flatbuffers
from typing import Optional

from ada.comms.wsock import FileObject, BinaryData, MeshInfo, Message

from ada.comms.fb_model_gen import FileObjectDC, BinaryDataDC, MeshInfoDC, MessageDC

def serialize_fileobject(builder: flatbuffers.Builder, obj: Optional[FileObjectDC]) -> Optional[int]:
    if obj is None:
        return None
    filepath_str = builder.CreateString(obj.filepath)

    FileObject.Start(builder)
    FileObject.AddFileType(builder, obj.file_type.value)
    FileObject.AddPurpose(builder, obj.purpose.value)
    FileObject.AddFilepath(builder, filepath_str)
    return FileObject.End(builder)


def serialize_binarydata(builder: flatbuffers.Builder, obj: Optional[BinaryDataDC]) -> Optional[int]:
    if obj is None:
        return None
    data_vector = builder.CreateByteVector(obj.data)

    BinaryData.Start(builder)
    BinaryData.AddData(builder, data_vector)
    return BinaryData.End(builder)


def serialize_meshinfo(builder: flatbuffers.Builder, obj: Optional[MeshInfoDC]) -> Optional[int]:
    if obj is None:
        return None
    object_name_str = builder.CreateString(obj.object_name)

    MeshInfo.Start(builder)
    MeshInfo.AddObjectName(builder, object_name_str)
    MeshInfo.AddFaceIndex(builder, obj.face_index)
    return MeshInfo.End(builder)


def serialize_message(message: MessageDC, builder: flatbuffers.Builder=None) -> bytes:
    if builder is None:
        builder = flatbuffers.Builder(1024)
    file_object_obj = None
    if message.file_object is not None:
        file_object_obj = serialize_fileobject(builder, message.file_object)
    binary_data_obj = None
    if message.binary_data is not None:
        binary_data_obj = serialize_binarydata(builder, message.binary_data)
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
    if message.binary_data is not None:
        Message.AddBinaryData(builder, binary_data_obj)
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

    message_flatbuffer = Message.End(builder)
    builder.Finish(message_flatbuffer)
    return bytes(builder.Output())
