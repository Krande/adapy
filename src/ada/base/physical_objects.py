from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable

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

    from ada import FEM, Boolean
    from ada.cadit.ifc.store import IfcStore
    from ada.fem import Elem
    from ada.fem.meshing import GmshOptions


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
        placement=None,
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
            color = Color(*color_dict["gray"], opacity=opacity)
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
    ) -> FEM:
        from ada.fem.meshing import GmshOptions, GmshSession

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        options = GmshOptions(Mesh_Algorithm=8) if options is None else options
        with GmshSession(silent=silent, options=options) as gs:
            gs.add_obj(self, geom_repr=geom_repr)
            gs.mesh(mesh_size, use_quads=use_quads, use_hex=use_hex)
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
        p.fem = self.to_fem_obj(mesh_size, geom_repr, options, silent, use_quads, use_hex, name)
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
        from IPython.display import display
        from ipywidgets import HBox, VBox

        from ada.config import Settings
        from ada.visit.rendering.renderer_pythreejs import MyRenderer

        if Settings.use_new_visualize_api is True:
            from ada.visit.rendering.new_render_api import Visualize

            viz = Visualize(self)
            viz.objects = []
            viz.add_obj(self)
            viz.display(return_viewer=False)
            return ""

        renderer = MyRenderer()

        renderer.DisplayObj(self)
        renderer.build_display()
        display(HBox([VBox([HBox(renderer.controls), renderer.renderer]), renderer.html]))
        return ""

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
