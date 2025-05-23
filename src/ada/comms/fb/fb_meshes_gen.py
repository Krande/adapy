from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MeshDC:
    name: str = ""
    indices: List[int] = None
    vertices: List[float] = None
    parent_name: str = ""


@dataclass
class AppendMeshDC:
    mesh: Optional[MeshDC] = None


@dataclass
class MeshInfoDC:
    object_name: str = ""
    face_index: int = None
    json_data: str = ""
    file_name: str = ""
