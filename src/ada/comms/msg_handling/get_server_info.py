from __future__ import annotations

import asyncio
import os
import threading
from typing import TYPE_CHECKING

from ada.comms.fb.fb_commands_gen import CommandTypeDC
from ada.comms.fb.fb_server_gen import ServerProcessInfoDC, ServerReplyDC
from ada.comms.fb.fb_wsock_gen import MessageDC
from ada.comms.fb.fb_wsock_serializer import serialize_root_message
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def get_server_info_func(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    """Handles a request to get server process information."""
    pid = os.getpid()
    thread_id = threading.get_ident()

    # Get log file path if available
    log_file_path = None
    for handler in logger.handlers:
        if hasattr(handler, "baseFilename"):
            log_file_path = handler.baseFilename
            break

    process_info = ServerProcessInfoDC(
        pid=pid,
        thread_id=thread_id,
        log_file_path=log_file_path,
    )

    server_reply = ServerReplyDC(
        message="Server info retrieved successfully",
        reply_to=CommandTypeDC.GET_SERVER_INFO,
        process_info=process_info,
    )

    reply_message = MessageDC(
        instance_id=message.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        server_reply=server_reply,
    )

    # Serialize and send the reply message
    serialized_reply = serialize_root_message(reply_message)
    asyncio.run(client.websocket.send(serialized_reply))
