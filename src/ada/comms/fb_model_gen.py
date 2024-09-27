from __future__ import annotations

import pathlib
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class CommandTypeDC(Enum):
    PING = 0
    PONG = 1
    UPDATE_SCENE = 2
    UPDATE_SERVER = 3
    MESH_INFO_CALLBACK = 4
    MESH_INFO_REPLY = 5
    LIST_WEB_CLIENTS = 6
    LIST_FILE_OBJECTS = 7
    LIST_PROCEDURES = 8
    ERROR = 9
    SERVER_REPLY = 10


class TargetTypeDC(Enum):
    WEB = 0
    LOCAL = 1
    SERVER = 2


class SceneOperationsDC(Enum):
    ADD = 0
    REMOVE = 1
    REPLACE = 2


class FilePurposeDC(Enum):
    DESIGN = 0
    ANALYSIS = 1
    FABRICATE = 2


class FileTypeDC(Enum):
    IFC = 0
    GLB = 1
    SQLITE = 2


@dataclass
class WebClientDC:
    instance_id: int = None
    name: str = ""
    address: str = ""
    port: int = None


@dataclass
class FileObjectDC:
    name: str = ""
    file_type: Optional[FileTypeDC] = None
    purpose: Optional[FilePurposeDC] = None
    filepath: pathlib.Path | str = ""
    filedata: bytes = None


@dataclass
class MeshInfoDC:
    object_name: str = ""
    face_index: int = None
    json_data: str = ""


@dataclass
class CameraParamsDC:
    position: List[float] = None
    look_at: List[float] = None
    up: List[float] = None
    fov: float = None
    near: float = None
    far: float = None
    force_camera: bool = None


@dataclass
class SceneOperationDC:
    operation: Optional[SceneOperationsDC] = None
    camera_params: Optional[CameraParamsDC] = None


@dataclass
class ProcedureStoreDC:
    procedures: Optional[List[ProcedureDC]] = None


@dataclass
class ProcedureDC:
    id: str = ""
    name: str = ""
    description: str = ""
    script_file_location: str = ""
    parameters: Optional[List[ParameterDC]] = None
    input_ifc_filepath: pathlib.Path | str = ""
    output_ifc_filepath: pathlib.Path | str = ""
    error: str = ""


@dataclass
class ParameterDC:
    name: str = ""
    type: str = ""
    value: str = ""


@dataclass
class ErrorDC:
    code: int = None
    message: str = ""


@dataclass
class MessageDC:
    instance_id: int = None
    command_type: Optional[CommandTypeDC] = None
    file_object: Optional[FileObjectDC] = None
    mesh_info: Optional[MeshInfoDC] = None
    target_group: Optional[TargetTypeDC] = None
    client_type: Optional[TargetTypeDC] = None
    scene_operation: Optional[SceneOperationDC] = None
    target_id: int = None
    web_clients: Optional[List[WebClientDC]] = None
    procedure_store: Optional[ProcedureStoreDC] = None
