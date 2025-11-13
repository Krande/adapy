"""Handler for listing connected web clients."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MessageDC,
    ServerReplyDC,
    TargetTypeDC,
    WebClientDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def list_web_clients(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    """
    List all connected web clients.

    Args:
        server: The WebSocket server instance
        client: The client requesting the list
        message: The message containing the request
    """
    logger.debug(f"Listing connected clients for client {client.instance_id}")

    # Collect information about all connected clients
    web_clients = []
    for connected_client in server.connected_clients:
        if connected_client.group_type == TargetTypeDC.WEB:
            web_clients.append(
                WebClientDC(
                    instance_id=connected_client.instance_id,
                    name=f"WebClient-{connected_client.instance_id}",
                    address=(
                        str(connected_client.websocket.remote_address[0])
                        if connected_client.websocket.remote_address
                        else "unknown"
                    ),
                    port=(
                        connected_client.websocket.remote_address[1] if connected_client.websocket.remote_address else 0
                    ),
                )
            )

    # Send reply with the list of clients
    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        target_id=client.instance_id,
        target_group=client.group_type,
        client_type=TargetTypeDC.SERVER,
        server_reply=ServerReplyDC(
            reply_to=message.command_type,
            web_clients=web_clients,
        ),
    )

    flatbuffer_data = serialize_root_message(reply_message)
    asyncio.run(client.websocket.send(flatbuffer_data))
    logger.debug(f"Sent list of {len(web_clients)} web clients to client {client.instance_id}")
