from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import MessageDC

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def delete_file_object(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    file_obj = message.server.delete_file_object
    server.scene.delete_file_object(file_obj.name)
