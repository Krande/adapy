from ada.comms.fb.fb_commands_deserializer import deserialize_webclient
from ada.comms.fb.fb_commands_gen import CommandTypeDC, TargetTypeDC
from ada.comms.fb.fb_meshes_deserializer import (
    deserialize_appendmesh,
    deserialize_meshinfo,
)
from ada.comms.fb.fb_procedures_deserializer import deserialize_procedurestore
from ada.comms.fb.fb_scene_deserializer import deserialize_scene, deserialize_screenshot
from ada.comms.fb.fb_server_deserializer import (
    deserialize_server,
    deserialize_serverreply,
)
from ada.comms.fb.fb_wsock_gen import MessageDC
from ada.comms.fb.wsock import Message


def deserialize_message(fb_obj) -> MessageDC | None:
    if fb_obj is None:
        return None

    return MessageDC(
        instance_id=fb_obj.InstanceId(),
        command_type=CommandTypeDC(fb_obj.CommandType()),
        scene=deserialize_scene(fb_obj.Scene()),
        server=deserialize_server(fb_obj.Server()),
        mesh_info=deserialize_meshinfo(fb_obj.MeshInfo()),
        target_group=TargetTypeDC(fb_obj.TargetGroup()),
        client_type=TargetTypeDC(fb_obj.ClientType()),
        target_id=fb_obj.TargetId(),
        web_clients=(
            [deserialize_webclient(fb_obj.WebClients(i)) for i in range(fb_obj.WebClientsLength())]
            if fb_obj.WebClientsLength() > 0
            else None
        ),
        procedure_store=deserialize_procedurestore(fb_obj.ProcedureStore()),
        server_reply=deserialize_serverreply(fb_obj.ServerReply()),
        screenshot=deserialize_screenshot(fb_obj.Screenshot()),
        package=deserialize_appendmesh(fb_obj.Package()),
    )


def deserialize_root_message(bytes_obj: bytes) -> MessageDC:
    fb_obj = Message.Message.GetRootAsMessage(bytes_obj, 0)
    return deserialize_message(fb_obj)
