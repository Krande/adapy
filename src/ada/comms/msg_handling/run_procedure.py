from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from ada.comms.fb_model_gen import FileObjectDC, MessageDC
from ada.comms.msg_handling.update_server import update_server
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
        if param.type == "string":
            params[param.name].value = param.value.string_value
        elif param.type == "float":
            params[param.name].value = param.value.float_value
        elif param.type == "integer":
            params[param.name].value = param.value.integer_value
        elif param.type == "boolean":
            params[param.name].value = param.value.boolean_value
        elif param.type == "array":
            params[param.name].value = param.value.array_value
        else:
            if param.value.string_value:
                params[param.name].value = param.value.string_value
            else:
                raise ValueError(f"Unknown parameter type {param.type}")


    procedure(**procedure.params)
    logger.info(f"Procedure {procedure.name} ran successfully")
    update_server_on_successful_procedure_run(server, procedure, client, message)


def update_server_on_successful_procedure_run(
    server: WebSocketAsyncServer, procedure: Procedure, client: ConnectedClient, message: MessageDC
) -> None:
    param = procedure.params.get(procedure.input_file_var)
    if isinstance(param.value, str):
        input_file_path = pathlib.Path(param.value)
    else:
        input_file_path = pathlib.Path(param.value.string_value)

    server_file_object = server.scene.get_file_object(input_file_path.stem)
    output_file = procedure.get_procedure_output(input_file_path.stem)

    new_file_object = FileObjectDC(
        name=output_file.name,
        filepath=output_file,
        file_type=procedure.export_file_type,
        purpose=server_file_object.purpose,
        is_procedure_output=True,
        procedure_parent=message.procedure_store.start_procedure,
    )
    update_server(server, client, new_file_object)
    logger.info(f"Completed Procedure '{procedure.name}' and added the File Object '{output_file}' to the server")
