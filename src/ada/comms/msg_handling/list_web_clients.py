from __future__ import annotations

import asyncio

from ada.comms.fb.fb_commands_gen import WebClientDC, TargetTypeDC, CommandTypeDC
from ada.comms.fb.fb_wsock_gen import MessageDC
from ada.comms.fb.fb_wsock_serializer import serialize_root_message
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.comms.wsock.server import WebSocketAsyncServer, ConnectedClient


def list_web_clients_func(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    """Handles a request to list connected web clients."""
    web_clients = [
        WebClientDC(
            instance_id=cl.instance_id,
            group_type=cl.group_type,
            client_name=cl.client_name,
            connected_at=cl.connected_at,
            last_heartbeat=cl.last_heartbeat,
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

    # Serialize and send the reply message
    serialized_reply = serialize_root_message(reply_message)
    asyncio.create_task(client.websocket.send(serialized_reply))
