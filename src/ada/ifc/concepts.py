from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ifcopenshell.geom


@dataclass
class IfcRef:
    source_ifc_file: os.PathLike | ifcopenshell.file

    def get_ifc_geom(self, ifc_elem, settings: ifcopenshell.geom.settings):
        import ifcopenshell.geom

        return ifcopenshell.geom.create_shape(settings, inst=ifc_elem)
