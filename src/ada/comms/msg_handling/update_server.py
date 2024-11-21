from __future__ import annotations

import pathlib
import platform
import shutil
import subprocess
from typing import TYPE_CHECKING

import ada
from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
from ada.cadit.ifc.sql_model import IfcSqlModel
from ada.comms.fb_model_gen import FileObjectDC, FileTypeDC
from ada.config import Config, logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def update_server(server: WebSocketAsyncServer, client: ConnectedClient, add_file: FileObjectDC) -> None:
    logger.info(f"Received message from {client} to update server")
    if add_file.file_type == FileTypeDC.IFC and add_file.filepath:
        tmp_ifc_fp = pathlib.Path(add_file.filepath)
        if not tmp_ifc_fp.exists():
            raise FileNotFoundError(f"File not found: {tmp_ifc_fp}")

        if add_file.glb_file is None:
            tmp_glb_fp = tmp_ifc_fp.with_suffix(".glb")
            if not tmp_glb_fp.exists():
                ifc_convert_exe = None

                if Config().procedures_use_ifc_convert:
                    ifc_convert_exe = shutil.which("ifcconvert")
                    if platform.platform().startswith("Windows"):
                        ifc_convert_exe = shutil.which("ifcconvert.exe")

                if ifc_convert_exe:
                    subprocess.run([ifc_convert_exe, tmp_ifc_fp.as_posix(), tmp_glb_fp.as_posix()])
                else:
                    a = ada.from_ifc(add_file.filepath)
                    a.to_gltf(tmp_glb_fp)

            add_file.glb_file = FileObjectDC(
                name=add_file.name, filepath=tmp_glb_fp, file_type=FileTypeDC.GLB, purpose=add_file.purpose
            )

        if add_file.ifcsqlite_file is None:
            tmp_sql_fp = tmp_ifc_fp.with_suffix(".sqlite")
            Ifc2SqlPatcher(tmp_ifc_fp, logger, dest_sql_file=tmp_sql_fp).patch()
            add_file.ifcsqlite_file = FileObjectDC(
                name=add_file.name, filepath=tmp_sql_fp, file_type=FileTypeDC.SQLITE, purpose=add_file.purpose
            )

        if server.scene.ifc_sql_store is not None:
            server.scene.ifc_sql_store.db.close()
            server.scene.ifc_sql_store = None

        server.scene.ifc_sql_store = IfcSqlModel(add_file.ifcsqlite_file.filepath)
        server.scene.add_file_object(add_file)
