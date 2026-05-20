from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, OrderedDict

from ada.base.types import GeomRepr
from ada.comms.fb.fb_base_gen import FilePurposeDC
from ada.comms.fb.fb_scene_gen import SceneDC, SceneOperationsDC

if TYPE_CHECKING:
    import trimesh


@dataclass
class FEARenderParams:
    step: int = (None,)
    field: str = (None,)
    warp_field: str = (None,)
    warp_step: int = (None,)
    cfunc: Callable[[list[float]], float] = (None,)
    warp_scale: float = 1.0
    solid_beams: bool = False


@dataclass
class RenderParams:
    auto_sync_ifc_store: bool = True
    stream_from_ifc_store: bool = True
    merge_meshes: bool = True
    scene_post_processor: Optional[Callable[[trimesh.Scene], trimesh.Scene]] = None
    purpose: Optional[FilePurposeDC] = FilePurposeDC.DESIGN
    scene: SceneDC = None
    _gltf_buffer_postprocessor: Optional[Callable[[OrderedDict, dict], None]] = None
    _gltf_tree_postprocessor: Optional[Callable[[OrderedDict], None]] = None
    gltf_export_to_file: str | pathlib.Path = None
    gltf_asset_extras_dict: dict = None
    add_ifc_backend: bool = False
    backend_file_dir: Optional[str] = None
    unique_id: int = None
    fea_params: Optional[FEARenderParams] = field(default_factory=FEARenderParams)
    serve_web_port: int = 5174
    serve_ws_port: int = 8765
    serve_html: bool = False
    apply_transform: bool = False
    render_override: dict[str, GeomRepr | str] = None
    filter_by_guids: list[str] = None
    embed_ada_extension: bool = True
    # When True, emit per-Beam/Plate section + material dicts into
    # ``DesignDataExtension.object_metadata`` so the viewer's Properties
    # panel populates without a server round-trip back to the source
    # IFC. On by default because the viewer is the primary consumer and
    # the size cost is negligible over HTTP gzip — measured on
    # mini-example (1100 objects): raw GLB +335 KB (+17.8 %), gzipped
    # only +7.7 KB (+3.1 %) since repeated material dicts compress to
    # ~7 B amortised. Flip off only for raw-bytes consumers that don't
    # need the Properties panel and skip HTTP gzip.
    embed_object_metadata: bool = True
    force_y_is_up: bool = False

    def __post_init__(self):
        # ensure that if unique_id is set, it is a 32-bit integer
        if self.unique_id is not None:
            self.unique_id = self.unique_id & 0xFFFFFFFF
        if self.scene is None:
            self.scene = SceneDC(operation=SceneOperationsDC.REPLACE)

    def set_gltf_buffer_postprocessor(
        self, postprocessor: Callable[[OrderedDict, dict], None], overwrite: bool = False
    ):
        if self._gltf_buffer_postprocessor is not None and overwrite is False:
            raise ValueError("gltf_buffer_postprocessor is already set.")
        self._gltf_buffer_postprocessor = postprocessor

    def set_gltf_tree_postprocessor(self, postprocessor: Callable[[OrderedDict], None], overwrite: bool = False):
        if self._gltf_tree_postprocessor is not None and overwrite is False:
            raise ValueError("gltf_tree_postprocessor is already set.")
        self._gltf_tree_postprocessor = postprocessor

    @property
    def gltf_buffer_postprocessor(self):
        return self._gltf_buffer_postprocessor

    @property
    def gltf_tree_postprocessor(self):
        return self._gltf_tree_postprocessor
