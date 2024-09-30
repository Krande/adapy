from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
from ada.cadit.ifc.sql_model import IfcSqlModel
from ada.comms.fb_model_gen import FileTypeDC, MessageDC
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def update_server(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to update server")
    logger.info(f"Message: {message}")
    add_file = message.server.add_file_object
    if add_file.file_type == FileTypeDC.IFC and add_file.filepath:
        tmp_ifc_fp = pathlib.Path(add_file.filepath)
        if not tmp_ifc_fp.exists():
            raise FileNotFoundError(f"File not found: {tmp_ifc_fp}")
        tmp_sql_fp = tmp_ifc_fp.with_suffix(".sqlite")

        Ifc2SqlPatcher(tmp_ifc_fp, logger, dest_sql_file=tmp_sql_fp).patch()

        server.scene.ifc_sql_store = IfcSqlModel(tmp_sql_fp)
        remove_existing_idx = None
        for i, fo in enumerate(server.scene.file_objects):
            if fo.name == add_file.name:
                remove_existing_idx = i
        if remove_existing_idx is not None:
            server.scene.file_objects.pop(remove_existing_idx)

        server.scene.file_objects.append(add_file)
