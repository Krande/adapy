from __future__ import annotations

import pathlib
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from ada.comms.fb.fb_base_gen import FileObjectDC


class SceneOperationsDC(Enum):
    ADD = 0
    REMOVE = 1
    REPLACE = 2


@dataclass
class CameraParamsDC:
    position: List[float] = None
    look_at: List[float] = None
    up: List[float] = None
    fov: float = None
    near: float = None
    far: float = None
    force_camera: bool = False


@dataclass
class ScreenshotDC:
    png_file_path: pathlib.Path | str = ""


@dataclass
class SceneDC:
    operation: Optional[SceneOperationsDC] = None
    camera_params: Optional[CameraParamsDC] = None
    current_file: Optional[FileObjectDC] = None
