import flatbuffers

from ada.comms.fb_deserialize import deserialize_message
from ada.comms.fb_model import CommandTypeDC, FilePurposeDC, FileTypeDC, SceneOperationsDC, MessageDC, FileObjectDC, \
    MeshInfoDC
from ada.comms.fb_serializer import serialize_message


def test_basic_flat_buffers():
    # Create a sample dataclass message object
    message = MessageDC(
        instance_id=1234,
        command_type=CommandTypeDC.SEND_FILE,
        file_object=FileObjectDC(
            file_type=FileTypeDC.GLB,
            purpose=FilePurposeDC.DESIGN,
            filepath="/path/to/file.glb"
        ),
        binary_data=None,
        mesh_info=MeshInfoDC(
            object_name="MyMeshObject",
            face_index=10
        ),
        target_id=5678,
        target_group="web",
        client_type="local",
        scene_operation=SceneOperationsDC.ADD
    )

    # Initialize the FlatBuffer builder
    builder = flatbuffers.Builder(1024)

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_message(builder, message)

    # You can now send `flatbuffer_data` over a network, save it to a file, etc.
    print(flatbuffer_data)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_message(flatbuffer_data)
    print(deserialized_message)
    assert deserialized_message == message
