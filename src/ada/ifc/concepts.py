import pathlib
from dataclasses import dataclass
from io import StringIO
from typing import Union


@dataclass
class IfcRef:
    source_ifc_file: Union[str, pathlib.PurePath, StringIO]
