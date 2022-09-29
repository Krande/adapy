from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ifcopenshell.geom

    from ada import Assembly


@dataclass
class IfcRef:
    source_ifc_file: os.PathLike | ifcopenshell.file

    def get_ifc_geom(self, ifc_elem, settings: ifcopenshell.geom.settings):
        import ifcopenshell.geom

        return ifcopenshell.geom.create_shape(settings, inst=ifc_elem)


@dataclass
class IfcIOBase:
    f: ifcopenshell.file = None
    a: Assembly = None


@dataclass
class IfcIO(IfcIOBase):
    def from_ifc(self, ifc_file: str | pathlib.Path) -> Assembly:
        ...

    def to_ifc(self, assembly: Assembly, ifc_file: str | pathlib.Path):
        ...
