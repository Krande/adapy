from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import CommandTypeDC, ErrorDC, MessageDC, ServerReplyDC
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.reply_to import reply_to

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def on_error_reply(
    server: WebSocketAsyncServer,
    client: ConnectedClient,
    error_message: str = None,
    request_message: MessageDC | None = None,
) -> None:
    """Send an ERROR reply. ``request_message`` is the command that failed, if it
    parsed at all; its ``request_id`` is echoed so the client's pending request
    settles instead of timing out."""
    reply_message = reply_to(
        request_message,
        instance_id=server.instance_id,
        command_type=CommandTypeDC.ERROR,
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(error=ErrorDC(message=str(error_message))),
    )
    fb_message = serialize_root_message(reply_message)
    server.send_message_threadsafe(client, fb_message)
