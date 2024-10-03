from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from ada.comms.fb_model_gen import FileObjectDC, MessageDC, ParameterDC, ParameterTypeDC, ProcedureStartDC
from ada.comms.msg_handling.update_server import update_server
from ada.comms.procedures import Procedure
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def run_procedure(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to run procedure")
    start_procedure = message.procedure_store.start_procedure

    procedure: Procedure = server.procedure_store.get(start_procedure.procedure_name)
    params = {p.name: p for p in start_procedure.parameters}
    procedure(**params)

    logger.info(f"Procedure {procedure.name} ran successfully")

    update_server_on_successful_procedure_run(server, procedure, client, message, start_procedure)


def update_server_on_successful_procedure_run(
    server: WebSocketAsyncServer, procedure: Procedure, client: ConnectedClient, message: MessageDC, start_procedure: ProcedureStartDC
) -> None:
    params = [p for p in start_procedure.parameters if p.name == procedure.input_file_var]
    if len(params) == 0:
        # it's a component procedure
        input_file_path = None
        output_dir = procedure.get_component_output_dir()
        if procedure.export_file_type
        output_file = output_dir /
    else:
        # it's a modification procedure on an existing file
        param = params[0]
        if param.type == ParameterTypeDC.STRING:
            input_file_path = pathlib.Path(param.value.string_value)
        elif param.type == ParameterTypeDC.UNKNOWN and param.value.string_value:
            input_file_path = pathlib.Path(param.value.string_value)
        else:
            raise NotImplementedError("Only string input file paths are supported for now")

        server_file_object = server.scene.get_file_object(input_file_path.stem)
        output_file = procedure.get_procedure_output(input_file_path.stem)
        purpose = server_file_object.purpose

    new_file_object = FileObjectDC(
        name=output_file.name,
        filepath=output_file,
        file_type=procedure.export_file_type,
        purpose=purpose,
        is_procedure_output=True,
        procedure_parent=message.procedure_store.start_procedure,
    )
    update_server(server, client, new_file_object)
    logger.info(f"Completed Procedure '{procedure.name}' and added the File Object '{output_file}' to the server")
