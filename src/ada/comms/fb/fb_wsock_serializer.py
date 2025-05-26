from typing import Optional

import flatbuffers
from ada.comms.fb.fb_commands_serializer import serialize_webclient
from ada.comms.fb.fb_meshes_serializer import serialize_appendmesh, serialize_meshinfo
from ada.comms.fb.fb_procedures_serializer import serialize_procedurestore
from ada.comms.fb.fb_scene_serializer import serialize_scene, serialize_screenshot
from ada.comms.fb.fb_server_serializer import serialize_server, serialize_serverreply
from ada.comms.fb.fb_wsock_gen import MessageDC
from ada.comms.fb.wsock import Message


def serialize_message(builder: flatbuffers.Builder, obj: Optional[MessageDC]) -> Optional[int]:
    if obj is None:
        return None
    scene_obj = None
    if obj.scene is not None:
        scene_obj = serialize_scene(builder, obj.scene)
    server_obj = None
    if obj.server is not None:
        server_obj = serialize_server(builder, obj.server)
    mesh_info_obj = None
    if obj.mesh_info is not None:
        mesh_info_obj = serialize_meshinfo(builder, obj.mesh_info)
    web_clients_vector = None
    if obj.web_clients is not None and len(obj.web_clients) > 0:
        web_clients_list = [serialize_webclient(builder, item) for item in obj.web_clients]
        Message.StartWebClientsVector(builder, len(web_clients_list))
        for item in reversed(web_clients_list):
            builder.PrependUOffsetTRelative(item)
        web_clients_vector = builder.EndVector()
    procedure_store_obj = None
    if obj.procedure_store is not None:
        procedure_store_obj = serialize_procedurestore(builder, obj.procedure_store)
    server_reply_obj = None
    if obj.server_reply is not None:
        server_reply_obj = serialize_serverreply(builder, obj.server_reply)
    screenshot_obj = None
    if obj.screenshot is not None:
        screenshot_obj = serialize_screenshot(builder, obj.screenshot)
    package_obj = None
    if obj.package is not None:
        package_obj = serialize_appendmesh(builder, obj.package)

    Message.Start(builder)
    if obj.instance_id is not None:
        Message.AddInstanceId(builder, obj.instance_id)
    if obj.scene is not None:
        Message.AddScene(builder, scene_obj)
    if obj.server is not None:
        Message.AddServer(builder, server_obj)
    if obj.mesh_info is not None:
        Message.AddMeshInfo(builder, mesh_info_obj)
    if obj.target_id is not None:
        Message.AddTargetId(builder, obj.target_id)
    if obj.web_clients is not None and len(obj.web_clients) > 0:
        Message.AddWebClients(builder, web_clients_vector)
    if obj.procedure_store is not None:
        Message.AddProcedureStore(builder, procedure_store_obj)
    if obj.server_reply is not None:
        Message.AddServerReply(builder, server_reply_obj)
    if obj.screenshot is not None:
        Message.AddScreenshot(builder, screenshot_obj)
    if obj.package is not None:
        Message.AddPackage(builder, package_obj)
    return Message.End(builder)


def serialize_root_message(message: MessageDC, builder: flatbuffers.Builder = None) -> bytes:
    if builder is None:
        builder = flatbuffers.Builder(1024)
    scene_obj = None
    if message.scene is not None:
        scene_obj = serialize_scene(builder, message.scene)
    server_obj = None
    if message.server is not None:
        server_obj = serialize_server(builder, message.server)
    mesh_info_obj = None
    if message.mesh_info is not None:
        mesh_info_obj = serialize_meshinfo(builder, message.mesh_info)
    procedure_store_obj = None
    if message.procedure_store is not None:
        procedure_store_obj = serialize_procedurestore(builder, message.procedure_store)
    server_reply_obj = None
    if message.server_reply is not None:
        server_reply_obj = serialize_serverreply(builder, message.server_reply)
    screenshot_obj = None
    if message.screenshot is not None:
        screenshot_obj = serialize_screenshot(builder, message.screenshot)
    package_obj = None
    if message.package is not None:
        package_obj = serialize_appendmesh(builder, message.package)

    Message.Start(builder)
    if message.instance_id is not None:
        Message.AddInstanceId(builder, message.instance_id)
    if message.command_type is not None:
        Message.AddCommandType(builder, message.command_type.value)
    if message.scene is not None:
        Message.AddScene(builder, scene_obj)
    if message.server is not None:
        Message.AddServer(builder, server_obj)
    if message.mesh_info is not None:
        Message.AddMeshInfo(builder, mesh_info_obj)
    if message.target_group is not None:
        Message.AddTargetGroup(builder, message.target_group.value)
    if message.client_type is not None:
        Message.AddClientType(builder, message.client_type.value)
    if message.target_id is not None:
        Message.AddTargetId(builder, message.target_id)
    if message.web_clients is not None:
        webclient_list = [serialize_webclient(builder, item) for item in message.web_clients]
        Message.AddWebClients(builder, builder.CreateByteVector(webclient_list))
    if message.procedure_store is not None:
        Message.AddProcedureStore(builder, procedure_store_obj)
    if message.server_reply is not None:
        Message.AddServerReply(builder, server_reply_obj)
    if message.screenshot is not None:
        Message.AddScreenshot(builder, screenshot_obj)
    if message.package is not None:
        Message.AddPackage(builder, package_obj)

    message_flatbuffer = Message.End(builder)
    builder.Finish(message_flatbuffer)
    return bytes(builder.Output())
