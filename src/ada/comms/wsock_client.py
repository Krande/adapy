from __future__ import annotations

import asyncio
import io
import random
from typing import Callable, Any

import trimesh
import websockets

from ada.comms.fb_deserializer import deserialize_root_message
from ada.comms.fb_model_gen import MessageDC, CommandTypeDC, FilePurposeDC, SceneOperationsDC, FileObjectDC, FileTypeDC, \
    TargetTypeDC
from ada.comms.fb_serializer import serialize_message
from ada.config import logger


def dual_sync_async(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    A wrapper to make functions dual-mode (sync/async).
    Depending on the context, either awaits or runs the coroutine.
    """

    async def async_wrapper(*args, **kwargs):
        # Directly call the async function
        return await func(*args, **kwargs)

    def sync_wrapper(*args, **kwargs):
        # Run the coroutine in sync mode
        return asyncio.get_event_loop().run_until_complete(func(*args, **kwargs))

    # Detect if function is async or sync depending on its context
    def wrapper(*args, **kwargs):
        if asyncio.get_event_loop().is_running():
            return async_wrapper(*args, **kwargs)
        else:
            return sync_wrapper(*args, **kwargs)

    return wrapper


def client_as_str(client_type: TargetTypeDC) -> str:
    if client_type == TargetTypeDC.LOCAL:
        return "local"
    elif client_type == TargetTypeDC.WEB:
        return "web"





class WebSocketClient:
    def __init__(self, host: str = "localhost", port: int = 8765, client_type: TargetTypeDC | str = TargetTypeDC.LOCAL):
        if isinstance(client_type, str):
            if client_type == 'local':
                client_type = TargetTypeDC.LOCAL
            elif client_type == 'web':
                client_type = TargetTypeDC.WEB
            else:
                raise ValueError("Invalid client type. Must be either 'local' or 'web'.")
        self.host = host
        self.port = port
        self.client_type = client_type
        self.instance_id = random.randint(0, 2 ** 31 - 1)

    async def __aenter__(self):
        self._conn = websockets.connect(
            f"ws://{self.host}:{self.port}",
            extra_headers={"Client-Type": client_as_str(self.client_type), "instance-id": str(self.instance_id)}
        )
        self.websocket = await self._conn.__aenter__()
        logger.info(f"Connected to server: ws://{self.host}:{self.port}")
        return self

    async def __aexit__(self, *args, **kwargs):
        await self._conn.__aexit__(*args, **kwargs)
        logger.info(f"Disconnected from server: ws://{self.host}:{self.port}")

    # Sync context manager for sync usage
    def __enter__(self):
        self.loop = asyncio.get_event_loop()
        self._conn = websockets.connect(
            f"ws://{self.host}:{self.port}",
            extra_headers={"Client-Type": self.client_type, "instance-id": str(self.instance_id)}
        )
        self.websocket = self.loop.run_until_complete(self._conn.__aenter__())
        logger.info(f"Connected to server: ws://{self.host}:{self.port}")
        return self

    def __exit__(self, *args, **kwargs):
        self.loop.run_until_complete(self._conn.__aexit__(*args, **kwargs))
        logger.info(f"Disconnected from server: ws://{self.host}:{self.port}")

    @dual_sync_async
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

    @dual_sync_async
    async def check_target_liveness(self, target_id=None, target_group: TargetTypeDC = TargetTypeDC.WEB):
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

    @dual_sync_async
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
                target_group=TargetTypeDC.WEB,
                scene_operation=scene_op
            )

            # Serialize the dataclass message into a FlatBuffer
            flatbuffer_data = serialize_message(message)
            await self.websocket.send(flatbuffer_data)

    @dual_sync_async
    async def update_file_server(self, file_object: FileObjectDC):
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.UPDATE_SERVER,
            file_object=file_object,
            target_group=TargetTypeDC.WEB
        )

        # Serialize the dataclass message into a FlatBuffer
        flatbuffer_data = serialize_message(message)
        await self.websocket.send(flatbuffer_data)

    @dual_sync_async
    async def receive(self) -> MessageDC:
        message = await self.websocket.recv()
        return deserialize_root_message(message)
