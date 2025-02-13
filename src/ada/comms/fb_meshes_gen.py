from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MeshDC:
    indices: List[int]
    vertices: List[float] = None


@dataclass
class AppendMeshDC:
    mesh: Optional[MeshDC] = None
