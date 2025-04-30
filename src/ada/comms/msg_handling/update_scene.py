from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import trimesh

from ada.comms.fb_wrap_model_gen import FileObjectDC
from ada.config import Config, logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def setup_backend_scene(glb_file_data: FileObjectDC, server: WebSocketAsyncServer) -> pathlib.Path:
    import gzip

    from ada.visit.rendering.render_backend import is_gzip_file

    tmp_dir = (
        pathlib.Path("temp") if Config().websockets_server_temp_dir is None else Config().websockets_server_temp_dir
    )
    local_glb_file = tmp_dir / f"{glb_file_data.name}.glb"
    local_glb_file.parent.mkdir(parents=True, exist_ok=True)

    with open(local_glb_file, "wb") as f:
        f.write(glb_file_data.filedata)

    if is_gzip_file(local_glb_file):
        with gzip.open(local_glb_file, "rb") as f:
            scene = trimesh.load(f, file_type="glb")
    else:
        with open(local_glb_file, "rb") as f:
            scene = trimesh.load(f, file_type="glb")

    server.scene.mesh_meta = scene.metadata
    return local_glb_file


def update_scene(server: WebSocketAsyncServer, client: ConnectedClient, glb_file_data: FileObjectDC) -> None:
    logger.info(f"Received message from {client} to update scene")

    local_glb_file = setup_backend_scene(glb_file_data, server)

    file_object = FileObjectDC(
        name=glb_file_data.name,
        filedata=glb_file_data.filedata,
        filepath=local_glb_file.as_posix(),
        file_type=glb_file_data.file_type,
        purpose=glb_file_data.purpose,
        compressed=glb_file_data.compressed,
    )

    server.scene.add_file_object(file_object)
