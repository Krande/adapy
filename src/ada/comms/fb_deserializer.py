import flatbuffers
from typing import List

from ada.comms.wsock import FileObject, BinaryData, MeshInfo, Message,CommandType, SceneOperations, FilePurpose, FileType

from ada.comms.fb_model_gen import FileObjectDC, BinaryDataDC, MeshInfoDC, MessageDC,CommandTypeDC, SceneOperationsDC, FilePurposeDC, FileTypeDC

def deserialize_fileobject(fb_obj) -> FileObjectDC | None:
    if fb_obj is None:
        return None

    return FileObjectDC(
        file_type=FileTypeDC(fb_obj.FileType()),
        purpose=FilePurposeDC(fb_obj.Purpose()),
        filepath=fb_obj.Filepath().decode('utf-8')
    )


def deserialize_binarydata(fb_obj) -> BinaryDataDC | None:
    if fb_obj is None:
        return None

    return BinaryDataDC(
        data=bytes(fb_obj.Data())
    )


def deserialize_meshinfo(fb_obj) -> MeshInfoDC | None:
    if fb_obj is None:
        return None

    return MeshInfoDC(
        object_name=fb_obj.ObjectName().decode('utf-8'),
        face_index=fb_obj.FaceIndex()
    )


def deserialize_message(fb_obj) -> MessageDC | None:
    if fb_obj is None:
        return None

    return MessageDC(
        instance_id=fb_obj.InstanceId(),
        command_type=CommandTypeDC(fb_obj.CommandType()),
        file_object=deserialize_fileobject(fb_obj.FileObject()),
        binary_data=deserialize_binarydata(fb_obj.BinaryData()),
        mesh_info=deserialize_meshinfo(fb_obj.MeshInfo()),
        target_group=fb_obj.TargetGroup().decode('utf-8'),
        client_type=fb_obj.ClientType().decode('utf-8'),
        scene_operation=SceneOperationsDC(fb_obj.SceneOperation()),
        target_id=fb_obj.TargetId()
    )


def deserialize_root_message(bytes_obj: bytes) -> MessageDC:
    fb_obj = Message.Message.GetRootAsMessage(bytes_obj, 0)
    return deserialize_message(fb_obj)
