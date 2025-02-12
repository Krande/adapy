from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import MessageDC

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def start_local_app(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    os.startfile(message.server.start_file_in_local_app.filepath)
