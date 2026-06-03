from dataclasses import dataclass
from typing import Optional, Tuple

from ada.comms.fb.fb_scene_gen import CameraParamsDC


@dataclass
class Camera:
    position: Optional[Tuple[float, float, float]] = None
    look_at: Optional[Tuple[float, float, float]] = None
    up: Optional[Tuple[float, float, float]] = None
    fov: float = 70
    near: float = 0.1
    far: float = 1000
    fit_view: bool = True
    padding: float = 0.2  # 20% padding -> 80% filling

    def to_camera_dc(self) -> CameraParamsDC:
        return CameraParamsDC(
            position=list(self.position) if self.position is not None else None,
            look_at=list(self.look_at) if self.look_at is not None else None,
            up=list(self.up) if self.up is not None else None,
            fov=self.fov,
            near=self.near,
            far=self.far,
        )
