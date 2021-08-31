import logging
import os
import pathlib

from ada.config import Settings as _Settings
from ada.core.constants import color_map as _cmap
from ada.visualize.renderer import MyRenderer


class Backend:
    def __init__(self, name, guid=None, metadata=None, units="m", parent=None, ifc_settings=None, ifc_elem=None):
        self.name = name
        self.parent = parent
        self._ifc_settings = ifc_settings
        from ada.ifc.utils import create_guid

        self.guid = create_guid() if guid is None else guid
        units = units.lower()
        if units not in _Settings.valid_units:
            raise ValueError(f'Unit type "{units}"')
        self._units = units
        self._metadata = metadata if metadata is not None else dict(props=dict())
        self._ifc_elem = ifc_elem
        # TODO: Currently not able to keep and edit imported ifc_elem objects
        self._ifc_elem = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if _Settings.convert_bad_names:
            logging.debug("Converting bad name")
            value = value.replace("/", "_").replace("=", "")
            if str.isnumeric(value[0]):
                value = "ADA_" + value

        if "/" in value:
            logging.debug(f'Character "/" found in {value}')

        self._name = value.strip()

    @property
    def guid(self):
        return self._guid

    @guid.setter
    def guid(self, value):
        if value is None:
            raise ValueError("guid cannot be None")
        self._guid = value

    @property
    def parent(self):
        """

        :return:
        :rtype: ada.Part
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def metadata(self):
        return self._metadata

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        raise NotImplementedError("Assigning units is not yet represented for this object")

    @staticmethod
    def _unit_conversion(ori_unit, value):
        if value == "m" and ori_unit == "mm":
            scale_factor = 0.001
        elif value == "mm" and ori_unit == "m":
            scale_factor = 1000
        else:
            raise ValueError(f'Unrecognized unit conversion from "{ori_unit}" to "{value}"')
        return scale_factor

    @property
    def ifc_settings(self):
        if self._ifc_settings is None:
            import ifcopenshell.geom

            ifc_settings = ifcopenshell.geom.settings()
            ifc_settings.set(ifc_settings.USE_PYTHON_OPENCASCADE, True)
            ifc_settings.set(ifc_settings.SEW_SHELLS, True)
            ifc_settings.set(ifc_settings.WELD_VERTICES, True)
            ifc_settings.set(ifc_settings.INCLUDE_CURVES, True)
            ifc_settings.set(ifc_settings.USE_WORLD_COORDS, True)
            ifc_settings.set(ifc_settings.VALIDATE_QUANTITIES, True)
            self._ifc_settings = ifc_settings
        return self._ifc_settings

    @ifc_settings.setter
    def ifc_settings(self, value):
        self._ifc_settings = value

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem

    def get_assembly(self):
        """

        :return:
        :rtype: ada.Assembly
        """
        from ada import Assembly

        parent = self
        still_looking = True
        max_levels = 10
        a = 0
        while still_looking is True:
            if issubclass(type(parent), Assembly) is True:
                still_looking = False
            if parent.parent is None:
                break
            a += 1
            parent = parent.parent
            if a > max_levels:
                logging.info(f"Max levels reached. {self} is highest level element")
                break
        return parent

    def _generate_ifc_elem(self):
        raise NotImplementedError("")

    def remove(self):
        """
        Remove this element/part from assembly/part
        """
        from ada import Beam, Part, Shape

        if self.parent is None:
            logging.error(f"Unable to delete {self.name} as it does not have a parent")
            return

        # if self._ifc_elem is not None:
        #     a = self.parent.get_assembly()
        # f = a.ifc_file
        # This returns results in a failure error
        # f.remove(self.ifc_elem)

        if type(self) is Part:
            self.parent.parts.pop(self.name)
        elif issubclass(type(self), Shape):
            self.parent.shapes.pop(self.parent.shapes.index(self))
        elif type(self) is Beam:
            self.parent.beams.remove(self)
        else:
            raise NotImplementedError()


class BackendGeom(Backend):
    """
    The shared backend of all physical components (Beam, Plate) or aggregate of components (Part, Assembly).

    """

    _renderer = None

    def __init__(self, name, guid=None, metadata=None, units="m", parent=None, colour=None, ifc_elem=None):
        super().__init__(name, guid, metadata, units, parent, ifc_elem=ifc_elem)
        self._penetrations = []
        self.colour = colour

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

    def to_stp(self, destination_file, geom_repr="solid", schema="AP242", silent=False, fuse_piping=False):
        from ada.occ.writer import StepExporter

        step_export = StepExporter(schema)
        step_export.add_to_step_writer(self, geom_repr, fuse_piping=fuse_piping)
        step_export.write_to_file(destination_file, silent)

    def render_locally(
        self, addr="localhost", server_port=8080, open_webbrowser=False, render_engine="threejs", resolution=(1800, 900)
    ):
        from OCC.Display.WebGl.simple_server import start_server

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
        return self._penetrations

    def _repr_html_(self):
        from IPython.display import display
        from ipywidgets import HBox, VBox

        renderer = MyRenderer()

        renderer.DisplayObj(self)
        renderer.build_display()
        self._renderer = renderer
        display(HBox([VBox([HBox(renderer._controls), renderer._renderer]), renderer.html]))
        # renderer._reset()
        # self._renderer.Display()
        return ""
