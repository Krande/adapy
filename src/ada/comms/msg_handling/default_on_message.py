from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import CommandTypeDC
from ada.comms.msg_handling.list_procedures import list_procedures
from ada.comms.msg_handling.mesh_info_callback import mesh_info_callback
from ada.comms.msg_handling.update_scene import update_scene
from ada.comms.msg_handling.update_server import update_server
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import WebSocketAsyncServer, ConnectedClient


def default_on_message(server: WebSocketAsyncServer, client: ConnectedClient, message_data: bytes) -> None:
    message = deserialize_root_message(message_data)
    if message.command_type == CommandTypeDC.UPDATE_SCENE:
        update_scene(server, client, message)
    elif message.command_type == CommandTypeDC.UPDATE_SERVER:
        update_server(server, client, message)
    elif message.command_type == CommandTypeDC.MESH_INFO_CALLBACK:
        mesh_info_callback(server, client, message)
    elif message.command_type == CommandTypeDC.LIST_PROCEDURES:
        list_procedures(server, client, message)
    else:
        logger.error(f"Unknown command type: {message.command_type}")
