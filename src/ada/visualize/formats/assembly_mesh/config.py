from __future__ import annotations

from dataclasses import dataclass
from typing import List, Union, Callable

import numpy as np


@dataclass
class DataFilter:
    name_filter: Union[None, List[str]] = None
    filter_elements_by_guid: Union[None, List[str]] = None
    filter_func: Callable = None
    filter_func_ref: str = None


@dataclass
class ExportConfig:
    quality: float = 1.0
    threads: int = 1
    parallel: bool = True
    merge_by_colour: bool = True
    render_edges: bool = False
    ifc_skip_occ: bool = False
    data_filter: DataFilter = DataFilter()
    # Position of model
    volume_center: Union[None, np.ndarray] = None
    auto_center_model: bool = True
    max_convert_objects: int = None
    do_not_load_by_default: List[str] = None
