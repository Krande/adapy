import asyncio
import json
import random
from dataclasses import dataclass
from typing import Optional, Callable, Set, Literal
from urllib.parse import urlparse, parse_qs

import websockets

from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import MessageDC, CommandTypeDC
from ada.comms.fb_serializer import serialize_message
from ada.comms.wsock import Message
from ada.config import logger


@dataclass
class ConnectedClient:
    client: websockets.WebSocketServerProtocol
    group_type: str = None
    instance_id: int | None = None

    def __hash__(self):
        return hash(self.client)


class WebSocketAsyncServer:
    def __init__(
            self,
            host: str,
            port: int,
            on_connect: Optional[Callable[['ConnectedClient'], asyncio.Future]] = None,
            on_disconnect: Optional[Callable[['ConnectedClient'], asyncio.Future]] = None,
            on_message: Optional[Callable[['ConnectedClient', MessageDC], asyncio.Future]] = None
    ):
        self.host = host
        self.port = port
        self.connected_clients: Set[ConnectedClient] = set()
        self.server: Optional[websockets.server.Serve] = None
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_message = on_message

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol, path: str):
        client = ConnectedClient(client=websocket, group_type=websocket.request_headers.get("Client-Type"))
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

        self.connected_clients.add(client)
        logger.debug(f"Client connected: {client} - {websocket.remote_address}")
        if self.on_connect:
            await self.on_connect(client)
        try:
            async for message in websocket:
                logger.debug(f"Received message from client: {message}")
                msg = await handle_partial_message(message)

                if self.on_message:
                    await self.on_message(client, msg)

                # Update instance_id if not set
                if client.client == websocket:
                    if client.instance_id is None:
                        client.instance_id = msg.instance_id
                    if client.group_type is None:
                        client.group_type = msg.client_type

                # Forward message to appropriate clients
                await self.forward_message(message, sender=client, msg=msg)
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

    def run_in_background(self):
        """Run the server in a background thread."""
        import threading

        thread = threading.Thread(target=lambda: asyncio.run(self.start_async()), daemon=True)
        thread.start()
        return thread


@dataclass
class WebSocketClientAsync:
    host: str = "localhost"
    port: int = 8765
    client_type: Literal["web", "local"] = "local"

    async def __aenter__(self):
        self.instance_id = random.randint(0, 2 ** 31 - 1)  # Generates a random int32 value
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
        await self.send("list_clients", target_group="web")
        return await self.receive()

    async def check_server_liveness_using_fb(self, target_id=None, target_group: Literal["web", "local"] = "web"):
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


async def start_async_server():
    server = WebSocketAsyncServer("localhost", 8765)
    await server.start_async()


if __name__ == '__main__':
    logger.setLevel("DEBUG")
    asyncio.run(start_async_server())
