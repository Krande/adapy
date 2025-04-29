from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    AppendMeshDC,
    CommandTypeDC,
    FileArgDC,
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MeshDC,
    MeshInfoDC,
    MessageDC,
    ParameterDC,
    ParameterTypeDC,
    ProcedureDC,
    ProcedureStateDC,
    ProcedureStoreDC,
    SceneDC,
    SceneOperationsDC,
    TargetTypeDC,
)
from ada.comms.fb_wsock_serializer import serialize_root_message


def test_basic_flat_buffers():
    # Create a sample dataclass message object
    message = MessageDC(
        instance_id=1234,
        command_type=CommandTypeDC.UPDATE_SERVER,
        scene=SceneDC(
            operation=SceneOperationsDC.REPLACE,
            current_file=FileObjectDC(
                file_type=FileTypeDC.GLB,
                purpose=FilePurposeDC.DESIGN,
                filepath="/path/to/file.glb",
                is_procedure_output=False,
                compressed=False,
            ),
        ),
        mesh_info=MeshInfoDC(object_name="MyMeshObject", face_index=10),
        target_id=5678,
        target_group=TargetTypeDC.WEB,
        client_type=TargetTypeDC.LOCAL,
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_root_message(message)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_root_message(flatbuffer_data)
    assert deserialized_message == message


def test_basic_flat_buffers_2():
    # Create a sample dataclass message object
    message = MessageDC(
        instance_id=1234,
        command_type=CommandTypeDC.UPDATE_SERVER,
        scene=SceneDC(
            operation=SceneOperationsDC.ADD,
            current_file=FileObjectDC(
                file_type=FileTypeDC.GLB,
                purpose=FilePurposeDC.DESIGN,
                filepath="/path/to/file.glb",
                is_procedure_output=False,
            ),
        ),
        mesh_info=MeshInfoDC(object_name="MyMeshObject", face_index=10),
        target_id=5678,
        target_group=TargetTypeDC.WEB,
        client_type=TargetTypeDC.LOCAL,
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_root_message(message)

    # You can now send `flatbuffer_data` over a network, save it to a file, etc.
    print(flatbuffer_data)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_root_message(flatbuffer_data)
    print(deserialized_message)
    assert deserialized_message == message


def test_procedure_store():
    # Create a sample dataclass message object
    message = MessageDC(
        instance_id=1234,
        command_type=CommandTypeDC.LIST_PROCEDURES,
        target_group=TargetTypeDC.SERVER,
        target_id=0,
        client_type=TargetTypeDC.LOCAL,
        procedure_store=ProcedureStoreDC(
            procedures=[
                ProcedureDC(
                    name="add_stiffeners",
                    description="Add stiffeners to the structure",
                    parameters=[ParameterDC(name="ifc_file", type=ParameterTypeDC.STRING)],
                    state=ProcedureStateDC.IDLE,
                    file_inputs=[FileArgDC("input_ifc_file", FileTypeDC.IFC)],
                    file_outputs=[FileArgDC("output_ifc_file", FileTypeDC.IFC)],
                    is_component=True,
                )
            ],
        ),
    )

    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_root_message(message)

    # You can now send `flatbuffer_data` over a network, save it to a file, etc.
    print(flatbuffer_data)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_root_message(flatbuffer_data)
    print(deserialized_message)
    assert deserialized_message == message


def test_append_mesh():

    message = MessageDC(package=AppendMeshDC(mesh=MeshDC(vertices=[1.0, 2.0, 3.0], indices=[1, 2, 3])))
    # Serialize the dataclass message into a FlatBuffer
    flatbuffer_data = serialize_root_message(message)

    # Deserialize the FlatBuffer back into a dataclass message
    deserialized_message = deserialize_root_message(flatbuffer_data)
    package = deserialized_message.package

    assert package == message.package
