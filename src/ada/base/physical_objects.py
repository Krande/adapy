from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Callable, Iterable, Literal

from ada.api.transforms import Placement
from ada.base.root import Root
from ada.base.types import GeomRepr
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BoolOpEnum
from ada.visit.colors import Color, color_dict
from ada.visit.config import ExportConfig

if TYPE_CHECKING:
    from OCC.Core.TopoDS import (
        TopoDS_Compound,
        TopoDS_Face,
        TopoDS_Shell,
        TopoDS_Solid,
        TopoDS_Wire,
    )

    from ada import FEM, Boolean, BoolHalfSpace, Point
    from ada.cadit.ifc.store import IfcStore
    from ada.fem import Elem
    from ada.fem.meshing import GmshOptions
    from ada.visit.render_params import RenderParams


class BackendGeom(Root):
    """The backend of all physical components (Beam, Plate, etc.) or aggregate of components (Part, Assembly)"""

    def __init__(
        self,
        name,
        guid=None,
        metadata=None,
        units=Units.M,
        parent=None,
        color: Color | Iterable[float, float, float] | str | None = None,
        placement: Placement = None,
        ifc_store: IfcStore = None,
        opacity=1.0,
    ):
        super().__init__(name, guid, metadata, units, parent, ifc_store=ifc_store)
        self._booleans = []

        self._placement = placement if placement is not None else Placement()
        self._placement.parent = self

        if isinstance(color, str):
            color = Color.from_str(color, opacity=opacity)
        elif isinstance(color, Iterable):
            color = list(color)
            if len(color) == 3:
                color = Color(*color, opacity=opacity)
            else:
                color = Color(*color)
        elif color is None:
            color = Color(*color_dict["light-gray"], opacity=opacity)
        self.color = color
        self._elem_refs = []

    def add_boolean(
        self,
        boolean: BackendGeom,
        bool_op: str | BoolOpEnum = BoolOpEnum.DIFFERENCE,
        add_to_layer: str = None,
    ):
        from ada import Boolean, Shape
        from ada.base.changes import ChangeAction

        boolean.parent = self
        if isinstance(bool_op, str):
            bool_op = BoolOpEnum.from_str(bool_op)
        if issubclass(type(boolean), Shape) is True:
            # Will automatically wrap the shape in a Boolean using the Difference operation
            boolean = Boolean(boolean, bool_op, parent=self)
            self._booleans.append(boolean)
        elif isinstance(boolean, Boolean):
            self._booleans.append(boolean)
        elif isinstance(boolean, BoolHalfSpace):
            self._booleans.append(boolean)
        else:
            raise ValueError(f"Cannot add {type(boolean)} to {type(self)}")

        if self.change_type in (ChangeAction.NOCHANGE, ChangeAction.NOTDEFINED):
            self.change_type = ChangeAction.MODIFIED

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(boolean, add_to_layer)

        return boolean

    def to_fem_obj(
        self,
        mesh_size,
        geom_repr: str | GeomRepr,
        options: GmshOptions = None,
        silent=True,
        use_quads=False,
        use_hex=False,
        name="AdaFEM",
        interactive=False,
        perform_quality_check=False,
    ) -> FEM:
        from ada.fem.meshing import GmshOptions, GmshSession

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        options = GmshOptions(Mesh_Algorithm=8) if options is None else options
        with GmshSession(silent=silent, options=options) as gs:
            gs.add_obj(self, geom_repr=geom_repr)
            gs.mesh(mesh_size, use_quads=use_quads, use_hex=use_hex, perform_quality_check=perform_quality_check)
            if interactive:
                gs.open_gui()
            return gs.get_fem(name)

    def to_fem(
        self,
        mesh_size,
        geom_repr,
        name: str,
        fem_format: str,
        options: GmshOptions = None,
        silent=True,
        use_quads=False,
        use_hex=False,
        return_assembly=False,
        **kwargs,
    ):
        from ada import Assembly, Part

        p = Part(name)
        p.fem = self.to_fem_obj(
            mesh_size=mesh_size,
            geom_repr=geom_repr,
            options=options,
            silent=silent,
            use_quads=use_quads,
            use_hex=use_hex,
            name=name,
        )
        a = Assembly() / (p / self)
        if return_assembly:
            return a
        a.to_fem(name, fem_format, **kwargs)

    def to_stp(self, destination_file, geom_repr: GeomRepr = GeomRepr.SOLID, progress_callback: Callable = None):
        from ada.occ.store import OCCStore

        step_writer = OCCStore.get_step_writer()
        step_writer.add_shape(self.solid_occ(), self.name, rgb_color=self.color.rgb)
        step_writer.export(destination_file)

    def to_obj_mesh(self, geom_repr: str | GeomRepr = GeomRepr.SOLID, export_config: ExportConfig = ExportConfig()):
        from ada.occ.visit_utils import occ_geom_to_poly_mesh

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        return occ_geom_to_poly_mesh(self, geom_repr=geom_repr, export_config=export_config)

    def show(
        self,
        renderer: Literal["react", "pygfx", "trimesh"] = "react",
        host="localhost",
        ws_port=8765,
        server_exe: pathlib.Path = None,
        server_args: list[str] = None,
        run_ws_in_thread=False,
        unique_viewer_id=None,
        stream_from_ifc_store=False,
        append_to_scene=False,
        add_ifc_backend=False,
        auto_sync_ifc_store=True,
        params_override: RenderParams = None,
        apply_transform=False,
        liveness_timeout: int = 1,
        embed_glb: bool = False,
        auto_embed_glb_in_notebook=True,
        force_ws=False,
        always_use_external_viewer=False,
    ):
        """
        Show model using either react, pygfx or trimesh renderer and set up WebSocket connection

        :param renderer: The renderer to use, can be 'react', 'pygfx' or 'trimesh'.
        :param host: The host to run the WebSocket server on.
        :param ws_port: The port for the WebSocket server.
        :param server_exe: Path to the WebSocket server executable.
        :param server_args: Additional arguments for the WebSocket server.
        :param run_ws_in_thread: Whether to run the WebSocket server in a separate thread.
        :param unique_viewer_id: Unique ID for the viewer, used for synchronization.
        :param stream_from_ifc_store: Whether to stream the geometry from the IFC store.
        :param append_to_scene: Whether to append this geometry to the existing scene.
        :param add_ifc_backend: Whether to add the IFC backend to the scene.
        :param auto_sync_ifc_store: Whether to automatically synchronize the IFC store.
        :param params_override: Override parameters for rendering.
        :param apply_transform: Whether to apply the placement transform to the geometry.
        :param liveness_timeout: Timeout for checking the WebSocket connection.
        :param embed_glb: Whether to embed the GLB file in the WebSocket server.
        :param auto_embed_glb_in_notebook: Whether to automatically embed the GLB in Jupyter Notebook.
        :param force_ws: Whether to force the use of WebSocket for rendering.
        :param always_use_external_viewer: Whether to always use an external viewer for rendering.
        """
        from ada.comms.fb_wrap_model_gen import SceneDC, SceneOperationsDC
        from ada.visit.renderer_manager import RendererManager, RenderParams

        renderer_manager = RendererManager(
            renderer=renderer,
            host=host,
            ws_port=ws_port,
            server_exe=server_exe,
            server_args=server_args,
            run_ws_in_thread=run_ws_in_thread,
            ping_timeout=liveness_timeout,
            embed_glb=embed_glb,
        )

        if params_override is None:
            params_override = RenderParams(
                unique_id=unique_viewer_id,
                auto_sync_ifc_store=auto_sync_ifc_store,
                add_ifc_backend=add_ifc_backend,
                scene=SceneDC(operation=SceneOperationsDC.REPLACE if not append_to_scene else SceneOperationsDC.ADD),
                apply_transform=apply_transform,
            )
        params_override.stream_from_ifc_store = stream_from_ifc_store
        # Set up the renderer and WebSocket server
        renderer_instance = renderer_manager.render(
            self,
            params_override,
            force_ws=force_ws,
            auto_embed_glb_in_notebook=auto_embed_glb_in_notebook,
            always_use_external_viewer=always_use_external_viewer,
        )
        return renderer_instance

    @property
    def booleans(self) -> list[Boolean]:
        return self._booleans

    @property
    def elem_refs(self) -> list[Elem]:
        return self._elem_refs

    @elem_refs.setter
    def elem_refs(self, value):
        self._elem_refs = value

    @property
    def placement(self) -> Placement:
        return self._placement

    @placement.setter
    def placement(self, value: Placement):
        self._placement = value

    def _repr_html_(self):
        from ada.visit.config import JUPYTER_GEOM_RENDERER

        if JUPYTER_GEOM_RENDERER is None:
            return

        return JUPYTER_GEOM_RENDERER(self)

    def solid_occ(self) -> TopoDS_Solid | TopoDS_Compound:
        raise NotImplementedError()

    def shell_occ(self) -> TopoDS_Shell | TopoDS_Face:
        raise NotImplementedError()

    def line_occ(self) -> TopoDS_Wire:
        raise NotImplementedError()

    def solid_geom(self) -> Geometry:
        raise NotImplementedError(f"solid_geom not implemented for {self.__class__.__name__}")

    def shell_geom(self) -> Geometry:
        raise NotImplementedError(f"shell_geom not implemented for {self.__class__.__name__}")

    def line_geom(self) -> Geometry:
        raise NotImplementedError(f"line_geom not implemented for {self.__class__.__name__}")

    def copy_to(
        self,
        name: str,
        position: list[float] | Point = None,
        rotation_axis: Iterable[float] = None,
        rotation_angle: float = None,
    ) -> BackendGeom:
        raise NotImplementedError(f"copy_to not implemented for {self.__class__.__name__}")
