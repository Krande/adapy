from __future__ import annotations

import asyncio
import io
import json
import os
import pathlib
import random
import socket
import time
from dataclasses import dataclass, field
from typing import Optional, Callable, Set, Literal
from urllib.parse import urlparse, parse_qs

import trimesh
import websockets

from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import MessageDC, CommandTypeDC, FileObjectDC, SceneOperationsDC, FileTypeDC, FilePurposeDC
from ada.comms.fb_serializer import serialize_message
from ada.comms.wsock import Message
from ada.config import logger
from ada.visit.websocket_server import start_external_ws_server


@dataclass
class ConnectedClient:
    client: websockets.WebSocketServerProtocol = field(repr=False)
    group_type: str = None
    instance_id: int | None = None
    port: int = field(init=False, default=None)

    def __hash__(self):
        return hash(self.client)


async def process_client(websocket, path) -> ConnectedClient:
    group_type = websocket.request_headers.get("Client-Type")
    instance_id = websocket.request_headers.get("instance-id")
    if instance_id is not None:
        instance_id = int(instance_id)
    client = ConnectedClient(client=websocket, group_type=group_type, instance_id=instance_id)

    if client.group_type is None and 'instance-id' in path:
        # Parse query parameters from the path
        parsed_url = urlparse(path)
        query_params = parse_qs(parsed_url.query)

        # Extract client type and instance id
        group_type = query_params.get("client-type", [None])[0]
        if group_type is not None:
            client.group_type = group_type
        instance_id = query_params.get("instance-id", [None])[0]
        if instance_id is not None:
            client.instance_id = int(instance_id)
    return client


@dataclass
class SceneMeta:
    file_objects: list[FileObjectDC] = field(default_factory=list)


class WebSocketAsyncServer:
    def __init__(
            self,
            host: str,
            port: int,
            on_connect: Optional[Callable[[ConnectedClient], asyncio.Future]] = None,
            on_disconnect: Optional[Callable[[ConnectedClient], asyncio.Future]] = None,
            on_message: Optional[Callable[[WebSocketAsyncServer, ConnectedClient, bytes], None]] = None,
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

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol, path: str):
        client = await process_client(websocket, path)

        self.connected_clients.add(client)
        logger.debug(f"Client connected: {client} [{len(self.connected_clients)} clients connected]")
        if self.on_connect:
            await self.on_connect(client)
        try:
            async for message in websocket:
                msg = await handle_partial_message(message)
                logger.debug(f"Received message: {msg}")

                # Update instance_id if not set
                if client.client == websocket:
                    if client.instance_id is None:
                        client.instance_id = msg.instance_id
                    if client.group_type is None:
                        client.group_type = msg.client_type

                # Forward message to appropriate clients
                await self.forward_message(message, sender=client, msg=msg)

                if self.on_message:
                    # Offload to a separate thread
                    await asyncio.to_thread(self.on_message, self, client, message)

        except websockets.ConnectionClosed:
            logger.debug(f"Client disconnected: {websocket.remote_address}")
        finally:
            self.connected_clients.remove(client)
            if self.on_disconnect:
                await self.on_disconnect(client)

    async def forward_message(self, message: str | bytes, sender: ConnectedClient, msg: MessageDC):
        target_id = msg.target_id
        target_group = msg.target_group
        for client in self.connected_clients:
            if client == sender:
                continue
            # check if client is still connected
            if client.client.closed:
                logger.debug(f"Client disconnected: {client}")
                self.connected_clients.remove(client)
                continue
            # Filtering based on target_id and target_group
            if target_id is not None and client.instance_id != target_id:
                continue
            if target_group and client.group_type != target_group:
                continue

            await client.client.send(message)

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

    async def receive(self) -> MessageDC:
        message = await self.server.recv()
        if isinstance(message, bytes):
            return deserialize_root_message(message)
        else:
            try:
                msg = json.loads(message)
                logger.info(f"Received message: {msg}")
                return msg
            except json.JSONDecodeError:
                logger.error("Received non-JSON message")
                return message

    def run_in_background(self):
        """Run the server in a background thread."""
        import threading

        thread = threading.Thread(target=lambda: asyncio.run(self.start_async()), daemon=True)
        thread.start()
        return thread


