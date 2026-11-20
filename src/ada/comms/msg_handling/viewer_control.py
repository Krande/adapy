"""Viewer control through the desktop websocket server (``ada.comms.viewer_wire``).

The server RELAYS it: a local client (a script) sends ``VIEWER_COMMAND`` to a viewer (``target_group``
WEB, ``target_id`` its instance id, from ``LIST_WEB_CLIENTS``), and the viewer answers with
``VIEWER_ANSWER`` to that client (``target_group`` LOCAL, ``target_id`` the sender's instance id). The
server forwards both before this handler sees them, so for those there is nothing left to do here --
except refuse a command addressed to the server itself, which has no viewer to run it.

``VIEWER_STATE`` a viewer sends to the server is kept per viewer (``server.viewer_states``), so a
local client can read what each viewer last said it shows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.msg_handling.on_error_reply import on_error_reply
from ada.comms.viewer_wire import payload_of
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def viewer_states(server: WebSocketAsyncServer) -> dict[int, dict]:
    """The state each viewer last reported, by its instance id."""
    states = getattr(server, "viewer_states", None)
    if states is None:
        states = {}
        server.viewer_states = states
    return states


def handle_viewer_message(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    if message.command_type == CommandTypeDC.VIEWER_STATE:
        state = payload_of(message)
        if isinstance(state, dict) and client.instance_id is not None:
            viewer_states(server)[client.instance_id] = state
        return
    if message.command_type == CommandTypeDC.VIEWER_COMMAND and message.target_group == TargetTypeDC.SERVER:
        on_error_reply(
            server,
            client,
            error_message="VIEWER_COMMAND goes to a viewer: target_group WEB, target_id its instance id",
            request_message=message,
        )
        return
    # Relayed already (server.forward_message): a command on its way to a viewer, or an answer on its
    # way back to the client that sent it.
    logger.debug(f"viewer control: relayed {message.command_type.name} from {client.instance_id}")
