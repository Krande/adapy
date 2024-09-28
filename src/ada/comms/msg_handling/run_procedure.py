from __future__ import annotations

from typing import TYPE_CHECKING

from ada.comms.fb_model_gen import MessageDC
from ada.comms.procedures import Procedure
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def run_procedure(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to run procedure")
    start_procedure = message.procedure_store.start_procedure

    procedure: Procedure = server.procedure_store.get(start_procedure.procedure_name)
    params = procedure.params
    for param in start_procedure.parameters:
        params[param.name] = param.value
    procedure(**procedure.params)
