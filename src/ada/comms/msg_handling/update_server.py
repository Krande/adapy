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
    if message.file_object.file_type == FileTypeDC.IFC and message.file_object.filepath:
        tmp_ifc_fp = pathlib.Path(message.file_object.filepath)
        tmp_sql_fp = tmp_ifc_fp.with_suffix(".sqlite")
        Ifc2SqlPatcher(tmp_ifc_fp, logger, dest_sql_file=tmp_sql_fp).patch()
        server.scene_meta.ifc_sql_store = IfcSqlModel(tmp_sql_fp)
