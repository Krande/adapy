from __future__ import annotations

import io
import random
from abc import ABC, abstractmethod

import trimesh

from ada import logger
from ada.comms.fb_wrap_model_gen import (
    AppendMeshDC,
    CommandTypeDC,
    FileObjectDC,
    FilePurposeDC,
    FileTypeDC,
    MeshDC,
    MessageDC,
    ProcedureDC,
    ProcedureStartDC,
    ProcedureStoreDC,
    SceneDC,
    SceneOperationsDC,
    ServerDC,
    TargetTypeDC,
)
from ada.comms.fb_wrap_serializer import serialize_root_message
from ada.comms.wsockets_utils import client_as_str


class WebSocketClientBase(ABC):
    def __init__(self, host: str, port: int, client_type: TargetTypeDC | str, url_override: str = None):
        if isinstance(client_type, str):
            if client_type == "local":
                client_type = TargetTypeDC.LOCAL
            elif client_type == "web":
                client_type = TargetTypeDC.WEB
            else:
                raise ValueError("Invalid client type. Must be either 'local' or 'web'.")
        self.host = host
        self.port = port
        self.client_type = client_type
        self.instance_id = random.randint(0, 2**31 - 1)
        self.websocket = None
        self.url_override = url_override

    def _extra_headers(self):
        return {"Client-Type": client_as_str(self.client_type), "instance-id": str(self.instance_id)}

    def _prep_target_liveness(self, target_id, target_group) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.PING,
            target_id=target_id,
            target_group=target_group,
            client_type=self.client_type,
        )

        # Serialize the dataclass message into a FlatBuffer
        return serialize_root_message(message)

    def _scene_update_prep(
        self,
        name: str,
        scene: trimesh.Scene,
        purpose: FilePurposeDC = FilePurposeDC.DESIGN,
        scene_op: SceneOperationsDC = SceneOperationsDC.REPLACE,
        gltf_buffer_postprocessor=None,
        gltf_tree_postprocessor=None,
        target_id=None,
    ) -> bytes:
        import gzip

        with io.BytesIO() as data:
            scene.export(
                file_obj=data,
                file_type="glb",
                buffer_postprocessor=gltf_buffer_postprocessor,
                tree_postprocessor=gltf_tree_postprocessor,
            )
            glb_data = data.getvalue()
        size_mb = len(glb_data) / 1_000_000
        compressed = False

        if size_mb > 5:
            logger.warning(f"Large GLB size ({size_mb:.2f} MB) detected for scene '{name}', compressing...")

            glb_data = gzip.compress(glb_data)
            compressed = True

        file_object = FileObjectDC(
            name=name,
            file_type=FileTypeDC.GLB,
            purpose=purpose,
            filedata=glb_data,
            compressed=compressed,
        )
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.UPDATE_SCENE,
            target_group=TargetTypeDC.WEB,
            target_id=target_id,
            scene=SceneDC(operation=scene_op, current_file=file_object),
        )
        return serialize_root_message(message)

    def _scene_append_prep(self, mesh: MeshDC, target_id) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.UPDATE_SCENE,
            target_group=TargetTypeDC.WEB,
            target_id=target_id,
            scene=SceneDC(operation=SceneOperationsDC.ADD),
            package=AppendMeshDC(mesh),
        )
        return serialize_root_message(message)

    def _update_file_server_prep(self, file_object: FileObjectDC) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.UPDATE_SERVER,
            server=ServerDC(new_file_object=file_object),
            target_group=TargetTypeDC.SERVER,
        )
        return serialize_root_message(message)

    def _list_procedures_prep(self) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.LIST_PROCEDURES,
            target_group=TargetTypeDC.SERVER,
        )
        return serialize_root_message(message)

    def _run_procedure_prep(self, procedure: ProcedureStartDC) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.RUN_PROCEDURE,
            procedure_store=ProcedureStoreDC(start_procedure=procedure),
            target_group=TargetTypeDC.SERVER,
        )
        return serialize_root_message(message)

    def _view_file_object_prep(self, file_name: str) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.VIEW_FILE_OBJECT,
            target_group=TargetTypeDC.SERVER,
            client_type=TargetTypeDC.WEB,
            server=ServerDC(get_file_object_by_name=file_name),
        )
        return serialize_root_message(message)

    def _list_server_file_objects_prep(self) -> bytes:
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.LIST_FILE_OBJECTS,
            target_group=TargetTypeDC.SERVER,
        )
        return serialize_root_message(message)

    def _get_file_object_prep(self, file_name):
        message = MessageDC(
            instance_id=self.instance_id,
            command_type=CommandTypeDC.GET_FILE_OBJECT,
            target_group=TargetTypeDC.SERVER,
            server=ServerDC(get_file_object_by_name=file_name),
        )
        return serialize_root_message(message)

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def receive(self, timeout=None) -> MessageDC:
        pass

    @abstractmethod
    def check_target_liveness(self, target_id=None, target_group=TargetTypeDC.WEB, timeout=1):
        pass

    @abstractmethod
    def update_scene(
        self,
        name: str,
        scene: trimesh.Scene,
        purpose: FilePurposeDC,
        scene_op: SceneOperationsDC,
        gltf_buffer_postprocessor=None,
    ):
        pass

    @abstractmethod
    def update_file_server(self, file_object: FileObjectDC):
        pass

    @abstractmethod
    def list_procedures(self) -> list[ProcedureDC]:
        pass

    @abstractmethod
    def list_server_file_objects(self) -> list[FileObjectDC]:
        pass

    @abstractmethod
    def get_file_object(self, name: str) -> FileObjectDC:
        pass

    @abstractmethod
    def run_procedure(self, procedure: ProcedureStartDC) -> None:
        pass

    @abstractmethod
    def view_file_object(self, file_name: str) -> None:
        pass
