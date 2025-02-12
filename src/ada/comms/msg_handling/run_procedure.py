from __future__ import annotations

import asyncio
import pathlib
import random
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MessageDC,
    ParameterDC,
    ParameterTypeDC,
    ServerDC,
    ServerReplyDC,
    ValueDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.update_server import update_server
from ada.comms.msg_handling.view_file_object import view_file_object
from ada.config import logger
from ada.procedural_modelling.procedure_model import Procedure

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def run_procedure(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to run procedure")
    start_procedure = message.procedure_store.start_procedure

    procedure: Procedure = server.procedure_store.get(start_procedure.procedure_name)
    if procedure is None:
        server.procedure_store.update_procedures()
        procedure = server.procedure_store.get(start_procedure.procedure_name)
        if procedure is None:
            raise ValueError(f"Procedure {start_procedure.procedure_name} not found")
    if start_procedure.parameters is None:
        params = {}
    else:
        params = {p.name: p for p in start_procedure.parameters}

    for output in procedure.outputs:
        if output.arg_name not in params.keys():
            # add output_file if not exist
            output_dir = procedure.get_output_dir()
            if output.file_type == FileTypeDC.IFC:
                suffix = ".ifc"
            elif output.file_type == FileTypeDC.GLB:
                suffix = ".glb"
            else:
                raise NotImplementedError(f"Export file type {procedure.outputs} not implemented")

            # output_dir.mkdir(parents=True, exist_ok=True)
            params[output.arg_name] = ParameterDC(
                name=output.arg_name,
                type=ParameterTypeDC.STRING,
                value=ValueDC(
                    string_value=(output_dir / f"{procedure.name}-{random.randint(10000,20000)}{suffix}").as_posix()
                ),
            )

    procedure(**params)

    logger.info(f"Procedure {procedure.name} ran successfully")

    update_server_on_successful_procedure_run(server, procedure, client, message, params)


def update_server_on_successful_procedure_run(
    server: WebSocketAsyncServer,
    procedure: Procedure,
    client: ConnectedClient,
    message: MessageDC,
    parameters: dict[str, ParameterDC],
) -> None:

    if procedure.is_component:
        # it's a component procedure
        purpose = FilePurposeDC.DESIGN
    else:
        # it's a modification procedure on an existing file
        input_file = parameters.get("input_file")
        if input_file is None:
            raise ValueError("No input file provided for procedure?")
        input_file_value = input_file.value.string_value
        if input_file_value is None:
            raise NotImplementedError("Only string input file paths are supported for now")

        input_file_path = pathlib.Path(input_file_value)
        server_file_object = server.scene.get_file_object(input_file_path.stem)
        if server_file_object is None:
            raise ValueError(f"Input file {input_file_path.stem} not found on server")

        purpose = server_file_object.purpose
    new_file_objects = []
    output_files = []
    for output in procedure.outputs:
        if output.arg_name not in parameters.keys():
            raise ValueError(f"Output parameter {output.arg_name} not found in parameters")
        output_file = pathlib.Path(parameters.get(output.arg_name).value.string_value)
        output_files.append(output_file)
        new_file_object = FileObjectDC(
            name=output_file.stem,
            filepath=output_file,
            file_type=output.file_type,
            purpose=purpose,
            is_procedure_output=True,
            procedure_parent=message.procedure_store.start_procedure,
        )

        update_server(server, client, new_file_object)
        new_file_objects.append(new_file_object)

    if message.instance_id != client.instance_id:
        # send the new file object to the target client instead of client triggering the procedure
        target_client = server.get_client_by_instance_id(message.instance_id)
        if target_client is None:
            raise ValueError(f"Client with instance id {message.instance_id} not found")
    else:
        target_client = client

    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        server=ServerDC(all_file_objects=server.scene.file_objects),
        target_id=target_client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(file_objects=new_file_objects, reply_to=message.command_type),
    )

    fb_message = serialize_root_message(reply_message)

    asyncio.run(client.websocket.send(fb_message))

    # view the last IFC file object
    for new_file_object in new_file_objects:
        if new_file_object.file_type == FileTypeDC.IFC:
            view_file_object(server, target_client, new_file_object.name)
            break

    logger.info(f"Completed Procedure '{procedure.name}' and added the File Objects '{output_files}' to the server")
