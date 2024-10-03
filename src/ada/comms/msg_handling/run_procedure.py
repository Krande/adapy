from __future__ import annotations

import asyncio
import pathlib
import random
from typing import TYPE_CHECKING

from ada.comms.fb_model_gen import (
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
from ada.comms.fb_serializer import serialize_message
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
    params = {p.name: p for p in start_procedure.parameters}

    if "output_file" not in params.keys():
        # add output_file if not exist
        output_dir = procedure.get_output_dir()
        if procedure.export_file_type == FileTypeDC.IFC:
            suffix = ".ifc"
        elif procedure.export_file_type == FileTypeDC.GLB:
            suffix = ".glb"
        else:
            raise NotImplementedError(f"Export file type {procedure.export_file_type} not implemented")

        # output_dir.mkdir(parents=True, exist_ok=True)
        params["output_file"] = ParameterDC(
            name="output_file",
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

    output_file = pathlib.Path(parameters.get("output_file").value.string_value)
    new_file_object = FileObjectDC(
        name=output_file.stem,
        filepath=output_file,
        file_type=procedure.export_file_type,
        purpose=purpose,
        is_procedure_output=True,
        procedure_parent=message.procedure_store.start_procedure,
    )

    update_server(server, client, new_file_object)

    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.SERVER_REPLY,
        server=ServerDC(all_file_objects=server.scene.file_objects),
        target_id=client.instance_id,
        target_group=client.group_type,
        server_reply=ServerReplyDC(file_object=new_file_object, reply_to=message.command_type),
    )

    fb_message = serialize_message(reply_message)

    asyncio.run(client.websocket.send(fb_message))

    view_file_object(server, client, new_file_object.name)

    logger.info(f"Completed Procedure '{procedure.name}' and added the File Object '{output_file}' to the server")
