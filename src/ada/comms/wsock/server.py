from __future__ import annotations

import asyncio
import logging
import sys
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Set
from urllib.parse import parse_qs, urlparse

import websockets
import websockets.protocol
from websockets.asyncio.server import ServerConnection

from ada.comms.fb.wsock import Message
from ada.comms.fb_wrap_model_gen import CommandTypeDC, MessageDC, TargetTypeDC
from ada.comms.msg_handling.default_on_message import default_on_message
from ada.comms.wsock.scene_model import SceneBackend
from ada.comms.wsock.utils import client_from_str
from ada.config import Config, logger
from ada.procedural_modelling.procedure_store import ProcedureStore


class WebSocketHandshakeFilter(logging.Filter):
    """Filter to suppress expected WebSocket handshake errors.

    These errors occur when TCP port availability checks are performed
    using raw sockets (e.g., in is_port_open()). The raw TCP connection
    doesn't complete the WebSocket handshake protocol, causing these
    expected and harmless error messages.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Suppress "opening handshake failed" errors from websockets.server
        if record.name == "websockets.server" and "opening handshake failed" in record.getMessage():
            return False
        return True


# Apply the filter to the websockets.server logger to suppress expected handshake errors
_ws_server_logger = logging.getLogger("websockets.server")
_ws_server_logger.addFilter(WebSocketHandshakeFilter())


@dataclass
class ConnectedClient:
    websocket: ServerConnection = field(repr=False)
    group_type: TargetTypeDC | None = None
    instance_id: int | None = None
    port: int = field(init=False, default=None, repr=False)
    last_heartbeat: int = None

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

    if client.group_type == TargetTypeDC.WEB:
        client.last_heartbeat = int(time.time() * 1000)

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
        self.connected_web_clients: Set[ConnectedClient] = set()
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
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        # Idle shutdown watchdog settings/state
        self._idle_timeout_sec: int = 30
        self._watchdog_interval_sec: int = 5
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_activity_monotonic: float = time.monotonic()
        self._last_web_count: int = 0

    def _reset_idle_timer(self, reason: str = "") -> None:
        """Reset the idle timer to postpone automatic shutdown.

        This is non-blocking and safe to call from within the event loop.
        """
        self._last_activity_monotonic = time.monotonic()
        if reason:
            logger.debug(f"Idle timer reset: {reason}")

    async def _idle_check_loop(self) -> None:
        """Periodic, non-blocking watchdog that exits the process after inactivity.

        Rules:
        - Runs every 5 seconds.
        - Maintains a single inactivity timer that resets when:
          * The number of web clients changes (add/remove), or
          * A local client connects (or sends a message).
        - If there are zero web clients AND more than 30 seconds have passed
          since the last activity, exit the process.
        """
        try:
            while True:
                await asyncio.sleep(self._watchdog_interval_sec)
                # Only consider shutdown when there are zero web clients
                current_web_count = len([cl for cl in self.connected_clients if cl.group_type == TargetTypeDC.WEB])
                if current_web_count == 0:
                    idle_for = time.monotonic() - self._last_activity_monotonic
                    if idle_for > self._idle_timeout_sec:
                        logger.warning(
                            f"No web clients and no recent local activity for {idle_for:.1f}s (> {self._idle_timeout_sec}s). Exiting."
                        )
                        # Exit process to avoid detached server lingering
                        sys.exit(0)
        except asyncio.CancelledError:
            # Task cancelled during server shutdown
            logger.debug("Idle watchdog task cancelled")
            raise

    def send_message_threadsafe(self, client: ConnectedClient, message: bytes) -> None:
        """Send a message to a client from any thread (including threads created by asyncio.to_thread).

        This method uses run_coroutine_threadsafe to schedule the send operation
        on the main event loop, which is necessary because websocket operations
        must be performed on the same event loop that owns the connection.
        """
        if self._event_loop is None:
            raise RuntimeError("Event loop not set. Server must be started first.")
        future = asyncio.run_coroutine_threadsafe(client.websocket.send(message), self._event_loop)
        # Wait for the result to ensure the message is sent before returning
        future.result()

    def get_client_by_instance_id(self, instance_id: int) -> Optional[ConnectedClient]:
        for client in self.connected_clients:
            if client.instance_id == instance_id:
                return client
        return None

    async def handle_client(self, websocket: ServerConnection):
        client = await process_client(websocket)

        self.connected_clients.add(client)

        if client.group_type == TargetTypeDC.WEB and client not in self.connected_web_clients:
            logger.debug(f"Adding web client to heartbeat tracking: {client.instance_id}")
            self.connected_web_clients.add(client)
        # If the number of web clients changed, reset the idle timer
        current_web_count = len(self.connected_web_clients)
        if current_web_count != self._last_web_count:
            self._last_web_count = current_web_count
            self._reset_idle_timer("web clients count changed (connect)")

        # If a local client connects, reset the idle timer
        if client.group_type == TargetTypeDC.LOCAL:
            self._reset_idle_timer("local client connected")

        logger.debug(f"Client connected: {client.instance_id} [{len(self.connected_clients)} clients connected]")
        if self.on_connect:
            await self.on_connect(client)
        try:
            async for message in websocket:
                await self.handle_message(message, client, websocket)

        except websockets.ConnectionClosed:
            logger.debug(f"Client disconnected: {websocket.remote_address}")

        finally:
            self.connected_clients.remove(client)
            if client in self.connected_web_clients:
                self.connected_web_clients.remove(client)
                logger.debug(f"Removing web client from heartbeat tracking: {client.instance_id}")
            # If the number of web clients changed, reset the idle timer
            current_web_count = len(self.connected_web_clients)
            if current_web_count != self._last_web_count:
                self._last_web_count = current_web_count
                self._reset_idle_timer("web clients count changed (disconnect)")
            logger.debug(f"Client disconnected: {client.instance_id} [{await self._get_clients_str()}]")
            if self.on_disconnect:
                await self.on_disconnect(client)

    async def _get_clients_str(self):
        web_clients = [cl for cl in self.connected_clients if cl.group_type == TargetTypeDC.WEB]
        local_clients = [cl for cl in self.connected_clients if cl.group_type == TargetTypeDC.LOCAL]
        return f"Web clients: {len(web_clients)}, Local clients: {len(local_clients)}"

    async def handle_message(self, message: bytes, client: ConnectedClient, websocket: ServerConnection):
        msg = await handle_partial_message(message)
        logger.debug(f"Received message: {msg.command_type.name} from {client.instance_id}")

        if msg.command_type == CommandTypeDC.HEARTBEAT:
            client.last_heartbeat = int(time.time() * 1000)
            return

        # If a local client sends any message, consider that activity and reset timer
        if client.group_type == TargetTypeDC.LOCAL:
            self._reset_idle_timer("message from local client")

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
            logger.debug(f"Forwarding message to {sender.instance_id}")
        message_sent = False
        clients_to_remove = []
        if Config().general_target_id_support:
            client_ids = [client.instance_id for client in self.connected_clients]
            if target_id is not None and target_id not in client_ids:
                from ada.visit.rendering.renderer_react import RendererReact

                RendererReact().show(target_id=target_id)
        for client in self.connected_clients:
            if client == sender:
                continue
            # check if client is still connected
            if client.websocket.state != websockets.protocol.State.OPEN:
                logger.debug(f"Client disconnected: {client}")
                clients_to_remove.append(client)
                continue

            # Filtering based on target_id and target_group
            if target_id is not None and client.instance_id != target_id:
                continue
            if target_group and client.group_type != target_group:
                continue

            logger.debug(f"Forwarding message to {client.instance_id}")

            await client.websocket.send(message)
            message_sent = True

        for client in clients_to_remove:
            self.connected_clients.remove(client)

        if not message_sent and msg.command_type == CommandTypeDC.UPDATE_SCENE:
            logger.debug(f"No clients to forward message {msg} to. Starting retry mechanism.")
            asyncio.create_task(self.on_unsent_message(self, message, sender, msg, 3))
            return False

        return True

    async def start_async(self):
        """Run the server asynchronously. Blocks until the server is stopped. Max size is set to 10MB."""
        self._event_loop = asyncio.get_running_loop()
        self.server = await websockets.serve(self.handle_client, self.host, self.port, max_size=10**7)
        logger.debug(f"WebSocket server started on ws://{self.host}:{self.port}")
        # Start idle watchdog task (non-blocking loop)
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._idle_check_loop())
        await self.server.wait_closed()

    async def start_async_non_blocking(self):
        """Start the server asynchronously. Does not block the event loop. Max size is set to 10MB."""
        self._event_loop = asyncio.get_running_loop()
        self.server = await websockets.serve(self.handle_client, self.host, self.port, max_size=10**7)
        print(f"WebSocket server started on ws://{self.host}:{self.port}")
        # Start idle watchdog task (non-blocking loop)
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._idle_check_loop())
        # Do not call await self.server.wait_closed() here

    async def stop(self):
        """Stop the server gracefully."""
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        # Cancel watchdog task
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        logger.debug("WebSocket server stopped")


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
