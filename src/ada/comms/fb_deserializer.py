import flatbuffers
from typing import List

from ada.comms.wsock import WebClient, FileObject, MeshInfo, Message,CommandType, SceneOperations, FilePurpose, FileType

from ada.comms.fb_model_gen import WebClientDC, FileObjectDC, MeshInfoDC, MessageDC,CommandTypeDC, SceneOperationsDC, FilePurposeDC, FileTypeDC

def deserialize_webclient(fb_obj) -> WebClientDC | None:
    if fb_obj is None:
        return None

    return WebClientDC(
        instance_id=fb_obj.InstanceId(),
        name=fb_obj.Name().decode('utf-8') if fb_obj.Name() is not None else None,
        address=fb_obj.Address().decode('utf-8') if fb_obj.Address() is not None else None,
        port=fb_obj.Port()
    )


def deserialize_fileobject(fb_obj) -> FileObjectDC | None:
    if fb_obj is None:
        return None

    return FileObjectDC(
        file_type=FileTypeDC(fb_obj.FileType()),
        purpose=FilePurposeDC(fb_obj.Purpose()),
        filepath=fb_obj.Filepath().decode('utf-8') if fb_obj.Filepath() is not None else None,
        filedata=bytes(fb_obj.FiledataAsNumpy()) if fb_obj.FiledataLength() > 0 else None
    )


def deserialize_meshinfo(fb_obj) -> MeshInfoDC | None:
    if fb_obj is None:
        return None

    return MeshInfoDC(
        object_name=fb_obj.ObjectName().decode('utf-8') if fb_obj.ObjectName() is not None else None,
        face_index=fb_obj.FaceIndex(),
        json_data=fb_obj.JsonData().decode('utf-8') if fb_obj.JsonData() is not None else None
    )


def deserialize_message(fb_obj) -> MessageDC | None:
    if fb_obj is None:
        return None

    return MessageDC(
        instance_id=fb_obj.InstanceId(),
        command_type=CommandTypeDC(fb_obj.CommandType()),
        file_object=deserialize_fileobject(fb_obj.FileObject()),
        mesh_info=deserialize_meshinfo(fb_obj.MeshInfo()),
        target_group=fb_obj.TargetGroup().decode('utf-8') if fb_obj.TargetGroup() is not None else None,
        client_type=fb_obj.ClientType().decode('utf-8') if fb_obj.ClientType() is not None else None,
        scene_operation=SceneOperationsDC(fb_obj.SceneOperation()),
        target_id=fb_obj.TargetId(),
        web_clients=[deserialize_webclient(fb_obj.WebClients(i)) for i in range(fb_obj.WebClientsLength())] if fb_obj.WebClientsLength() > 0 else None
    )


def deserialize_root_message(bytes_obj: bytes) -> MessageDC:
    fb_obj = Message.Message.GetRootAsMessage(bytes_obj, 0)
    return deserialize_message(fb_obj)
