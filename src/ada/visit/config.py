from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Union

import numpy as np

import ada.base.physical_objects
from ada.visit.plots import section_overview_to_html_str


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


JUPYTER_GEOM_RENDERER: Callable[[ada.base.physical_objects.BackendGeom], None] | None = None
JUPYTER_SECTION_RENDERER: Callable[[ada.sections.Section], None] | None = section_overview_to_html_str


def set_jupyter_part_renderer(renderer: Callable[[ada.base.physical_objects.BackendGeom], None]) -> None:
    global JUPYTER_GEOM_RENDERER

    JUPYTER_GEOM_RENDERER = renderer


def set_jupyter_section_renderer(renderer: Callable[[ada.sections.Section], None]) -> None:
    global JUPYTER_SECTION_RENDERER

    JUPYTER_SECTION_RENDERER = renderer
