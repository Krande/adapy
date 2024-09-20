from typing import Optional

import flatbuffers
# Your Python dataclasses
from .fb_model_gen import FileObjectDC, BinaryDataDC, MeshInfoDC, MessageDC

from ada.comms.wsock import Message, FileObject, BinaryData, MeshInfo


# Serialize FileObject dataclass to FlatBuffer
def serialize_file_object(builder: flatbuffers.Builder, file_object: Optional[FileObjectDC]) -> Optional[int]:
    if file_object is None:
        return None
    filepath = builder.CreateString(file_object.filepath)

    FileObject.Start(builder)
    FileObject.AddFileType(builder, file_object.file_type.value)
    FileObject.AddPurpose(builder, file_object.purpose.value)
    FileObject.AddFilepath(builder, filepath)
    return FileObject.End(builder)


# Serialize BinaryData dataclass to FlatBuffer
def serialize_binary_data(builder: flatbuffers.Builder, binary_data: Optional[BinaryDataDC]) -> Optional[int]:
    if binary_data is None:
        return None
    data_vector = builder.CreateByteVector(binary_data.data)

    BinaryData.Start(builder)
    BinaryData.AddData(builder, data_vector)
    return BinaryData.End(builder)


# Serialize MeshInfo dataclass to FlatBuffer
def serialize_mesh_info(builder: flatbuffers.Builder, mesh_info: Optional[MeshInfoDC]) -> Optional[int]:
    if mesh_info is None:
        return None
    object_name = builder.CreateString(mesh_info.object_name)

    MeshInfo.Start(builder)
    MeshInfo.AddObjectName(builder, object_name)
    MeshInfo.AddFaceIndex(builder, mesh_info.face_index)
    return MeshInfo.End(builder)


# Serialize the entire Message dataclass to FlatBuffer
def serialize_message(message: MessageDC, builder: flatbuffers.Builder=None) -> bytes:
    if builder is None:
        # Initialize the FlatBuffer builder
        builder = flatbuffers.Builder(1024)
    # Create the strings for target_group and client_type
    target_group_str = builder.CreateString(message.target_group)
    client_type_str = builder.CreateString(message.client_type)

    # Serialize optional objects (FileObject, BinaryData, MeshInfo)
    file_object = serialize_file_object(builder, message.file_object)
    binary_data = serialize_binary_data(builder, message.binary_data)
    mesh_info = serialize_mesh_info(builder, message.mesh_info)

    # Start building the Message flatbuffer
    Message.Start(builder)

    # Add the required fields
    Message.AddInstanceId(builder, message.instance_id)
    Message.AddCommandType(builder, message.command_type.value)
    if message.target_id is not None:
        Message.AddTargetId(builder, message.target_id)
    Message.AddTargetGroup(builder, target_group_str)
    Message.AddClientType(builder, client_type_str)
    if message.scene_operation is not None:
        Message.AddSceneOperation(builder, message.scene_operation.value)

    # Add the optional fields if they exist
    if file_object is not None:
        Message.AddFileObject(builder, file_object)

    if binary_data is not None:
        Message.AddBinaryData(builder, binary_data)

    if mesh_info is not None:
        Message.AddMeshInfo(builder, mesh_info)

    # Finish the message
    message_flatbuffer = Message.End(builder)
    builder.Finish(message_flatbuffer)

    # Return the binary data (flatbuffer)
    return bytes(builder.Output())
