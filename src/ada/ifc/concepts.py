import pathlib
from dataclasses import dataclass
from typing import Union


@dataclass
class IfcRef:
    source_ifc_file: Union[str, pathlib.PurePath]
