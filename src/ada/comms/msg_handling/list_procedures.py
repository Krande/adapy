from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ada.comms.fb_model_gen import CommandTypeDC, MessageDC
from ada.comms.fb_serializer import serialize_message
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def list_procedures(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to list procedures")
    logger.info(f"Message: {message}")

    procedure_store_dc = server.procedure_store.to_procedure_dc()

    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        procedure_store=procedure_store_dc,
        target_id=client.instance_id,
        target_group=client.group_type,
    )
    fb_message = serialize_message(reply_message)

    # run the client.websocket in an event loop
    asyncio.run(client.websocket.send(fb_message))