from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ada.comms.fb_model_gen import CommandTypeDC, ErrorDC, MessageDC, ServerReplyDC
from ada.comms.fb_serializer import serialize_message

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def on_error_reply(server: WebSocketAsyncServer, client: ConnectedClient, error_message: str = None) -> None:
    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.ERROR,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(error=ErrorDC(message=str(error_message))),
    )
    fb_message = serialize_message(reply_message)
    # run the client.websocket in an event loop
    asyncio.run(client.websocket.send(fb_message))
