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
    LIST_WEB_CLIENTS = 5

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
    instance_id: int
    name: str
    address: str
    port: int

@dataclass
class FileObjectDC:
    file_type: Optional[FileTypeDC] = None
    purpose: Optional[FilePurposeDC] = None
    filepath: pathlib.Path | str = ""
    filedata: bytes = None

@dataclass
class MeshInfoDC:
    object_name: str
    face_index: int

@dataclass
class MessageDC:
    instance_id: int
    command_type: Optional[CommandTypeDC] = None
    file_object: Optional[FileObjectDC] = None
    mesh_info: Optional[MeshInfoDC] = None
    target_group: str = ""
    client_type: str = ""
    scene_operation: Optional[SceneOperationsDC] = None
    target_id: int = None
    web_clients: Optional[List[WebClientDC]] = None
