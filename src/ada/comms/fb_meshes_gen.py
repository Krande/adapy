from __future__ import annotations
from enum import Enum
from dataclasses import dataclass
from typing import Optional, List
import pathlib




@dataclass
class MeshDC:
    indices: Optional[List[uint32DC]] = None
    vertices: List[float] = None

@dataclass
class AppendMeshDC:
    mesh: Optional[MeshDC] = None
