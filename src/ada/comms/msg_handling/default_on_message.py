from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import CommandTypeDC
from ada.comms.msg_handling.delete_file_object import delete_file_object
from ada.comms.msg_handling.list_file_objects import list_file_objects
from ada.comms.msg_handling.list_procedures import list_procedures
from ada.comms.msg_handling.mesh_info_callback import mesh_info_callback
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.msg_handling.run_procedure import run_procedure
from ada.comms.msg_handling.start_local_app import start_local_app
from ada.comms.msg_handling.start_separate_node_editor import start_separate_node_editor
from ada.comms.msg_handling.update_scene import update_scene
from ada.comms.msg_handling.update_server import update_server
from ada.comms.msg_handling.view_file_object import view_file_object
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def default_on_message(server: WebSocketAsyncServer, client: ConnectedClient, message_data: bytes) -> None:
    try:
        message = deserialize_root_message(message_data)
        if message.command_type == CommandTypeDC.UPDATE_SCENE and message.scene.current_file is not None:
            update_scene(server, client, message.scene.current_file)
        elif message.command_type == CommandTypeDC.UPDATE_SERVER:
            update_server(server, client, message.server.new_file_object)
        elif message.command_type == CommandTypeDC.MESH_INFO_CALLBACK:
            mesh_info_callback(server, client, message)
        elif message.command_type == CommandTypeDC.LIST_PROCEDURES:
            list_procedures(server, client, message)
        elif message.command_type == CommandTypeDC.RUN_PROCEDURE:
            run_procedure(server, client, message)
        elif message.command_type == CommandTypeDC.LIST_FILE_OBJECTS:
            list_file_objects(server, client, message)
        elif message.command_type == CommandTypeDC.VIEW_FILE_OBJECT:
            view_file_object(server, client, message.server.get_file_object_by_name)
        elif message.command_type == CommandTypeDC.DELETE_FILE_OBJECT:
            delete_file_object(server, client, message)
        elif message.command_type == CommandTypeDC.START_NEW_NODE_EDITOR:
            start_separate_node_editor(server, client, message)
        elif message.command_type == CommandTypeDC.START_FILE_IN_LOCAL_APP:
            start_local_app(server, client, message)
        else:
            logger.error(f"Unknown command type: {message.command_type}")
            on_error_reply(server, client, error_message=f"Unknown command type: {message.command_type}")

    except Exception as e:
        trace_str = traceback.format_exc()
        logger.error(f"Error handling message: {e}")
        if server.debug:
            logger.error(trace_str)
        on_error_reply(server, client, error_message=str(e))
