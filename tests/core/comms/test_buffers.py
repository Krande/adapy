from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import (
    CommandTypeDC,
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MeshInfoDC,
    MessageDC,
    SceneOperationDC,
    SceneOperationsDC,
    TargetTypeDC,
)
from ada.comms.fb_serializer import serialize_message


def test_basic_flat_buffers():
    # Create a sample dataclass message object
    message = MessageDC(
        instance_id=1234,
        command_type=CommandTypeDC.UPDATE_SERVER,
        file_object=FileObjectDC(file_type=FileTypeDC.GLB, purpose=FilePurposeDC.DESIGN, filepath="/path/to/file.glb"),
        mesh_info=MeshInfoDC(object_name="MyMeshObject", face_index=10),
        target_id=5678,
        target_group=TargetTypeDC.WEB,
        client_type=TargetTypeDC.LOCAL,
        scene_operation=SceneOperationDC(operation=SceneOperationsDC.REPLACE),
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_message(message)

    # You can now send `flatbuffer_data` over a network, save it to a file, etc.
    print(flatbuffer_data)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_root_message(flatbuffer_data)
    print(deserialized_message)
    assert deserialized_message == message


def test_basic_flat_buffers_2():
    # Create a sample dataclass message object
    message = MessageDC(
        instance_id=1234,
        command_type=CommandTypeDC.UPDATE_SERVER,
        file_object=FileObjectDC(file_type=FileTypeDC.GLB, purpose=FilePurposeDC.DESIGN, filepath="/path/to/file.glb"),
        mesh_info=MeshInfoDC(object_name="MyMeshObject", face_index=10),
        target_id=5678,
        target_group=TargetTypeDC.WEB,
        client_type=TargetTypeDC.LOCAL,
        scene_operation=SceneOperationDC(operation=SceneOperationsDC.ADD),
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_message(message)

    # You can now send `flatbuffer_data` over a network, save it to a file, etc.
    print(flatbuffer_data)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_root_message(flatbuffer_data)
    print(deserialized_message)
    assert deserialized_message == message
