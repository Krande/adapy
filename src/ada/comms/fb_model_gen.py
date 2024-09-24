from enum import Enum
from dataclasses import dataclass
from typing import Optional, List
import pathlib



class CommandTypeDC(Enum):
    PING = 0
    PONG = 1
    UPDATE_SCENE = 2
    UPDATE_SERVER = 3
    MESH_INFO_CALLBACK = 4
    MESH_INFO_REPLY = 5
    LIST_WEB_CLIENTS = 6

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
class MessageDC:
    instance_id: int = None
    command_type: Optional[CommandTypeDC] = None
    file_object: Optional[FileObjectDC] = None
    mesh_info: Optional[MeshInfoDC] = None
    target_group: Optional[TargetTypeDC] = None
    client_type: Optional[TargetTypeDC] = None
    scene_operation: Optional[SceneOperationsDC] = None
    target_id: int = None
    web_clients: Optional[List[WebClientDC]] = None