class WebSocketClientAsync:
    def __init__(self, host: str = "localhost", port: int = 8765, client_type: Literal["web", "local"] = "local"):
        self.host = host
        self.port = port
        self.client_type = client_type
        self.instance_id = random.randint(0, 2 ** 31 - 1)

    async def __aenter__(self):
        self._conn = websockets.connect(
            f"ws://{self.host}:{self.port}",
            extra_headers={"Client-Type": self.client_type, "instance-id": str(self.instance_id)}
        )
        self.websocket = await self._conn.__aenter__()
        logger.info(f"Connected to server: ws://{self.host}:{self.port}")
        return self

    async def __aexit__(self, *args, **kwargs):
        await self._conn.__aexit__(*args, **kwargs)
        logger.info(f"Disconnected from server: ws://{self.host}:{self.port}")

    async def list_web_clients(self):
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.LIST_WEB_CLIENTS,
        )
        # Serialize the dataclass message into a FlatBuffer
        flatbuffer_data = serialize_message(message)
        await self.websocket.send(flatbuffer_data)
        msg = await self.receive()
        return msg

    async def check_target_liveness_using_fb(self, target_id=None, target_group: Literal["web", "local"] = "web"):
        """Sends a Flatbuffer package to the server."""
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.PING,
            target_id=target_id,
            target_group=target_group
        )

        # Serialize the dataclass message into a FlatBuffer
        flatbuffer_data = serialize_message(message)
        await self.websocket.send(flatbuffer_data)
        msg = await self.receive()
        return msg.command_type == CommandTypeDC.PONG

    async def update_scene(self, scene: trimesh.Scene, purpose: FilePurposeDC = FilePurposeDC.DESIGN,
                           scene_op: SceneOperationsDC = SceneOperationsDC.REPLACE):
        with io.BytesIO() as data:
            scene.export(file_obj=data, file_type="glb")
            file_object = FileObjectDC(
                file_type=FileTypeDC.GLB,
                purpose=purpose,
                filedata=data.getvalue()
            )
            message = MessageDC(
                instance_id=self.instance_id,
                command_type=CommandTypeDC.UPDATE_SCENE,
                file_object=file_object,
                target_group="web",
                scene_operation=scene_op
            )

            # Serialize the dataclass message into a FlatBuffer
            flatbuffer_data = serialize_message(message)
            await self.websocket.send(flatbuffer_data)

    async def update_file_server(self, file_object: FileObjectDC):
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.UPDATE_SERVER,
            file_object=file_object,
            target_group="web"
        )

        # Serialize the dataclass message into a FlatBuffer
        flatbuffer_data = serialize_message(message)
        await self.websocket.send(flatbuffer_data)

    async def check_server_liveness_using_json(self, target_id=None, target_group: Literal["web", "local"] = "web"):
        pkg = {
            "instance_id": self.instance_id,
            "command_type": CommandTypeDC.PING.value,
            "target_id": target_id,
            "target_group": target_group,
            "client_type": self.client_type
        }
        await self.websocket.send(json.dumps(pkg))
        logger.info(f"Sent message: {pkg}")
        msg = await self.receive()
        return msg.command_type == CommandTypeDC.PONG

    async def receive(self) -> MessageDC:
        message = await self.websocket.recv()
        if isinstance(message, bytes):
            return deserialize_root_message(message)
        else:
            try:
                msg = json.loads(message)
                logger.info(f"Received message: {msg}")
                return msg
            except json.JSONDecodeError:
                logger.error("Received non-JSON message")
                return message


async def handle_partial_message(message) -> MessageDC | None:
    """Parse """
    if isinstance(message, bytes):
        message_fb = Message.Message.GetRootAsMessage(message)
        target_id = message_fb.TargetId()
        if target_id == 0:
            target_id = None
        return MessageDC(
            instance_id=message_fb.InstanceId(),
            command_type=CommandTypeDC(message_fb.CommandType()),
            target_id=target_id,
            target_group=message_fb.TargetGroup().decode('utf-8'),
            client_type=message_fb.ClientType().decode('utf-8')
        )
    else:
        try:
            msg = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("Invalid message received")
            return None

        return MessageDC(
            instance_id=msg.get("instance_id"),
            command_type=CommandTypeDC(msg.get("command_type")),
            target_id=msg.get("target_id"),
            target_group=msg.get("target_group"),
            client_type=msg.get("client_type")
        )


def is_port_open(host: str, port: int) -> bool:
    """Quickly check if a port is open using a socket connection."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, socket.timeout):
            return False


async def _check_websocket_server(host: str, port: int) -> bool:
    """Check if a WebSocket server is running by trying to connect."""
    try:
        async with websockets.connect(f"ws://{host}:{port}"):
            logger.info(f"WebSocket server is running on ws://{host}:{port}")
            return True
    except (websockets.exceptions.InvalidURI, OSError, websockets.exceptions.ConnectionClosedError) as e:
        logger.debug(f"Error checking WebSocket server: {e}")
        return False


def is_server_running(host="localhost", port=8765) -> bool:
    """Efficiently check if a WebSocket server is running."""
    if not is_port_open(host, port):
        logger.info(f"Port {port} on host {host} is not open.")
        return False

    loop = asyncio.get_event_loop()
    if loop.is_running():
        # If an asyncio loop is already running, create a new task.
        return asyncio.ensure_future(_check_websocket_server(host, port))
    else:
        # Run the async check if the loop is not running.
        return loop.run_until_complete(_check_websocket_server(host, port))


def start_ws_async_server(
        host="localhost",
        port=8765,
        server_exe: pathlib.Path = None,
        server_args: list[str] = None,
        run_in_thread=False,
        override_binder_check=False,
) -> WebSocketAsyncServer:
    from ada.comms.cli_async_ws_server import WS_ASYNC_SERVER_PY

    if server_exe is None:
        server_exe = WS_ASYNC_SERVER_PY

    # Check if we are running in a binder environment
    res = os.getenv("BINDER_SERVICE_HOST", None)
    if res is not None and override_binder_check is False:
        logger.info(
            "Running in binder environment, starting server in thread. Pass override_binder_check=True to override"
        )
        logger.warning("Binder does not support websockets, so you will not be able to send data to the viewer")
        run_in_thread = True

    ws = None
    if is_server_running(host, port) is False:
        if run_in_thread:
            ws = WebSocketAsyncServer(host=host, port=port)
            ws.run_in_background()
        else:
            start_external_ws_server(server_exe, server_args)

        while is_server_running(host, port) is False:
            time.sleep(0.1)

    return ws
