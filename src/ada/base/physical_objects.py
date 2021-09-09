import os
import pathlib

from ada.core.constants import color_map as _cmap

from .non_phyical_objects import Backend


class BackendGeom(Backend):
    """The backend of all physical components (Beam, Plate, etc.) or aggregate of components (Part, Assembly)"""

    _renderer = None

    def __init__(self, name, guid=None, metadata=None, units="m", parent=None, colour=None, ifc_elem=None):
        super().__init__(name, guid, metadata, units, parent, ifc_elem=ifc_elem)
        self._penetrations = []
        self.colour = colour
        self._elem_refs = []

    def add_penetration(self, pen):
        from ada import Penetration, Shape

        pen.parent = self

        if issubclass(type(pen), Shape) is True:
            pen = Penetration(pen, parent=self)
            self._penetrations.append(pen)
        elif type(pen) is Penetration:
            self._penetrations.append(pen)
        else:
            raise ValueError("")

        return pen

    def to_stp(self, destination_file, geom_repr=None, schema="AP242", silent=False, fuse_piping=False):
        from ada.fem.shapes import ElemType
        from ada.occ.writer import StepExporter

        geom_repr = ElemType.SOLID if geom_repr is None else geom_repr
        step_export = StepExporter(schema)
        step_export.add_to_step_writer(self, geom_repr, fuse_piping=fuse_piping)
        step_export.write_to_file(destination_file, silent)

    def render_locally(
        self, addr="localhost", server_port=8080, open_webbrowser=False, render_engine="threejs", resolution=(1800, 900)
    ):
        from OCC.Display.WebGl.simple_server import start_server

        from ada.visualize.renderer import MyRenderer

        if render_engine == "xdom":
            from OCC.Display.WebGl import x3dom_renderer

            my_renderer = x3dom_renderer.X3DomRenderer()
            # TODO: Find similarities in build processing as done for THREE.js (tesselate geom etc..).
            # my_renderer.DisplayShape(shape.profile_curve_outer.wire)
            # my_renderer.DisplayShape(shape.sweep_curve.wire)
            # my_renderer.DisplayShape(shape.geom)
            my_renderer.render()
        else:  # Assume THREEJS
            from ipywidgets.embed import embed_minimal_html

            _path = pathlib.Path("temp/index.html").resolve().absolute()

            renderer = MyRenderer(resolution)
            renderer.DisplayObj(self)
            renderer.build_display()

            os.makedirs(_path.parent, exist_ok=True)
            embed_minimal_html(_path, views=renderer._renderer, title="Pythreejs Viewer")
            start_server(addr, server_port, str(_path.parent), open_webbrowser)

    def get_render_snippet(self, view_size=None):
        """
        Return the html snippet containing threejs renderer
        """
        from ipywidgets.embed import embed_snippet

        from ada.visualize.renderer import MyRenderer

        renderer = MyRenderer()
        renderer.DisplayObj(self)
        renderer.build_display()

        return embed_snippet(renderer._renderer)

    @property
    def colour(self):
        return self._colour

    @colour.setter
    def colour(self, value):
        if type(value) is str:
            if value.lower() not in _cmap.keys():
                raise ValueError("Currently unsupported")
            self._colour = _cmap[value.lower()]
        else:
            self._colour = value

    @property
    def colour_webgl(self):
        from OCC.Display.WebGl.jupyter_renderer import format_color

        if self.colour is None:
            return None
        if self.colour[0] == -1 and self.colour[1] == -1 and self.colour[2] == -1:
            return None

        if self.colour[0] <= 1.0:
            colour = [int(x * 255) for x in self.colour]
        else:
            colour = [int(x) for x in self.colour]

        colour_formatted = format_color(*colour)
        return colour_formatted

    @property
    def penetrations(self):
        """:rtype: List[ada.Penetration]"""
        return self._penetrations

    @property
    def elem_refs(self):
        """:rtype: typing.List[ada.fem.Elem]"""
        return self._elem_refs

    @elem_refs.setter
    def elem_refs(self, value):
        self._elem_refs = value

    def _repr_html_(self):
        from IPython.display import display
        from ipywidgets import HBox, VBox

        from ada.visualize.renderer import MyRenderer

        renderer = MyRenderer()

        renderer.DisplayObj(self)
        renderer.build_display()
        self._renderer = renderer
        display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))
        # renderer._reset()
        # self._renderer.Display()
        return ""
