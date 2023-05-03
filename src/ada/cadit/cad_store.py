from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ada.occ.store import OccShape


@dataclass
class CadStore:
    filepath: str | Path

    def iter_occ_shapes(self) -> Iterable[OccShape]:
        raise NotImplementedError()

    def iter_meshes(self) -> Iterable[MeshStore]:
        raise NotImplementedError()

    def iter_geom(self) -> Iterable[GeomStore]:
        raise NotImplementedError()
