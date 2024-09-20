from enum import Enum
from dataclasses import dataclass
from typing import Optional

class CommandTypeDC(Enum):
    PING = 0
    PONG = 1
    SEND_FILE = 2
    SEND_BINARY = 3
    MESH_INFO = 4

class SceneOperationsDC(Enum):
    ADD = 0
    REMOVE = 1
    UPDATE = 2

class FilePurposeDC(Enum):
    DESIGN = 0
    ANALYSIS = 1
    FABRICATE = 2

class FileTypeDC(Enum):
    IFC = 0
    GLB = 1
    SQLITE = 2

@dataclass
class FileObjectDC:
    file_type: Optional[FileTypeDC] = None
    purpose: Optional[FilePurposeDC] = None
    filepath: str = None

@dataclass
class BinaryDataDC:
    data: bytes

@dataclass
class MeshInfoDC:
    object_name: str
    face_index: int

@dataclass
class MessageDC:
    instance_id: int
    command_type: Optional[CommandTypeDC] = None
    file_object: Optional[FileObjectDC] = None
    binary_data: Optional[BinaryDataDC] = None
    mesh_info: Optional[MeshInfoDC] = None
    target_group: str = None
    client_type: str = None
    scene_operation: Optional[SceneOperationsDC] = None
    target_id: int = None
