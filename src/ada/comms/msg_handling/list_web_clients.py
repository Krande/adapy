from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb.fb_commands_gen import CommandTypeDC, TargetTypeDC, WebClientDC
from ada.comms.fb.fb_wsock_gen import MessageDC
from ada.comms.fb.fb_wsock_serializer import serialize_root_message
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def list_web_clients_func(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    """Handles a request to list connected web clients."""
    web_clients = [
        WebClientDC(
            instance_id=cl.instance_id,
            port=cl.port,
            last_heartbeat=cl.last_heartbeat,
            name=f"WebClient_{cl.instance_id}",
            address="Address_Not_Implemented",
        )
        for cl in server.connected_web_clients
        if cl.group_type == TargetTypeDC.WEB
    ]

    reply_message = MessageDC(
        instance_id=message.instance_id,
        command_type=CommandTypeDC.LIST_WEB_CLIENTS,
        target_group=TargetTypeDC.WEB,
        web_clients=web_clients,
    )

    logger.debug(f"Len of web clients: {len(web_clients)}")
    # Serialize and send the reply message
    serialized_reply = serialize_root_message(reply_message)
    server.send_message_threadsafe(client, serialized_reply)
