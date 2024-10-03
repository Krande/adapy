from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import trimesh

from ada.comms.fb_model_gen import FileObjectDC
from ada.config import Config, logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def update_scene(server: WebSocketAsyncServer, client: ConnectedClient, glb_file_data: FileObjectDC) -> None:
    logger.info(f"Received message from {client} to update scene")
    tmp_dir = (
        pathlib.Path("temp") if Config().websockets_server_temp_dir is None else Config().websockets_server_temp_dir
    )
    local_glb_file = tmp_dir / f"{glb_file_data.name}.glb"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with open(local_glb_file, "wb") as f:
        f.write(glb_file_data.filedata)

    tri_scene = trimesh.load(local_glb_file)
    server.scene.mesh_meta = tri_scene.metadata

    file_object = FileObjectDC(
        name=glb_file_data.name,
        filedata=glb_file_data.filedata,
        filepath=local_glb_file,
        file_type=glb_file_data.file_type,
        purpose=glb_file_data.purpose,
    )

    server.scene.add_file_object(file_object)
