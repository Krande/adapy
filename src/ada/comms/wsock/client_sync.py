import time
import uuid

import trimesh
from websockets.sync.client import connect

from ada.comms.fb.fb_commands_gen import WebClientDC
from ada.comms.fb_wrap_deserializer import deserialize_root_message
from ada.comms.fb_wrap_model_gen import (
    CommandTypeDC,
    FileObjectDC,
    FilePurposeDC,
    MeshDC,
    MessageDC,
    ProcedureDC,
    ProcedureStartDC,
    SceneOperationsDC,
    TargetTypeDC,
)
from ada.comms.wsock.client_base import WebSocketClientBase
from ada.config import logger


class WebSocketClientSync(WebSocketClientBase):
    def __init__(self, host: str = "localhost", port: int = 8765, client_type: TargetTypeDC | str = TargetTypeDC.LOCAL):
        super().__init__(host, port, client_type)

    def connect(self):
        """Synchronous connection setup using websockets.sync.client.connect."""
        url = f"ws://{self.host}:{self.port}" if self.url_override is None else self.url_override
        self.websocket = connect(url, additional_headers=self._extra_headers())
        logger.info(f"Connected to server: ws://{self.host}:{self.port}")

    def disconnect(self):
        """Synchronous disconnect."""
        if self.websocket:
            self.websocket.close()
            logger.info(f"Disconnected from server: ws://{self.host}:{self.port}")

    def __enter__(self):
        """Synchronous connection setup using websockets.sync.client.connect."""
        self.connect()
        return self

    def __exit__(self, *args, **kwargs):
        """Synchronous disconnect."""
        self.disconnect()

    def receive(self, timeout=None) -> MessageDC:
        """Receives a message from the WebSocket with an optional timeout."""
        message = self.websocket.recv(timeout=timeout)
        return deserialize_root_message(message)

    def check_target_liveness(self, target_id=None, target_group: TargetTypeDC = TargetTypeDC.WEB, timeout=1):
        """Checks if the target is alive by sending a PING command."""
        self.websocket.send(self._prep_target_liveness(target_id, target_group))

        try:
            msg = self.receive(timeout=timeout)
        except TimeoutError:
            return False

        return msg.command_type == CommandTypeDC.PONG

    def update_scene(
        self,
        name: str,
        scene: trimesh.Scene,
        purpose: FilePurposeDC = FilePurposeDC.DESIGN,
        scene_op: SceneOperationsDC = SceneOperationsDC.REPLACE,
        gltf_buffer_postprocessor=None,
        gltf_tree_postprocessor=None,
        target_id=None,
    ):
        """Updates the scene with the given GLTF data."""
        buffer = self._scene_update_prep(
            name, scene, purpose, scene_op, gltf_buffer_postprocessor, gltf_tree_postprocessor, target_id=target_id
        )
        self.websocket.send(buffer)

    def append_scene(self, mesh: MeshDC, target_id=None):
        buffer = self._scene_append_prep(mesh, target_id=target_id)
        self.websocket.send(buffer)

    def update_file_server(self, file_object: FileObjectDC):
        """Updates the file server with a new file."""
        self.websocket.send(self._update_file_server_prep(file_object))

    def list_procedures(self) -> list[ProcedureDC]:
        """Lists the available procedures."""
        self.websocket.send(self._list_procedures_prep())
        msg = self.receive()
        return msg.procedure_store.procedures

    def get_file_object(self, name: str) -> FileObjectDC: ...

    def list_server_file_objects(self) -> list[FileObjectDC]:
        self.websocket.send(self._list_server_file_objects_prep())
        msg = self.receive()
        return msg.server.all_file_objects

    def run_procedure(self, procedure: ProcedureStartDC) -> None:
        """Runs a procedure with the given name and arguments."""
        self.websocket.send(self._run_procedure_prep(procedure))

    def view_file_object(self, file_name: str) -> None:
        self.websocket.send(self._get_file_object_prep(file_name))

    def register_fea_source(
        self, source_key: str, bake_dir: str, bake_id: str = "", open_in_viewer: bool = True, timeout: float = 30
    ) -> None:
        """Let the viewers read the FEA bake in ``bake_dir`` as ``source_key`` (and open it).

        Waits for the server's answer, so the bake is registered before this returns;
        raises RuntimeError if the server refuses it."""
        request_id = uuid.uuid4().hex
        self.websocket.send(
            self._register_fea_source_prep(source_key, str(bake_dir), bake_id, open_in_viewer, request_id)
        )
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no answer from the server registering FEA source {source_key!r}")
            msg = self.receive(timeout=remaining)
            if msg.request_id != request_id:
                continue  # an unrelated push; not the answer to this request
            if msg.command_type == CommandTypeDC.ERROR:
                error = msg.server_reply.error if msg.server_reply is not None else None
                raise RuntimeError(error.message if error is not None else "the server refused the FEA source")
            return

    def list_connected_web_clients(self) -> list[WebClientDC]:
        self.websocket.send(self._list_connected_web_clients_prep())
        msg = self.receive()
        if msg.web_clients is None:
            return []
        return msg.web_clients
