from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Union

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
    merge_by_colour: bool = False
    merge_subgeometries_by_colour: bool = True
    render_edges: bool = False
    ifc_skip_occ: bool = True
    data_filter: DataFilter = field(default_factory=DataFilter)

    # Position of model
    volume_center: Union[None, np.ndarray] = None
    auto_center_model: bool = False
    max_convert_objects: Union[int, None] = None
    do_not_load_by_default: List[str] = None
    use_cache: bool = False

    metadata: dict = field(default_factory=dict)
    name_prefix: str = ""
