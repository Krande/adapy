from __future__ import annotations

import asyncio
import json
import pathlib
import random
from dataclasses import dataclass, field
from typing import Callable, Optional, Set
from urllib.parse import parse_qs, urlparse

import trimesh
import websockets

from ada.cadit.ifc.ifc2sql import Ifc2SqlPatcher
from ada.cadit.ifc.sql_model import IfcSqlModel as ifc_sqlite
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
from ada.comms.procedures import ProcedureStore
from ada.comms.wsock import Message
from ada.config import logger, Config


@dataclass
class ConnectedClient:
    websocket: websockets.WebSocketServerProtocol = field(repr=False)
    group_type: TargetTypeDC | None = None
    instance_id: int | None = None
    port: int = field(init=False, default=None)

    def __hash__(self):
        return hash(self.websocket)


def client_from_str(client_type: str) -> TargetTypeDC:
    if client_type == "local":
        return TargetTypeDC.LOCAL
    elif client_type == "web":
        return TargetTypeDC.WEB


async def process_client(websocket, path) -> ConnectedClient:
    group_type = websocket.request_headers.get("Client-Type")
    instance_id = websocket.request_headers.get("instance-id")
    if instance_id is not None:
        instance_id = int(instance_id)
    if group_type is not None:
        group_type = client_from_str(group_type)
    client = ConnectedClient(websocket=websocket, group_type=group_type, instance_id=instance_id)

    if client.group_type is None and "instance-id" in path:
        # Parse query parameters from the path
        parsed_url = urlparse(path)
        query_params = parse_qs(parsed_url.query)

        # Extract client type and instance id
        group_type = query_params.get("client-type", [None])[0]
        if group_type is not None:
            client.group_type = client_from_str(group_type)
        instance_id = query_params.get("instance-id", [None])[0]
        if instance_id is not None:
            client.instance_id = int(instance_id)

    return client


@dataclass
class SceneMeta:
    file_objects: list[FileObjectDC] = field(default_factory=list)
    ifc_sql_store: ifc_sqlite = None
    mesh_meta: dict = None


def default_on_message(server: WebSocketAsyncServer, client: ConnectedClient, message_data: bytes) -> None:
    message = deserialize_root_message(message_data)
    if message.command_type == CommandTypeDC.UPDATE_SCENE:
        logger.info(f"Received message from {client} to update scene")
        glb_file_data = message.file_object.filedata
        tmp_dir = pathlib.Path("temp") if Config().websockets_server_temp_dir is None else Config().websockets_server_temp_dir
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
            server.scene_meta.ifc_sql_store = ifc_sqlite(tmp_sql_fp)
    elif message.command_type == CommandTypeDC.MESH_INFO_CALLBACK:
        logger.info(f"Received message from {client} to update mesh info")
        logger.info(f"Message: {message}")
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


class WebSocketAsyncServer:
    def __init__(
            self,
            host: str,
            port: int,
            on_connect: Optional[Callable[[ConnectedClient], asyncio.Future]] = None,
            on_disconnect: Optional[Callable[[ConnectedClient], asyncio.Future]] = None,
            on_message: Optional[Callable[[WebSocketAsyncServer, ConnectedClient, bytes], None]] = default_on_message,
    ):
        self.host = host
        self.port = port
        self.connected_clients: Set[ConnectedClient] = set()
        self.server: Optional[websockets.server.SERVER] = None
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_message = on_message
        self.scene_meta = SceneMeta()
        self.instance_id = random.randint(0, 2 ** 31 - 1)  # Generates a random int32 value
        self.msg_queue = asyncio.Queue()
        self.procedure_store = ProcedureStore()

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol, path: str):
        client = await process_client(websocket, path)

        self.connected_clients.add(client)
        logger.debug(f"Client connected: {client} [{len(self.connected_clients)} clients connected]")
        if self.on_connect:
            await self.on_connect(client)
        try:
            async for message in websocket:
                await self.handle_message(message, client, websocket)

            while not self.msg_queue.empty():
                msg = self.msg_queue.get_nowait()
                await self.forward_message(serialize_message(msg), sender=client, msg=msg)

        except websockets.ConnectionClosed:
            logger.debug(f"Client disconnected: {websocket.remote_address}")

        finally:
            self.connected_clients.remove(client)
            logger.debug(f"Client disconnected: {client} [{len(self.connected_clients)} clients connected]")
            if self.on_disconnect:
                await self.on_disconnect(client)

    async def handle_message(
            self, message: bytes, client: ConnectedClient, websocket: websockets.WebSocketServerProtocol
    ):
        msg = await handle_partial_message(message)
        logger.debug(f"Received message: {msg}")

        # Update instance_id if not set
        if client.websocket == websocket:
            if client.instance_id is None:
                client.instance_id = msg.instance_id
            if client.group_type is None:
                client.group_type = msg.client_type

        if msg.target_group != TargetTypeDC.SERVER:
            # Forward message to appropriate clients
            await self.forward_message(message, sender=client, msg=msg)

        if self.on_message and msg.command_type not in [CommandTypeDC.PING, CommandTypeDC.PONG]:
            # Offload to a separate thread
            await asyncio.to_thread(self.on_message, self, client, message)

    async def forward_message(self, message: str | bytes, sender: ConnectedClient, msg: MessageDC):
        target_id = msg.target_id
        target_group = msg.target_group

        for client in self.connected_clients:
            if client == sender:
                continue
            # check if client is still connected
            if client.websocket.closed:
                logger.debug(f"Client disconnected: {client}")
                self.connected_clients.remove(client)
                continue

            # Filtering based on target_id and target_group
            if target_id is not None and client.instance_id != target_id:
                continue
            if target_group and client.group_type != target_group:
                continue

            logger.debug(f"Forwarding message to {client}")

            await client.websocket.send(message)

    async def start_async(self):
        try:
            self.server = await websockets.serve(self.handle_client, self.host, self.port)
            logger.debug(f"WebSocket server started on ws://{self.host}:{self.port}")
            await self.server.wait_closed()
        except asyncio.CancelledError:
            await self.stop()  # Call stop to gracefully close connections
            raise  # Reraise the exception to ensure proper task handling

    async def stop(self):
        """Stop the server gracefully."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.debug("WebSocket server stopped")

    def run_in_background(self):
        """Run the server in a background thread."""
        import threading

        thread = threading.Thread(target=lambda: asyncio.run(self.start_async()), daemon=True)
        thread.start()
        return thread


async def handle_partial_message(message) -> MessageDC | None:
    """Parse only parts of the message needed to forward it to the appropriate clients."""
    message_fb = Message.Message.GetRootAsMessage(message)
    target_id = message_fb.TargetId()
    if target_id == 0:
        target_id = None

    return MessageDC(
        instance_id=message_fb.InstanceId(),
        command_type=CommandTypeDC(message_fb.CommandType()),
        target_id=target_id,
        target_group=TargetTypeDC(message_fb.TargetGroup()),
        client_type=TargetTypeDC(message_fb.ClientType()),
    )
