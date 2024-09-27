from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import trimesh

from ada.comms.fb_model_gen import FileObjectDC, MessageDC
from ada.config import Config, logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def update_scene(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to update scene")
    glb_file_data = message.file_object.filedata
    tmp_dir = (
        pathlib.Path("temp") if Config().websockets_server_temp_dir is None else Config().websockets_server_temp_dir
    )
    local_glb_file = tmp_dir / f"{message.file_object.name}.glb"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with open(local_glb_file, "wb") as f:
        f.write(glb_file_data)

    tri_scene = trimesh.load(local_glb_file)
    server.scene_meta.mesh_meta = tri_scene.metadata

    file_object = FileObjectDC(
        name=message.file_object.name,
        filedata=glb_file_data,
        filepath=local_glb_file,
        file_type=message.file_object.file_type,
        purpose=message.file_object.purpose,
    )
    server.scene_meta.file_objects.append(file_object)
