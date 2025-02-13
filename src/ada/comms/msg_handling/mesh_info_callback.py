from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    MeshInfoDC,
    MessageDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def mesh_info_callback(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info(f"Received message from {client} to update mesh info")
    logger.info(f"Message: {message}")
    if server.scene.ifc_sql_store is None:
        logger.error("IFC SQL store not initialized")
        return
    node_name = message.mesh_info.object_name
    num = node_name.replace("node", "")

    meta = server.scene.mesh_meta.get(f"id_sequence{num}")
    if meta is None:
        logger.error(f"No meta data found for node {node_name}")
        return
    guid = list(meta.keys())
    if len(guid) == 1:
        guid = guid[0]
        entity = server.scene.ifc_sql_store.by_guid(guid)
    elif len(guid) > 1:
        raise ValueError(f"Multiple GUIDs found for node {node_name}")
    else:
        raise ValueError(f"No GUID found for node {node_name}")

    logger.info(f"Entity: {entity}")
    mesh_info = MeshInfoDC(object_name=node_name, face_index=message.mesh_info.face_index, json_data=json.dumps(entity))
    reply_message = MessageDC(
        instance_id=server.instance_id,
        command_type=CommandTypeDC.MESH_INFO_REPLY,
        mesh_info=mesh_info,
        target_id=client.instance_id,
        target_group=TargetTypeDC.WEB,
    )
    fb_message = serialize_root_message(reply_message)
    # run the client.websocket in an event loop
    asyncio.run(client.websocket.send(fb_message))
