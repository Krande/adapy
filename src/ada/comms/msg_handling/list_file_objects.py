from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ServerDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def list_file_objects(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client.instance_id} to list file objects")

    file_objects = server.scene.file_objects

    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        server=ServerDC(all_file_objects=file_objects),
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(reply_to=message.command_type),
    )
    fb_message = serialize_root_message(reply_message)

    server.send_message_threadsafe(client, fb_message)
