import pathlib
from dataclasses import dataclass
from io import StringIO
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    import ifcopenshell.geom


@dataclass
class IfcRef:
    source_ifc_file: Union[str, pathlib.PurePath, StringIO]

    def get_ifc_geom(self, ifc_elem, settings: ifcopenshell.geom.settings):
        return ifcopenshell.geom.create_shape(settings, inst=ifc_elem)
