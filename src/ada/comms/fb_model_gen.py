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
    RUN_PROCEDURE = 9
    ERROR = 10
    SERVER_REPLY = 11
    VIEW_FILE_OBJECT = 12
    DELETE_FILE_OBJECT = 13
    START_NEW_NODE_EDITOR = 14
    START_FILE_IN_LOCAL_APP = 15


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
    XLSX = 3


class ProcedureStateDC(Enum):
    IDLE = 0
    RUNNING = 1
    FINISHED = 2
    ERROR = 3


class ParameterTypeDC(Enum):
    UNKNOWN = 0
    STRING = 1
    FLOAT = 2
    INTEGER = 3
    BOOLEAN = 4
    ARRAY = 6


class ArrayTypeDC(Enum):
    TUPLE = 0
    LIST = 1
    SET = 2


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
    glb_file: Optional[FileObjectDC] = None
    ifcsqlite_file: Optional[FileObjectDC] = None
    is_procedure_output: bool = None
    procedure_parent: Optional[ProcedureStartDC] = None


@dataclass
class FileObjectRefDC:
    name: str = ""
    file_type: Optional[FileTypeDC] = None
    purpose: Optional[FilePurposeDC] = None
    filepath: pathlib.Path | str = ""
    glb_file: Optional[FileObjectRefDC] = None
    ifcsqlite_file: Optional[FileObjectRefDC] = None
    is_procedure_output: bool = None
    procedure_parent: Optional[ProcedureStartDC] = None


@dataclass
class MeshInfoDC:
    object_name: str = ""
    face_index: int = None
    json_data: str = ""
    file_name: str = ""


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
class SceneDC:
    operation: Optional[SceneOperationsDC] = None
    camera_params: Optional[CameraParamsDC] = None
    current_file: Optional[FileObjectDC] = None


@dataclass
class ServerDC:
    new_file_object: Optional[FileObjectDC] = None
    all_file_objects: Optional[List[FileObjectDC]] = None
    get_file_object_by_name: str = ""
    get_file_object_by_path: pathlib.Path | str = ""
    delete_file_object: Optional[FileObjectDC] = None
    start_file_in_local_app: Optional[FileObjectDC] = None


@dataclass
class ProcedureStoreDC:
    procedures: Optional[List[ProcedureDC]] = None
    start_procedure: Optional[ProcedureStartDC] = None


@dataclass
class FileArgDC:
    arg_name: str = ""
    file_type: Optional[FileTypeDC] = None


@dataclass
class ProcedureDC:
    name: str = ""
    description: str = ""
    script_file_location: str = ""
    parameters: Optional[List[ParameterDC]] = None
    file_inputs: Optional[List[FileArgDC]] = None
    file_outputs: Optional[List[FileArgDC]] = None
    state: Optional[ProcedureStateDC] = None
    is_component: bool = None


@dataclass
class ValueDC:
    string_value: str = ""
    float_value: float = None
    integer_value: int = None
    boolean_value: bool = None
    array_value: Optional[List[ValueDC]] = None
    array_value_type: Optional[ParameterTypeDC] = None
    array_length: int = None
    array_type: Optional[ArrayTypeDC] = None
    array_any_length: bool = None


@dataclass
class ParameterDC:
    name: str = ""
    type: Optional[ParameterTypeDC] = None
    value: Optional[ValueDC] = None
    default_value: Optional[ValueDC] = None
    options: Optional[List[ValueDC]] = None


@dataclass
class ProcedureStartDC:
    procedure_name: str = ""
    procedure_id_string: str = ""
    parameters: Optional[List[ParameterDC]] = None


@dataclass
class ErrorDC:
    code: int = None
    message: str = ""


@dataclass
class ServerReplyDC:
    message: str = ""
    file_objects: Optional[List[FileObjectDC]] = None
    reply_to: Optional[CommandTypeDC] = None
    error: Optional[ErrorDC] = None


@dataclass
class MessageDC:
    instance_id: int = None
    command_type: Optional[CommandTypeDC] = None
    scene: Optional[SceneDC] = None
    server: Optional[ServerDC] = None
    mesh_info: Optional[MeshInfoDC] = None
    target_group: Optional[TargetTypeDC] = None
    client_type: Optional[TargetTypeDC] = None
    target_id: int = None
    web_clients: Optional[List[WebClientDC]] = None
    procedure_store: Optional[ProcedureStoreDC] = None
    server_reply: Optional[ServerReplyDC] = None
