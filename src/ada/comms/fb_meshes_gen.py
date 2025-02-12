from __future__ import annotations
from typing import Optional, List
from dataclasses import dataclass




@dataclass
class MeshDC:
    indices: List[int]
    vertices: List[float] = None

@dataclass
class AppendMeshDC:
    mesh: Optional[MeshDC] = None
