from __future__ import annotations

import io
from typing import TYPE_CHECKING

import trimesh

from ada.comms.exceptions import ServerError
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    FileObjectDC,
    FileTypeDC,
    MessageDC,
    SceneDC,
    ServerReplyDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.msg_handling.reply_to import reply_to
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock.server import ConnectedClient, WebSocketAsyncServer


def view_file_object(
    server: WebSocketAsyncServer,
    client: ConnectedClient,
    file_object_name: str,
    request_message: MessageDC | None = None,
) -> None:
    """Send the GLB for ``file_object_name`` to ``client``.

    ``request_message`` is the VIEW_FILE_OBJECT command being answered, so the
    reply can echo its ``request_id``. It is ``None`` when the view is pushed as
    a side effect of something else (e.g. a finished procedure)."""
    logger.info(f"Received message from {client.instance_id} to get file object")
    result = server.scene.get_file_object(file_object_name)
    if result is None:
        raise ServerError(f"File object {file_object_name} not found")

    if result.file_type != FileTypeDC.GLB:
        glb_file_obj = result.glb_file
    else:
        glb_file_obj = result

    scene = trimesh.load(glb_file_obj.filepath)
    with io.BytesIO() as data:
        scene.export(
            file_obj=data,
            file_type="glb",
        )
        glb_file_object = FileObjectDC(
            name=glb_file_obj.name, file_type=FileTypeDC.GLB, purpose=glb_file_obj.purpose, filedata=data.getvalue()
        )

        msg = reply_to(
            request_message,
            instance_id=server.instance_id,
            command_type=CommandTypeDC.SERVER_REPLY,
            server_reply=ServerReplyDC(reply_to=CommandTypeDC.VIEW_FILE_OBJECT, file_objects=[glb_file_object]),
            scene=SceneDC(current_file=glb_file_object),
            target_id=client.instance_id,
            target_group=client.group_type,
        )

        fb_message = serialize_root_message(msg)
        server.send_message_threadsafe(client, fb_message)

    server.scene.mesh_meta = scene.metadata
