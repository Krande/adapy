from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Callable, Optional, Set
from urllib.parse import parse_qs, urlparse

import websockets
import websockets.protocol
from websockets.asyncio.server import ServerConnection

from ada.comms.fb_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.msg_handling.default_on_message import default_on_message
from ada.comms.scene_model import SceneBackend
from ada.comms.wsock import Message
from ada.comms.wsockets_utils import client_from_str
from ada.config import logger
from ada.procedural_modelling.procedure_store import ProcedureStore


@dataclass
class ConnectedClient:
    websocket: ServerConnection = field(repr=False)
    group_type: TargetTypeDC | None = None
    instance_id: int | None = None
    port: int = field(init=False, default=None, repr=False)

    def __hash__(self):
        return hash(self.websocket)


async def process_client(websocket: ServerConnection) -> ConnectedClient:
    path = websocket.request.path
    group_type = websocket.request.headers.get("Client-Type")
    instance_id = websocket.request.headers.get("instance-id")
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


async def retry_message_sending(
    server: WebSocketAsyncServer, message: bytes, sender: ConnectedClient, msg: MessageDC, num_retries: int = 3
):
    """Retry sending the message to the clients."""
    while num_retries > 0:
        for client in server.connected_clients:
            if client.group_type == msg.target_group:
                if await server.forward_message(message, sender, msg):
                    return
        await asyncio.sleep(1)
        num_retries -= 1
        logger.warning(f"Retrying message sending. Retries left: {num_retries}")


class WebSocketAsyncServer:
    def __init__(
        self,
        host: str,
        port: int,
        on_connect: Optional[Callable[[ConnectedClient], asyncio.Future]] = None,
        on_disconnect: Optional[Callable[[ConnectedClient], asyncio.Future]] = None,
        on_message: Optional[Callable[[WebSocketAsyncServer, ConnectedClient, bytes], None]] = default_on_message,
        on_unsent_message: Optional[
            Callable[[WebSocketAsyncServer, bytes, ConnectedClient, MessageDC, int], None]
        ] = retry_message_sending,
        debug=False,
    ):
        self.host = host
        self.port = port
        self.connected_clients: Set[ConnectedClient] = set()
        self.server: Optional[websockets.server.SERVER] = None
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_message = on_message
        self.on_unsent_message = on_unsent_message
        self.scene = SceneBackend()
        self.instance_id = random.randint(0, 2**31 - 1)  # Generates a random int32 value
        self.msg_queue = asyncio.Queue()
        self.procedure_store = ProcedureStore()
        self.debug = debug

    def get_client_by_instance_id(self, instance_id: int) -> Optional[ConnectedClient]:
        for client in self.connected_clients:
            if client.instance_id == instance_id:
                return client
        return None

    async def handle_client(self, websocket: ServerConnection):
        client = await process_client(websocket)

        self.connected_clients.add(client)
        logger.debug(f"Client connected: {client} [{len(self.connected_clients)} clients connected]")
        if self.on_connect:
            await self.on_connect(client)
        try:
            async for message in websocket:
                await self.handle_message(message, client, websocket)

        except websockets.ConnectionClosed:
            logger.debug(f"Client disconnected: {websocket.remote_address}")

        finally:
            self.connected_clients.remove(client)
            logger.debug(f"Client disconnected: {client} [{await self._get_clients_str()}]")
            if self.on_disconnect:
                await self.on_disconnect(client)

    async def _get_clients_str(self):
        web_clients = [cl for cl in self.connected_clients if cl.group_type == TargetTypeDC.WEB]
        local_clients = [cl for cl in self.connected_clients if cl.group_type == TargetTypeDC.LOCAL]
        return f"Web clients: {len(web_clients)}, Local clients: {len(local_clients)}"

    async def handle_message(self, message: bytes, client: ConnectedClient, websocket: ServerConnection):
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
            try:
                await asyncio.to_thread(self.on_message, self, client, message)
            except Exception as e:
                logger.error(f"Error occurred while processing message: {e}")

    async def forward_message(
        self, message: str | bytes, sender: ConnectedClient, msg: MessageDC, is_forwarded_message=False
    ) -> bool:
        target_id = msg.target_id
        target_group = msg.target_group
        if is_forwarded_message:
            logger.debug(f"Forwarding message to {sender}")
        message_sent = False
        for client in self.connected_clients:
            if client == sender:
                continue
            # check if client is still connected
            if client.websocket.state != websockets.protocol.State.OPEN:
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
            message_sent = True

        if not message_sent and msg.command_type == CommandTypeDC.UPDATE_SCENE:
            logger.debug(f"No clients to forward message {msg} to. Starting retry mechanism.")
            asyncio.create_task(self.on_unsent_message(self, message, sender, msg, 3))
            return False

        return True

    async def start_async(self):
        """Run the server asynchronously. Blocks until the server is stopped. Max size is set to 10MB."""
        self.server = await websockets.serve(self.handle_client, self.host, self.port, max_size=10**7)
        logger.debug(f"WebSocket server started on ws://{self.host}:{self.port}")
        await self.server.wait_closed()

    async def start_async_non_blocking(self):
        """Start the server asynchronously. Does not block the event loop. Max size is set to 10MB."""
        self.server = await websockets.serve(self.handle_client, self.host, self.port, max_size=10**7)
        print(f"WebSocket server started on ws://{self.host}:{self.port}")
        # Do not call await self.server.wait_closed() here

    async def stop(self):
        """Stop the server gracefully."""
        if self.server is not None:
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
