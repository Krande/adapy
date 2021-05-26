import logging
import uuid
from itertools import chain
from random import randint

import numpy as np
from OCC.Core.Tesselator import ShapeTesselator
from OCC.Display.WebGl.jupyter_renderer import (
    NORMAL,
    BoundingBox,
    JupyterRenderer,
    _explode,
    _flatten,
    format_color,
)
from pythreejs import (
    BufferAttribute,
    BufferGeometry,
    LineBasicMaterial,
    LineMaterial,
    LineSegments,
    LineSegments2,
    LineSegmentsGeometry,
    Mesh,
    Points,
    PointsMaterial,
)

__all__ = ["MyRenderer", "SectionRenderer"]


class MyRenderer(JupyterRenderer):
    """
    An inherited class of the PythonOCC JupyterRenderer with only slight modifications

    :param size:
    :param compute_normals_mode:
    :param default_shape_color:
    :param default_edge_color:
    :param default_vertex_color:
    :param pick_color:
    :param background_color:
    """

    def __init__(
        self,
        size=(640, 480),
        compute_normals_mode=NORMAL.SERVER_SIDE,
        default_shape_color=format_color(166, 166, 166),  # light grey
        default_edge_color=format_color(32, 32, 32),  # dark grey
        default_vertex_color=format_color(8, 8, 8),  # darker grey
        pick_color=format_color(232, 176, 36),  # orange
        background_color="white",
    ):
        super().__init__(
            size,
            compute_normals_mode,
            default_shape_color,
            default_edge_color,
            default_vertex_color,
            pick_color,
            background_color,
        )
        from ipywidgets import Dropdown

        self._toggle_geom_visibility_button = self.create_button(
            "Geom", "Toggle Geom Visibility", False, self.toggle_all_geom_visibility
        )
        self._toggle_mesh_visibility_button = self.create_button(
            "Mesh", "Toggle Mesh Visibility", False, self.toggle_mesh_visibility
        )

        self._controls.pop(0)
        self._controls.pop(0)

        fem_sets = ["None"]
        self._fem_sets_opts = Dropdown(options=fem_sets, value=fem_sets[0], tooltip="Select a set", disabled=False)
        self._fem_sets_opts.observe(self._on_changed_fem_set, "value")

        self._controls.insert(0, self._toggle_geom_visibility_button)
        self._controls.insert(1, self._toggle_mesh_visibility_button)
        self._controls.pop(-1)
        self._controls.pop(-1)
        self._controls.append(self._fem_sets_opts)
        # self._controls.append(p1)
        # self._controls.append(p2)
        self._refs = dict()
        self._fem_refs = dict()

    def visible_check(self, obj, obj_type="geom"):
        from ada import Beam, Part, Plate

        if obj.name not in self._refs.keys():
            raise ValueError(f'object "{obj.name}" not found')
        adaobj = self._refs[obj.name]

        if obj_type == "geom" and type(adaobj) in (Beam, Plate):
            obj.visible = not obj.visible

        if obj_type == "mesh" and issubclass(type(adaobj), Part) is True:
            obj.visible = not obj.visible

    def toggle_all_geom_visibility(self, *kargs):
        for c in self._displayed_non_pickable_objects.children:
            self.visible_check(c)

        for c in self._displayed_pickable_objects.children:
            self.visible_check(c)

    def toggle_mesh_visibility(self, *kargs):
        for c in self._displayed_non_pickable_objects.children:
            self.visible_check(c, "mesh")

        for c in self._displayed_pickable_objects.children:
            self.visible_check(c, "mesh")

    def DisplayMesh(self, part, edge_color=None, vertex_color=None, vertex_width=2):
        """

        :param part:
        :param edge_color:
        :param vertex_color:
        :param vertex_width:
        :type part: ada.Part
        """

        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCC.Core.gp import gp_Pnt
        from OCC.Core.TopoDS import TopoDS_Compound

        # edge_color = format_color(*part.colour) if edge_color is None else edge_color
        rgb = randint(0, 255), randint(0, 255), randint(0, 255)
        edge_color = format_color(*rgb) if edge_color is None else edge_color
        vertex_color = self._default_vertex_color if vertex_color is None else vertex_color

        pmesh_id = "%s" % uuid.uuid4().hex

        BB = BRep_Builder()
        compound = TopoDS_Compound()
        BB.MakeCompound(compound)
        vertices_list = []

        def togp(n_):
            return gp_Pnt(float(n_[0]), float(n_[1]), float(n_[2]))

        for vertex in map(togp, part.fem.nodes):
            vertex_to_add = BRepBuilderAPI_MakeVertex(vertex).Shape()
            BB.Add(compound, vertex_to_add)
            vertices_list.append([vertex.X(), vertex.Y(), vertex.Z()])

        attributes = {"position": BufferAttribute(vertices_list, normalized=False)}
        mat = PointsMaterial(color=vertex_color, sizeAttenuation=False, size=vertex_width)
        geom = BufferGeometry(attributes=attributes)
        points_geom = Points(geometry=geom, material=mat, name=pmesh_id)
        lmesh_id = "%s" % uuid.uuid4().hex
        edges_nodes = list(chain.from_iterable(filter(None, [grab_nodes(el, part.fem) for el in part.fem.elements])))
        np_edge_vertices = np.array(edges_nodes, dtype=np.float32)
        np_edge_indices = np.arange(np_edge_vertices.shape[0], dtype=np.uint32)
        vertex_col = tuple([x / 255 for x in rgb])
        edge_geometry = BufferGeometry(
            attributes={
                "position": BufferAttribute(np_edge_vertices),
                "index": BufferAttribute(np_edge_indices),
                "color": BufferAttribute([vertex_col for n in np_edge_vertices]),
            }
        )
        edge_material = LineBasicMaterial(vertexColors="VertexColors", linewidth=5)

        edge_geom = LineSegments(
            geometry=edge_geometry,
            material=edge_material,
            type="LinePieces",
            name=lmesh_id,
        )
        output = [points_geom, edge_geom]

        for elem in output:
            self._shapes[elem.name] = compound
            self._refs[elem.name] = part
            self._displayed_pickable_objects.add(elem)

        self._fem_sets_opts.options = ["None"] + [
            f"{part.fem.name}.{s.name}" for s in filter(lambda x: "internal" not in x.metadata.keys(), part.fem.sets)
        ]
        self._fem_refs[part.fem.name] = (part.fem, edge_geometry)

    def DisplayAdaShape(self, shp):
        """

        :param shp:
        :type shp: ada.Shape
        :return:
        """
        res = self.DisplayShape(
            shp.geom,
            transparency=shp.transparent,
            opacity=shp.opacity,
            shape_color=shp.colour,
            render_edges=False,
        )
        for r in res:
            self._refs[r.name] = shp

    def DisplayBeam(self, beam):
        """

        :param beam:
        :type beam: ada.Beam
        """

        try:
            if "ifc_file" in beam.metadata.keys():
                from ada.core.ifc_utils import get_representation

                a = beam.parent.get_assembly()
                ifc_f = a.get_ifc_source_by_name(beam.metadata["ifc_file"])
                ifc_elem = ifc_f.by_guid(beam.guid)
                geom, color, alpha = get_representation(ifc_elem, a.ifc_settings)
            else:
                geom = beam.solid
            res = self.DisplayShape(geom, shape_color=beam.colour, render_edges=True)
        except BaseException as e:
            logging.debug(f'Unable to create solid for "{beam.name}" due to {e}')
            return None

        for r in res:
            self._refs[r.name] = beam

    def DisplayPlate(self, plate):
        """

        :param plate:
        :type plate: ada.Plate
        """

        geom = self._ifc_geom_to_shape(plate._ifc_geom) if plate._ifc_geom is not None else plate.solid
        # self.AddShapeToScene(geom)
        try:
            res = self.DisplayShape(geom, shape_color=plate.colour_webgl, opacity=0.5)
        except BaseException as e:
            logging.error(e)
            return None

        for r in res:
            self._refs[r.name] = plate

    def DisplayPipe(self, pipe):
        """

        :param pipe:
        :type pipe: ada.Pipe
        """
        # self.AddShapeToScene(geom)
        res = []

        for i, geom in enumerate(pipe.geometries):
            try:
                res += self.DisplayShape(geom, shape_color=pipe.colour_webgl, opacity=0.5)
            except BaseException as e:
                logging.error(e)
                continue

        for r in res:
            self._refs[r.name] = pipe

    def DisplayWall(self, wall):
        """

        :param wall:
        :type wall: ada.Wall
        """
        try:
            res = self.DisplayShape(wall.solid, shape_color=wall.colour, opacity=0.5)
        except BaseException as e:
            logging.error(e)
            return None

        for r in res:
            self._refs[r.name] = wall

    def DisplayAdaPart(self, part):
        """

        :return:
        :type part: ada.Part
        """
        all_shapes = [shp for p in part.get_all_subparts() for shp in p.shapes] + part.shapes
        all_beams = [bm for p in part.get_all_subparts() for bm in p.beams] + [bm for bm in part.beams]
        all_plates = [pl for p in part.get_all_subparts() for pl in p.plates] + [pl for pl in part.plates]
        all_pipes = [pipe for p in part.get_all_subparts() for pipe in p.pipes] + [pipe for pipe in part.pipes]
        all_walls = [wall for p in part.get_all_subparts() for wall in p.walls] + [wall for wall in part.walls]

        for wall in all_walls:
            for insert in wall._inserts:
                all_shapes.append(insert.shapes[0])

        list(map(self.DisplayAdaShape, all_shapes))
        list(filter(None, map(self.DisplayBeam, all_beams)))
        list(filter(None, map(self.DisplayPlate, all_plates)))
        list(filter(None, map(self.DisplayPipe, all_pipes)))
        list(filter(None, map(self.DisplayWall, all_walls)))
        list(
            map(
                self.DisplayMesh,
                filter(
                    lambda x: len(x.fem.elements) != 0,
                    part.get_all_parts_in_assembly(include_self=True),
                ),
            )
        )

    def DisplayObj(self, obj):
        from ada import Beam, Part, Pipe, Plate, Shape

        if issubclass(type(obj), Part) is True:
            self.DisplayAdaPart(obj)
        elif type(obj) is Beam:
            self.DisplayBeam(obj)
        elif type(obj) is Plate:
            self.DisplayPlate(obj)
        elif type(obj) is Pipe:
            self.DisplayPipe(obj)
        elif issubclass(type(obj), Shape):
            self.DisplayAdaShape(obj)
        else:
            raise ValueError(f'type "{type(obj)}" Not Recognized')

    def _ifc_geom_to_shape(self, ifc_geom):
        from OCC.Core import BRepTools
        from OCC.Core.TopoDS import TopoDS_Compound

        if type(ifc_geom) is TopoDS_Compound:
            geom = ifc_geom
        elif type(ifc_geom.solid) is not TopoDS_Compound:
            brep_data = ifc_geom.solid.brep_data
            ss = BRepTools.BRepTools_ShapeSet()
            ss.ReadFromString(brep_data)
            nb_shapes = ss.NbShapes()
            geom = ss.Shape(nb_shapes)
        else:
            geom = ifc_geom.solid
        return geom

    # Override and modify parent methods
    def DisplayShape(
        self,
        shp,
        shape_color=None,
        render_edges=False,
        edge_color=None,
        edge_deflection=0.05,
        vertex_color=None,
        quality=1.0,
        transparency=False,
        opacity=1.0,
        topo_level="default",
        update=False,
        selectable=True,
    ):
        """
        Displays a topods_shape in the renderer instance.

        :param shp: the TopoDS_Shape to render
        :param shape_color: the shape color, in html corm, eg '#abe000'
        :param render_edges: optional, False by default. If True, compute and dislay all
                      edges as a linear interpolation of segments.

        :param edge_color: optional, black by default. The color used for edge rendering,
                    in html form eg '#ff00ee'

        :param edge_deflection: optional, 0.05 by default
        :param vertex_color: optional
        :param quality: optional, 1.0 by default. If set to something lower than 1.0,
                      mesh will be more precise. If set to something higher than 1.0,
                      mesh will be less precise, i.e. lower numer of triangles.

        :param transparency: optional, False by default (opaque).
        :param opacity: optional, float, by default to 1 (opaque). if transparency is set to True,
                 0. is fully opaque, 1. is fully transparent.

        :param topo_level: "default" by default. The value should be either "compound", "shape", "vertex".
        :param update: optional, False by default. If True, render all the shapes.
        :param selectable: if True, can be doubleclicked from the 3d window
        """
        if edge_color is None:
            edge_color = self._default_edge_color
        if shape_color is None:
            shape_color = self._default_shape_color
        if vertex_color is None:
            vertex_color = self._default_vertex_color

        output = []  # a list of all geometries created from the shape
        # is it list of gp_Pnt ?
        from OCC.Core.gp import gp_Pnt
        from OCC.Extend.TopologyUtils import is_edge, is_wire

        if isinstance(shp, list) and isinstance(shp[0], gp_Pnt):
            result = self.AddVerticesToScene(shp, vertex_color)
            output.append(result)
        # or a 1d element such as edge or wire ?
        elif is_wire(shp) or is_edge(shp):
            result = self.AddCurveToScene(shp, edge_color, edge_deflection)
            output.append(result)
        elif topo_level != "default":
            from OCC.Extend.TopologyUtils import TopologyExplorer

            t = TopologyExplorer(shp)
            map_type_and_methods = {
                "Solid": t.solids,
                "Face": t.faces,
                "Shell": t.shells,
                "Compound": t.compounds,
                "Compsolid": t.comp_solids,
            }
            for subshape in map_type_and_methods[topo_level]():
                result = self.AddShapeToScene(
                    subshape,
                    shape_color,
                    render_edges,
                    edge_color,
                    vertex_color,
                    quality,
                    transparency,
                    opacity,
                )
                output.append(result)
        else:
            result = self.AddShapeToScene(
                shp,
                shape_color,
                render_edges,
                edge_color,
                vertex_color,
                quality,
                transparency,
                opacity,
            )
            output.append(result)

        if selectable:  # Add geometries to pickable or non pickable objects
            for elem in output:
                self._displayed_pickable_objects.add(elem)

        if update:
            self.Display()

        return output

    def AddShapeToScene(
        self,
        shp,
        shape_color=None,  # the default
        render_edges=False,
        edge_color=None,
        vertex_color=None,
        quality=1.0,
        transparency=False,
        opacity=1.0,
    ):
        # first, compute the tesselation
        tess = ShapeTesselator(shp)
        tess.Compute(compute_edges=render_edges, mesh_quality=quality, parallel=True)
        # get vertices and normals
        vertices_position = tess.GetVerticesPositionAsTuple()

        number_of_triangles = tess.ObjGetTriangleCount()
        number_of_vertices = len(vertices_position)

        # number of vertices should be a multiple of 3
        if number_of_vertices % 3 != 0:
            raise AssertionError("Wrong number of vertices")
        if number_of_triangles * 9 != number_of_vertices:
            raise AssertionError("Wrong number of triangles")

        # then we build the vertex and faces collections as numpy ndarrays
        np_vertices = np.array(vertices_position, dtype="float32").reshape(int(number_of_vertices / 3), 3)
        # Note: np_faces is just [0, 1, 2, 3, 4, 5, ...], thus arange is used
        np_faces = np.arange(np_vertices.shape[0], dtype="uint32")

        # set geometry properties
        buffer_geometry_properties = {
            "position": BufferAttribute(np_vertices),
            "index": BufferAttribute(np_faces),
        }
        if self._compute_normals_mode == NORMAL.SERVER_SIDE:
            # get the normal list, converts to a numpy ndarray. This should not raise
            # any issue, since normals have been computed by the server, and are available
            # as a list of floats
            np_normals = np.array(tess.GetNormalsAsTuple(), dtype="float32").reshape(-1, 3)
            # quick check
            if np_normals.shape != np_vertices.shape:
                raise AssertionError("Wrong number of normals/shapes")
            buffer_geometry_properties["normal"] = BufferAttribute(np_normals)

        # build a BufferGeometry instance
        shape_geometry = BufferGeometry(attributes=buffer_geometry_properties)

        # if the client has to render normals, add the related js instructions
        if self._compute_normals_mode == NORMAL.CLIENT_SIDE:
            shape_geometry.exec_three_obj_method("computeVertexNormals")

        # then a default material
        shp_material = self._material(shape_color, transparent=transparency, opacity=opacity)

        # and to the dict of shapes, to have a mapping between meshes and shapes
        mesh_id = "%s" % uuid.uuid4().hex
        self._shapes[mesh_id] = shp

        # finally create the mesh
        shape_mesh = Mesh(geometry=shape_geometry, material=shp_material, name=mesh_id)

        # edge rendering, if set to True
        if render_edges:
            edges = list(
                map(
                    lambda i_edge: [
                        tess.GetEdgeVertex(i_edge, i_vert) for i_vert in range(tess.ObjEdgeGetVertexCount(i_edge))
                    ],
                    range(tess.ObjGetEdgeCount()),
                )
            )
            edge_list = _flatten(list(map(_explode, edges)))
            lines = LineSegmentsGeometry(positions=edge_list)
            mat = LineMaterial(linewidth=1, color=edge_color)
            edge_lines = LineSegments2(lines, mat, name=mesh_id)
            self._displayed_non_pickable_objects.add(edge_lines)

        return shape_mesh

    def build_display(self, position=None, rotation=None, camera_type="orthographic"):
        """

        :param position: Camera Position
        :param rotation: Camera Rotation
        :param camera_type: Camera Type "orthographic" or "perspective"
        """
        import itertools
        import math

        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeSphere
        from OCC.Display.WebGl.jupyter_renderer import Axes, Grid, _add
        from pythreejs import (
            AmbientLight,
            CombinedCamera,
            DirectionalLight,
            OrbitControls,
            Picker,
            Renderer,
            Scene,
        )

        # Get the overall bounding box
        if self._shapes:
            self._bb = BoundingBox([self._shapes.values()])
        else:  # if nothing registered yet, create a fake bb
            self._bb = BoundingBox([[BRepPrimAPI_MakeSphere(5.0).Shape()]])
        bb_max = self._bb.max
        orbit_radius = 1.5 * self._bb._max_dist_from_center()

        # Set up camera
        camera_target = self._bb.center
        camera_position = _add(self._bb.center, self._scale([1, 1, 1] if position is None else self._scale(position)))
        camera_zoom = self._camera_initial_zoom

        self._camera = CombinedCamera(position=camera_position, width=self._size[0], height=self._size[1])
        self._camera.up = (0.0, 0.0, 1.0)
        self._camera.mode = camera_type
        self._camera_target = camera_target
        self._camera.position = camera_position

        if rotation is not None:
            self._camera.rotation = rotation
        # Set up lights in every of the 8 corners of the global bounding box
        positions = list(itertools.product(*[(-orbit_radius, orbit_radius)] * 3))
        key_lights = [DirectionalLight(color="white", position=pos, intensity=0.5) for pos in positions]
        ambient_light = AmbientLight(intensity=0.1)

        # Set up Helpers
        self.axes = Axes(bb_center=self._bb.center, length=bb_max * 1.1)
        self.horizontal_grid = Grid(bb_center=self._bb.center, maximum=bb_max, colorCenterLine="#aaa", colorGrid="#ddd")
        self.vertical_grid = Grid(bb_center=self._bb.center, maximum=bb_max, colorCenterLine="#aaa", colorGrid="#ddd")

        # Set up scene
        temp_elems = [ambient_light, self.horizontal_grid.grid, self.vertical_grid.grid, self._camera]
        environment = self.axes.axes + key_lights + temp_elems

        scene_shp = Scene(
            children=[self._displayed_pickable_objects, self._displayed_non_pickable_objects] + environment
        )

        # Set up Controllers
        self._controller = OrbitControls(controlling=self._camera, target=camera_target, target0=camera_target)
        # Update controller to instantiate camera position
        self._camera.zoom = camera_zoom
        self._update()

        # setup Picker
        self._picker = Picker(controlling=self._displayed_pickable_objects, event="dblclick")
        self._picker.observe(self.click)

        self._renderer = Renderer(
            camera=self._camera,
            background=self._background,
            background_opacity=self._background_opacity,
            scene=scene_shp,
            controls=[self._controller, self._picker],
            width=self._size[0],
            height=self._size[1],
            antialias=True,
        )

        # set rotation and position for each grid
        self.horizontal_grid.set_position((0, 0, 0))
        self.horizontal_grid.set_rotation((math.pi / 2.0, 0, 0, "XYZ"))

        self.vertical_grid.set_position((0, -bb_max, 0))

        self._savestate = (self._camera.rotation, self._controller.target)

    def click(self, value):
        """called whenever a shape  or edge is clicked"""
        obj = value.owner.object
        self.clicked_obj = obj
        if self._current_mesh_selection != obj:
            if self._current_mesh_selection is not None:
                self._current_mesh_selection.material.color = self._current_selection_material_color
                self._current_mesh_selection.material.transparent = False
                self._current_mesh_selection = None
                self._current_selection_material_color = None
                self._shp_properties_button.value = "Compute"
                self._shp_properties_button.disabled = True
                self._toggle_shp_visibility_button.disabled = True
                self._remove_shp_button.disabled = True
                self._current_shape_selection = None
            if obj is not None:
                self._shp_properties_button.disabled = False
                self._toggle_shp_visibility_button.disabled = False
                self._remove_shp_button.disabled = False
                id_clicked = obj.name  # the mesh id clicked
                self._current_mesh_selection = obj
                self._current_selection_material_color = obj.material.color
                obj.material.color = self._selection_color
                # selected part becomes transparent
                obj.material.transparent = True
                obj.material.opacity = 0.5
                # get the shape from this mesh id
                selected_shape = self._shapes[id_clicked]
                try:
                    html_value = self._click_ada_to_html(obj)
                except BaseException as e:
                    html_value = f'An error occured: "{str(e)}"'
                self.html.value = f'<div style="margin: 0 auto; width:300px;">{html_value}</div>'
                self._current_shape_selection = selected_shape
            else:
                self.html.value = "<b>Shape type:</b> None<br><b>Shape id:</b> None"

            # then execute calbacks
            for callback in self._select_callbacks:
                callback(self._current_shape_selection)

    def _click_ada_to_html(self, obj):
        """

        :param obj:
        :return:
        """
        from ada import Beam, Part, Pipe, Plate, Shape, Wall
        from ada.fem.utils import get_eldata

        def write_metadata_to_html(met_d):
            table_str = ""
            for subkey, val in met_d.items():
                if type(val) is dict:
                    table_str += f"<tr></tr><td><b>{subkey}:</b></td></tr></tr>"
                    table_str += write_metadata_to_html(val)
                else:
                    table_str += f"<tr><td>{subkey}</td><td>{val}</td></tr>"
            return table_str

        part_name = self._refs[obj.name].name if obj.name in self._refs.keys() else obj.name
        selected_part = self._refs[obj.name] if obj.name in self._refs.keys() else None
        html_value = "<b>Name:</b> {part_name}".format(part_name=part_name)
        if issubclass(type(selected_part), Part):
            assert isinstance(selected_part, Part)
            html_value += ", <b>Type:</b> FEM Mesh<br><br>"
            html_value += "<b>Mesh Info</b> <br>"
            fem_data = get_eldata(selected_part.fem)
            html_value += "{fem_data}<br>".format(
                fem_data=", ".join([f"(<b>{x}</b>: {y})" for x, y in fem_data.items()])
            )
            vol_cog_str = ", ".join([f"{x:.3f}" for x in selected_part.fem.nodes.vol_cog])
            cog = selected_part.fem.elements.calc_cog()
            cog_str = ", ".join([f"{x:.3f}" for x in cog.p])
            html_value += f"<b>Vol:</b> {cog.tot_vol:.3f} <b>COG:</b> ({vol_cog_str}) <br>"
            html_value += f"<b>Mass:</b> {cog.tot_mass:.1f}  <b>COG:</b> ({cog_str}) <br>"
            html_value += f"<b>Beam mass:</b> {cog.bm_mass:.1f}<br>"
            html_value += f"<b>Shell mass:</b> {cog.sh_mass:.1f}<br>"
            html_value += f"<b>Node mass:</b> {cog.no_mass:.1f}<br>"
            html_value += (
                "<br><br>Note! Mass calculations are calculated based on <br>beam offsets "
                "(which is not shown in the viewer yet)."
            )
        elif type(selected_part) is Beam:
            assert isinstance(selected_part, Beam)
            html_value += ", <b>Type:</b> Beam<br>"
            bm = selected_part
            html_value += f"<b>Nodes:</b> <b>n1:</b> {bm.n1.p}, <b>n2:</b> {bm.n2.p}<br>"
            html_value += f"<b>Ori:</b> <b>xv:</b> {bm.xvec}, <b>yv:</b> {bm.yvec}, <b>up:</b> {bm.up}<br>"
            html_value += f"<b>Angle:</b> {bm._angle} degrees<br>"
            html_value += f"<b>Section:</b> {bm.section.name}, <b>type:</b> {bm.section.type}<br>"
            html_value += f"<b>Material:</b> {bm.material.name}<br>"
        elif type(selected_part) is Plate:
            html_value += ", <b>Type:</b> Plate<br>"
        elif type(selected_part) is Shape:
            assert isinstance(selected_part, Shape)
            html_value += ", <b>Type:</b> Shape<br>"
            table_str = f'<div style="height:{self._size[1]}px;overflow:auto;line-height:1.0">'
            table_str += '<font size="2px" face="Arial" >'
            table_str += '<table style="width:100%;border: 1px solid black;"><tr><th>Key</th><th>Value</th></tr>'
            table_str += write_metadata_to_html(selected_part.metadata)
            table_str += "</table></font></div>"
            html_value += table_str
        elif type(selected_part) is Pipe:
            html_value += ", <b>Type:</b> Pipe<br>"
        elif type(selected_part) is Wall:
            html_value += ", <b>Type:</b> Wall<br>"
        else:
            html_value += f'<b>Type:</b> Object type "{type(selected_part)}" not recognized by ADA<br>'

        return html_value

    def _on_changed_fem_set(self, p):
        indata = p["new"]
        tmp_data = indata.split(".")
        pref = tmp_data[0]
        setref = tmp_data[1]
        fem = self._fem_refs[pref][0]
        edge_geom = self._fem_refs[pref][1]
        edges_nodes = list(chain.from_iterable(filter(None, [grab_nodes(el, fem, True) for el in fem.elements])))
        dark_grey = (0.66, 0.66, 0.66)
        color_array = np.array([dark_grey for x in edge_geom.attributes["color"].array], dtype="float32")

        color = (1, 0, 0)
        if setref in fem.elsets.keys():
            fem_set = fem.elsets[setref]
            set_edges_nodes = list(
                chain.from_iterable(filter(None, [grab_nodes(el, fem, True) for el in fem_set.members]))
            )

            res1 = [locate(edges_nodes, i) for i in set_edges_nodes]
            set_edges_indices = chain.from_iterable(res1)
            for i in set_edges_indices:
                color_array[i] = color
        elif setref in fem.nsets.keys():
            print(f'Set "{setref}" is a node set (which is not yet supported)')
        else:
            logging.error(f'Unrecognized set "{setref}". Not belonging to node or elements')
        edge_geom.attributes["color"].array = color_array

    def highlight_elem(self, elem_id, fem_name):
        """

        :param elem_id: Can be int or list of ints
        :param fem_name:
        :return:
        """
        fem = self._fem_refs[fem_name][0]
        edge_geom = self._fem_refs[fem_name][1]
        if type(elem_id) is int:
            el = fem.elements.from_id(elem_id)
            elem_nodes = grab_nodes(el, fem, True)
        elif type(elem_id) in (tuple, list):
            elem_nodes = list(
                chain.from_iterable(filter(None, [grab_nodes(fem.elements.from_id(el), fem, True) for el in elem_id]))
            )
        else:
            raise ValueError(f'Unrecognized type "{type(elem_id)}"')

        edges_nodes = list(chain.from_iterable(filter(None, [grab_nodes(el, fem, True) for el in fem.elements])))
        res1 = [locate(edges_nodes, i) for i in elem_nodes]
        set_edges_indices = chain.from_iterable(res1)
        dark_grey = (0.66, 0.66, 0.66)
        color_array = np.array([dark_grey for x in edge_geom.attributes["color"].array], dtype="float32")
        color = (1, 0, 0)
        for i in set_edges_indices:
            color_array[i] = color
        edge_geom.attributes["color"].array = color_array


