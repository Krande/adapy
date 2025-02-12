from __future__ import annotations

import asyncio

import trimesh
import websockets

from ada.comms.exceptions import ServerError
from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    FileObjectDC,
    FilePurposeDC,
    MessageDC,
    ProcedureDC,
    ProcedureStartDC,
    SceneOperationsDC,
    TargetTypeDC,
)
from ada.comms.wsock_client_base import WebSocketClientBase
from ada.config import logger


class WebSocketClientAsync(WebSocketClientBase):
    def __init__(
        self,
        host: str = "localhost",
        port: int = 8765,
        client_type: TargetTypeDC | str = TargetTypeDC.LOCAL,
        url_override: str = None,
    ):
        super().__init__(host, port, client_type, url_override)

    async def connect(self):
        url = f"ws://{self.host}:{self.port}" if self.url_override is None else self.url_override
        conn = websockets.connect(url, additional_headers=self._extra_headers())
        self.websocket = await conn.__aenter__()
        logger.info(f"Connected to server: ws://{self.host}:{self.port}")

    async def disconnect(self):
        if self.websocket:
            await self.websocket.close()
            logger.info(f"Disconnected from server: ws://{self.host}:{self.port}")

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args, **kwargs):
        await self.disconnect()

    async def check_target_liveness(self, target_id=None, target_group: TargetTypeDC = TargetTypeDC.WEB, timeout=1):
        """Checks if the target is alive by sending a PING command."""
        await self.websocket.send(self._prep_target_liveness(target_id, target_group))
        try:
            msg = await self.receive(timeout=timeout)
        except asyncio.TimeoutError:
            return False

        return msg.command_type == CommandTypeDC.PONG

    async def update_scene(
        self,
        name: str,
        scene: trimesh.Scene,
        purpose: FilePurposeDC = FilePurposeDC.DESIGN,
        scene_op: SceneOperationsDC = SceneOperationsDC.REPLACE,
        gltf_buffer_postprocessor=None,
        gltf_tree_postprocessor=None,
        target_id=None,
    ):
        # Serialize the dataclass message into a FlatBuffer
        await self.websocket.send(
            self._scene_update_prep(
                name, scene, purpose, scene_op, gltf_buffer_postprocessor, gltf_tree_postprocessor, target_id
            )
        )

    async def update_file_server(self, file_object: FileObjectDC):
        await self.websocket.send(self._update_file_server_prep(file_object))

    async def receive(self, timeout=None) -> MessageDC:
        if timeout:
            message = await asyncio.wait_for(self.websocket.recv(), timeout)
        else:
            message = await self.websocket.recv()
        msg = deserialize_root_message(message)

        if msg.server_reply and msg.server_reply.error is not None:
            raise ServerError(msg.server_reply.error.message)

        return msg

    async def list_procedures(self) -> list[ProcedureDC]:
        await self.websocket.send(self._list_procedures_prep())
        msg = await self.receive()
        return msg.procedure_store.procedures

    async def list_server_file_objects(self) -> list[FileObjectDC]:
        await self.websocket.send(self._list_server_file_objects_prep())
        msg = await self.receive()
        return msg.server.all_file_objects

    async def get_file_object(self, name: str) -> FileObjectDC:
        await self.websocket.send(self._get_file_object_prep(name))
        msg = await self.receive()
        return msg.server.file

    async def run_procedure(self, procedure: ProcedureStartDC) -> None:
        """Runs a procedure with the given name and arguments."""
        await self.websocket.send(self._run_procedure_prep(procedure))

    async def view_file_object(self, file_name: str) -> None:
        await self.websocket.send(self._view_file_object_prep(file_name))
