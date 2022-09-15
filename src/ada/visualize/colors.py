from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class VisColor:
    name: str
    pbrMetallicRoughness: PbrMetallicRoughness
    used_by: List[str]


@dataclass
class PbrMetallicRoughness:
    baseColorFactor: List[float]
    metallicFactor: float
    roughnessFactor: float