class SectionRenderer:
    """
    Basically just a test to see if it can serve a purpose for visualizing section properties in Jupyter Notebooks

    """

    def display(self, sec):
        """

        :type sec: ada.Section
        """
        from IPython.display import display
        from ipywidgets import HTML, HBox

        from ada.core.utils import easy_plotly
        from ada.sections import SectionCat

        # testb = Button(
        #     description="plot",
        #     button_style="",  # 'success', 'info', 'warning', 'danger' or ''
        # )
        #
        # center = Button(
        #     description="plot",
        #     button_style="",  # 'success', 'info', 'warning', 'danger' or ''
        # )
        html = HTML("<b>Section Properties</b></br></br>")
        outer_curve, inner_curve, disconnected = sec.cross_sec(True)

        def get_data(curve):
            x = []
            y = []
            for edge in curve + [curve[0]]:
                x.append(edge[0])
                y.append(edge[1])
            return x, y

        xrange, yrange = None, None
        plot_data = dict()

        if outer_curve is not None and type(outer_curve) is not float:
            outer = get_data(outer_curve)
            plot_data["outer"] = outer
            max_dim = max(max(outer[0]), max(outer[1]))
            min_dim = min(min(outer[0]), min(outer[1]))
            xrange = [min_dim, max_dim]
            yrange = [min_dim, max_dim]
        if inner_curve is not None:
            inner = get_data(inner_curve)
            plot_data["inner"] = inner

        sp = sec.properties
        sp.calculate()
        for sec_prop in [
            ("Ax", sp.Ax),
            ("Ix", sp.Ix),
            ("Iy", sp.Iy),
            ("Iz", sp.Iz),
            ("Iyz", sp.Iyz),
            ("Wxmin", sp.Wxmin),
            ("Wymin", sp.Wymin),
            ("Wzmin", sp.Wzmin),
            ("Sy", sp.Sy),
            ("Sz", sp.Sz),
            ("Shary", sp.Shary),
            ("Sharz", sp.Sharz),
            ("Shceny", sp.Scheny),
            ("Schenz", sp.Schenz),
        ]:
            res = sec_prop[1]
            if res is not None:
                html.value += f"<b>{sec_prop[0]}:</b> {sec_prop[1]:.4E}<br>"
            else:
                html.value += f"<b>{sec_prop[0]}:</b> Prop calc not defined yet<br>"

        # controls = []
        shapes = None
        if sec.type in SectionCat.circular:
            xrange = [-sec.r * 1.1, sec.r * 1.1]
            yrange = xrange
            shapes = [
                # unfilled circle
                dict(
                    type="circle",
                    xref="x",
                    yref="y",
                    x0=0,
                    y0=0,
                    x1=sec.r,
                    y1=0,
                    line_color="LightSeaGreen",
                )
            ]
        elif sec.type in SectionCat.tubular:
            xrange = [-sec.r * 1.1, sec.r * 1.1]
            yrange = xrange
            shapes = [
                dict(
                    type="circle",
                    xref="x",
                    yref="y",
                    x0=-sec.r,
                    y0=-sec.r,
                    x1=sec.r,
                    y1=sec.r,
                    line_color="LightSeaGreen",
                ),
                dict(
                    type="circle",
                    xref="x",
                    yref="y",
                    x0=-sec.r + sec.wt,
                    y0=-sec.r + sec.wt,
                    x1=sec.r - sec.wt,
                    y1=sec.r - sec.wt,
                    line_color="LightSeaGreen",
                ),
            ]

        fig = easy_plotly(
            f'ADA Section: "{sec.name}", Type: "{sec.type}"',
            plot_data,
            xrange=xrange,
            yrange=yrange,
            shapes=shapes,
            return_widget=True,
        )
        fig["layout"]["yaxis"]["scaleanchor"] = "x"

        display(HBox([fig, html]))

        # display(widgets.VBox([widgets.HBox([testb]), center, self._fig]))


def grab_nodes(el, fem, return_ids=False):
    """

    :param el:
    :param fem:
    :param return_ids:
    :type el: ada.fem.Elem
    :type fem: ada.fem.FEM
    """
    if el.shape.edges_seq is None:
        return None
    if return_ids:
        return [i for i in [el.nodes[e].id for ed_seq in el.shape.edges_seq for e in ed_seq]]
    else:
        return [fem.nodes.from_id(i).p for i in [el.nodes[e].id for ed_seq in el.shape.edges_seq for e in ed_seq]]


def locate(data_set, i):
    return [index for index, value in enumerate(data_set) if value == i]
