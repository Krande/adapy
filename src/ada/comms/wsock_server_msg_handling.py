from __future__ import annotations

import asyncio
import json
import pathlib
from typing import TYPE_CHECKING

import trimesh

from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
from ada.cadit.ifc.sql_model import IfcSqlModel
from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import (
    CommandTypeDC,
    FileObjectDC,
    FileTypeDC,
    MeshInfoDC,
    MessageDC,
    TargetTypeDC,
)
from ada.comms.fb_serializer import serialize_message
from ada.config import Config, logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def default_on_message(server: WebSocketAsyncServer, client: ConnectedClient, message_data: bytes) -> None:
    message = deserialize_root_message(message_data)
    if message.command_type == CommandTypeDC.UPDATE_SCENE:
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
    elif message.command_type == CommandTypeDC.UPDATE_SERVER:
        logger.info(f"Received message from {client} to update server")
        logger.info(f"Message: {message}")
        if message.file_object.file_type == FileTypeDC.IFC and message.file_object.filepath:
            tmp_ifc_fp = pathlib.Path(message.file_object.filepath)
            tmp_sql_fp = tmp_ifc_fp.with_suffix(".sqlite")
            Ifc2SqlPatcher(tmp_ifc_fp, logger, dest_sql_file=tmp_sql_fp).patch()
            server.scene_meta.ifc_sql_store = IfcSqlModel(tmp_sql_fp)
    elif message.command_type == CommandTypeDC.MESH_INFO_CALLBACK:
        logger.info(f"Received message from {client} to update mesh info")
        logger.info(f"Message: {message}")
        if server.scene_meta.ifc_sql_store is None:
            logger.error("IFC SQL store not initialized")
            return
        node_name = message.mesh_info.object_name
        num = node_name.replace("node", "")

        meta = server.scene_meta.mesh_meta.get(f"id_sequence{num}")
        guid = list(meta.keys())
        if len(guid) == 1:
            guid = guid[0]
            entity = server.scene_meta.ifc_sql_store.by_guid(guid)
        elif len(guid) > 1:
            raise ValueError(f"Multiple GUIDs found for node {node_name}")
        else:
            raise ValueError(f"No GUID found for node {node_name}")

        logger.info(f"Entity: {entity}")
        mesh_info = MeshInfoDC(
            object_name=node_name, face_index=message.mesh_info.face_index, json_data=json.dumps(entity)
        )
        reply_message = MessageDC(
            instance_id=server.instance_id,
            command_type=CommandTypeDC.MESH_INFO_REPLY,
            mesh_info=mesh_info,
            target_id=client.instance_id,
            target_group=TargetTypeDC.WEB,
        )
        fb_message = serialize_message(reply_message)
        # run the client.websocket in an event loop
        asyncio.run(client.websocket.send(fb_message))
    else:
        logger.error(f"Unknown command type: {message.command_type}")
