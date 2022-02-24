from dataclasses import dataclass
from typing import List, Union


@dataclass
class ExportConfig:
    quality: float = 1.0
    threads: int = 1
    parallel: bool = True
    merge_by_colour: bool = False
    render_edges: bool = False
    ifc_skip_occ: bool = False
    filter_elements_by_guid: Union[None, List[str]] = None
