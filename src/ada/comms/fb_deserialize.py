from ada.comms.fb_model_gen import MessageDC, FileObjectDC, BinaryDataDC, MeshInfoDC, FileTypeDC, FilePurposeDC, \
    CommandTypeDC, SceneOperationsDC
from ada.comms.wsock import Message


def get_instance_id(buf: bytes) -> int:
    return Message.Message.GetRootAsMessage(buf).InstanceId()


def deserialize_message(buf: bytes) -> MessageDC:
    message_fb = Message.Message.GetRootAsMessage(buf)

    # Deserialize the FileObject, if it exists
    file_object = None
    if message_fb.FileObject():
        file_object = FileObjectDC(
            file_type=FileTypeDC(message_fb.FileObject().FileType()),
            purpose=FilePurposeDC(message_fb.FileObject().Purpose()),
            filepath=message_fb.FileObject().Filepath().decode('utf-8')
        )

    # Deserialize the BinaryData, if it exists
    binary_data = None
    if message_fb.BinaryData():
        binary_data = BinaryDataDC(
            data=message_fb.BinaryData()
        )

    # Deserialize the MeshInfo, if it exists
    mesh_info = None
    if message_fb.MeshInfo():
        mesh_info = MeshInfoDC(
            object_name=message_fb.MeshInfo().ObjectName().decode('utf-8'),
            face_index=message_fb.MeshInfo().FaceIndex()
        )

    # Deserialize the Message object
    return MessageDC(
        instance_id=message_fb.InstanceId(),
        command_type=CommandTypeDC(message_fb.CommandType()),
        file_object=file_object,
        binary_data=binary_data,
        mesh_info=mesh_info,
        target_id=message_fb.TargetId(),
        target_group=message_fb.TargetGroup().decode('utf-8'),
        client_type=message_fb.ClientType().decode('utf-8'),
        scene_operation=SceneOperationsDC(message_fb.SceneOperation())
    )
