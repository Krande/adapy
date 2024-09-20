from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


# Enums based on FlatBuffers schema
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


# Data classes for FlatBuffers schema

@dataclass
class FileObjectDC:
    file_type: FileTypeDC        # The file type (IFC, GLB, SQLITE)
    purpose: FilePurposeDC       # The purpose (DESIGN, ANALYSIS, FABRICATE)
    filepath: str                # Path to the file


@dataclass
class BinaryDataDC:
    data: List[int]              # Binary data (e.g., GLB)


@dataclass
class MeshInfoDC:
    object_name: str             # Mesh object name
    face_index: int              # Mesh face index


@dataclass
class MessageDC:
    instance_id: int                             # ID of the instance
    command_type: CommandTypeDC                  # Command type (PING, PONG, etc.)
    file_object: Optional[FileObjectDC] = None   # File object, optional
    binary_data: Optional[BinaryDataDC] = None   # Binary data, optional
    mesh_info: Optional[MeshInfoDC] = None       # Mesh info, optional
    target_id: int = 0                           # Target ID, optional
    target_group: str = ""                       # Target group, e.g., "web"
    client_type: str = ""                        # Client type, e.g., "local"
    scene_operation: Optional[SceneOperationsDC] = None  # Scene operation (ADD, REMOVE, UPDATE)
