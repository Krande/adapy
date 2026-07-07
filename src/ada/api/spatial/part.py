from __future__ import annotations

import io
import os
import pathlib
from itertools import chain
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, Iterable, Literal

from ada.api.beams.base_bm import Beam
from ada.api.beams.beam_tapered import BeamTapered
from ada.api.containers import Beams, Connections, Materials, Nodes, Plates, Sections
from ada.api.groups import Group
from ada.api.nodes import Node
from ada.api.piping import Pipe
from ada.api.plates import PlateCurved
from ada.api.presentation_layers import PresentationLayers
from ada.api.primitives import PrimBox, PrimCyl, PrimExtrude, PrimRevolve, Shape
from ada.api.spatial.eq_types import EquipRepr
from ada.api.transforms import Placement
from ada.base.changes import ChangeAction
from ada.base.ifc_types import SpatialTypes
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.base.units import Units
from ada.comms.fb_wrap_model_gen import FileObjectDC, FilePurposeDC, FileTypeDC
from ada.config import Config, logger
from ada.fem.concept.base import ConceptFEM
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.render_params import RenderParams
from ada.visit.scene_converter import SceneConverter

if TYPE_CHECKING:
    import trimesh
    from PIL.Image import Image

    from ada import (  # Placement,
        FEM,
        Boolean,
        Instance,
        Material,
        Plate,
        Point,
        Section,
        Wall,
        Weld,
    )
    from ada.api.connections import JointBase
    from ada.api.mass import MassPoint
    from ada.cadit.ifc.store import IfcStore
    from ada.fem.containers import COG
    from ada.fem.meshing import GmshOptions
    from ada.visit.rendering.camera import Camera


class Part(BackendGeom):
    """A Part superclass design to host all relevant information for cad and FEM modelling."""

    IFC_CLASSES = SpatialTypes

    def __init__(
        self,
        name,
        color=None,
        placement=None,
        fem: FEM = None,
        metadata=None,
        parent=None,
        units: Units = Units.M,
        guid=None,
        ifc_store: IfcStore = None,
        ifc_class: SpatialTypes = SpatialTypes.IfcBuildingStorey,
    ):
        from ada import FEM

        super().__init__(
            name, guid=guid, metadata=metadata, units=units, parent=parent, ifc_store=ifc_store, placement=placement
        )
        self._nodes = Nodes(parent=self)
        self._beams = Beams(parent=self)
        self._plates = Plates(parent=self)
        self._pipes = list()
        self._walls = list()
        self._connections = Connections(parent=self)
        self._materials = Materials(parent=self)
        self._sections = Sections(parent=self)
        self._colour = color
        self._masses = []
        self._instances: dict[Any, Instance] = dict()
        self._shapes = []
        self._welds = []
        self._welds_for_cache: dict[int, list[Weld]] = {}
        self._parts = dict()
        self._groups: dict[str, Group] = dict()
        self._ifc_class = ifc_class

        if fem is not None:
            fem.parent = self

        self._presentation_layers = PresentationLayers()

        # FEM related properties
        from ada.fem.concept.base import ConceptFEM

        self.fem = FEM(name + "-1", parent=self) if fem is None else fem
        self._concept_fem = ConceptFEM(parent_part=self)

    def add_beam(self, beam: Beam, add_to_layer: str = None) -> Beam | BeamTapered:
        if beam.units != self.units:
            beam.units = self.units
        beam.parent = self

        mat = self.add_material(beam.material)
        if mat != beam.material:
            beam.material = mat

        sec = self.add_section(beam.section)
        if sec != beam.section:
            beam.section = sec

        if isinstance(beam, BeamTapered):
            tap = self.add_section(beam.taper)
            if tap != beam.taper:
                beam.taper = tap

        old_node = self.nodes.add(beam.n1)
        if old_node != beam.n1:
            beam.n1 = old_node

        old_node = self.nodes.add(beam.n2)
        if old_node != beam.n2:
            beam.n2 = old_node

        beam.change_type = beam.change_type.ADDED
        self.beams.add(beam)

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(beam, add_to_layer)

        return beam

    def add_plate(self, plate: Plate | PlateCurved, add_to_layer: str = None) -> Plate | PlateCurved:
        if plate.units != self.units:
            plate.units = self.units

        plate.parent = self

        mat = self.add_material(plate.material)
        if mat is not None:
            plate.material = mat

        for n in plate.nodes:
            self.nodes.add(n)

        plate.change_type = plate.change_type.ADDED
        self._plates.add(plate)

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(plate, add_to_layer)

        return plate

    def add_pipe(self, pipe: Pipe, add_to_layer: str = None) -> Pipe:
        if pipe.units != self.units:
            pipe.units = self.units
        pipe.parent = self

        mat = self.add_material(pipe.material)
        if mat != pipe.material:
            pipe.material = mat
            mat.refs.append(pipe)

        sec = self.add_section(pipe.section)
        if sec != pipe.section:
            pipe.section = sec
            sec.refs.append(pipe)

        for seg in pipe.segments:
            mat = self.add_material(seg.material)
            if mat != seg.material:
                seg.material = mat
                mat.refs.append(seg)

            sec = self.add_section(seg.section)
            if sec != seg.section:
                seg.section = sec
                sec.refs.append(seg)

        pipe.change_type = pipe.change_type.ADDED
        self.pipes.append(pipe)

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(pipe, add_to_layer)

        return pipe

    def add_wall(self, wall: Wall) -> Wall:
        if wall.units != self.units:
            wall.units = self.units
        wall.parent = self
        self._walls.append(wall)
        return wall

    def add_shape(self, shape: Shape, change_type: ChangeAction = ChangeAction.ADDED) -> Shape:
        if shape.units != self.units:
            logger.info(f'shape "{shape}" has different units. changing from "{shape.units}" to "{self.units}"')
            shape.units = self.units
        shape.parent = self

        mat = self.add_material(shape.material)
        if mat != shape.material:
            shape.material = mat

        shape.change_type = change_type
        self._shapes.append(shape)
        return shape

    def add_part(self, part: Part, overwrite: bool = False, add_to_layer: str = None) -> Part:
        if issubclass(type(part), Part) is False:
            raise ValueError("Added Part must be a subclass or instance of Part")

        if part.units != self.units:
            part.units = self.units
        part.parent = self

        if part.name in self._parts.keys() and overwrite is False:
            raise ValueError(f'Part name "{part.name}" already exists. Pass "overwrite=True" to replace existing part.')

        self._parts[part.name] = part
        try:
            part._on_import()
        except NotImplementedError:
            logger.debug(f'Part "{part}" has not defined its "on_import()" method')

        part.change_type = part.change_type.ADDED
        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(part, add_to_layer)

        return part

    def add_joint(self, joint: JointBase) -> JointBase:
        """
        This method takes a Joint element containing two intersecting beams. It will check with the existing
        list of joints to see whether or not it is part of a larger more complex joint. It usese primarily
        two criteria.

        Criteria 1: If both elements are in an existing joint already, it will u

        Criteria 2: If the intersecting point coincides within a specified tolerance (currently 10mm)
        with an exisiting joint intersecting point. If so it will add the elements to this joint.
        If not it will create a new joint based on these two members.
        """
        if joint.units != self.units:
            joint.units = self.units
        self._connections.add(joint)
        return joint

    def add_weld(self, weld: Weld) -> Weld:
        weld.parent = self
        self._welds.append(weld)
        self._invalidate_welds_for_cache()

        return weld

    def _invalidate_welds_for_cache(self) -> None:
        for ancestor in self.get_ancestors():
            cache = getattr(ancestor, "_welds_for_cache", None)
            if cache is not None:
                cache.clear()

    def welds_for(self, member) -> list[Weld]:
        """Return every Weld at or below this Part whose members include `member`."""
        key = id(member)
        cached = self._welds_for_cache.get(key)
        if cached is not None:
            return cached
        result: list[Weld] = []
        for part in self.get_all_subparts(include_self=True):
            for weld in part._welds:
                if any(m is member for m in weld.members):
                    result.append(weld)
        self._welds_for_cache[key] = result
        return result

    def add_material(self, material: Material) -> Material:
        if material.units != self.units:
            material.units = self.units
        material.parent = self
        return self._materials.add(material)

    def add_section(self, section: Section) -> Section:
        if section.units != self.units:
            section.units = self.units
        return self._sections.add(section)

    def add_mass(self, mass: MassPoint) -> MassPoint:
        self._masses.append(mass)
        mass.parent = self

        mat = self.add_material(mass.material)
        if mat != mass.material:
            mass.material = mat

        return mass

    def add_object(self, obj: Part | Beam | Plate | Wall | Pipe | Shape | Weld | Section):
        from ada import Beam, Part, Pipe, Plate, Section, Shape, Wall, Weld

        if isinstance(obj, Beam):
            return self.add_beam(obj)
        elif isinstance(obj, (Plate, PlateCurved)):
            # ``add_plate`` accepts both Plate and PlateCurved; this
            # makes ``part /= [Plate(...), PlateCurved(...)]`` work
            # for callers that build mixed plate lists (e.g. lofting
            # corner-transition faces as curved BSpline plates while
            # keeping flat side panels as standard Plates).
            return self.add_plate(obj)
        elif isinstance(obj, Pipe):
            return self.add_pipe(obj)
        elif issubclass(type(obj), Part):
            return self.add_part(obj)
        elif issubclass(type(obj), Shape):
            return self.add_shape(obj)
        elif isinstance(obj, Wall):
            return self.add_wall(obj)
        elif isinstance(obj, Weld):
            return self.add_weld(obj)
        elif isinstance(obj, Section):
            return self.add_section(obj)
        else:
            raise NotImplementedError(f'"{type(obj)}" is not yet supported for smart append')

    def add_sections_in_batch(self, secs: Iterable[Section]) -> dict[Section, Section]:
        """
        Add each unique section exactly once.  Returns a map
        original_section -> container_section.
        """
        unique_secs: dict[str, Section] = {}
        for sec in secs:
            unique_secs.setdefault(sec.guid, sec)

        mapping: dict[Section, Section] = {}
        for orig in unique_secs.values():
            new = self.sections.add(orig)
            mapping[orig] = new
        return mapping

    def add_materials_in_batch(self, mats: Iterable[Material]) -> dict[Material, Material]:
        """
        Add each unique material exactly once.  Returns a map
        original_material -> container_material.
        """
        unique_mats: dict[str, Material] = {}
        for m in mats:
            unique_mats.setdefault(m.guid, m)

        mapping: dict[Material, Material] = {}
        for orig in unique_mats.values():
            new = self.materials.add(orig)
            mapping[orig] = new
        return mapping

    def add_objects_in_batch(self, objects: Iterable[Beam | Plate], add_to_layer: str = None) -> list[Beam | Plate]:
        """
        Batch-add beams and plates. Returns the list of added (or existing) objects.
        Only supports Beam/BeamTapered and Plate for now.
        """
        from ada.api.beams import Beam, BeamTapered
        from ada.api.plates.base_pl import Plate

        objs = list(objects)

        # 1) Gather all sections & tapers
        all_secs = []
        for o in objs:
            if isinstance(o, Beam):
                all_secs.append(o.section)
                if isinstance(o, BeamTapered):
                    all_secs.append(o.taper)

        sec_map = self.add_sections_in_batch(all_secs)

        # 2) Gather all materials
        all_mats = []
        for o in objs:
            mat = o.material
            if mat is not None:
                all_mats.append(mat)
        mat_map = self.add_materials_in_batch(all_mats)

        # 3) Now one pass to attach & insert
        results = []
        units = self.units
        nodes = self.nodes
        beams_col = self.beams
        plates_col = self._plates
        get_asm = self.get_assembly
        to_layer_beams = []
        to_layer_plates = []

        for o in objs:
            if isinstance(o, Beam):
                beam = o
                # units & parent
                if beam.units != units:
                    beam.units = units
                beam.parent = self

                # rewire section & taper
                beam.section = sec_map[beam.section]
                if isinstance(beam, BeamTapered):
                    beam.taper = sec_map[beam.taper]

                # rewire material
                if beam.material:
                    beam.material = mat_map[beam.material]

                # merge nodes
                old = nodes.add(beam.n1)
                if old is not beam.n1:
                    beam.n1 = old
                old = nodes.add(beam.n2)
                if old is not beam.n2:
                    beam.n2 = old

                beam.change_type = beam.change_type.ADDED
                beams_col.add(beam)
                if add_to_layer:
                    to_layer_beams.append(beam)
                results.append(beam)

            elif isinstance(o, Plate):
                plate = o
                if plate.units != units:
                    plate.units = units
                plate.parent = self

                # rewire material
                if plate.material:
                    plate.material = mat_map[plate.material]

                # merge nodes
                for n in plate.nodes:
                    nodes.add(n)

                plate.change_type = plate.change_type.ADDED
                plates_col.add(plate)
                if add_to_layer:
                    to_layer_plates.append(plate)
                results.append(plate)

            else:
                raise NotImplementedError(f"Cannot batch-add {type(o)}")

        # 4) single get_assembly + layer adds
        if add_to_layer:
            asm = get_asm()
            for b in to_layer_beams:
                asm.presentation_layers.add_object(b, add_to_layer)
            for p in to_layer_plates:
                asm.presentation_layers.add_object(p, add_to_layer)

        return results

    def add_boolean(
        self,
        boolean: Boolean | PrimExtrude | PrimRevolve | PrimCyl | PrimBox,
        add_pen_to_subparts=True,
        add_to_layer: str = None,
    ) -> Boolean:
        from ada import Boolean

        def create_pen(pen_):
            if isinstance(pen_, (PrimExtrude, PrimRevolve, PrimCyl, PrimBox)):
                return Boolean(pen_, parent=self)
            return pen_

        for bm in self.beams:
            bm.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for pl in self.plates:
            pl.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for shp in self.shapes:
            shp.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for pipe in self.pipes:
            for seg in pipe.segments:
                seg.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for wall in self.walls:
            wall.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        if add_pen_to_subparts:
            for p in self.get_all_subparts():
                p.add_boolean(boolean, False, add_to_layer=add_to_layer)

        return boolean

    def add_instance(self, element, placement: Placement):
        from ada import Instance

        if element not in self._instances.keys():
            self._instances[element] = Instance(element)
        self._instances[element].placements.append(placement)

    def add_group(self, name, set_members: list[Part | Beam | Plate | Wall | Pipe | Shape]) -> Group:
        exist_group = self.groups.get(name)
        if exist_group is None:
            self.groups[name] = Group(name, set_members, parent=self, change_type=ChangeAction.ADDED)
        else:
            logger.info(f'Appending set "{name}"')
            for mem in set_members:
                if mem not in exist_group.members:
                    exist_group.members.append(mem)

            if exist_group.change_type != ChangeAction.ADDED:
                exist_group.change_type = ChangeAction.MODIFIED

        return self.groups[name]

    def add_elements_from_ifc(self, ifc_file_path: os.PathLike | str, data_only=False):
        from ada import Assembly, Beam, Pipe, Plate, Shape, Wall

        a = Assembly("temp")
        a.read_ifc(ifc_file_path, data_only=data_only)
        for sec in a.get_all_sections():
            res = self.add_section(sec)
            if res == sec:
                sec.change_type = ChangeAction.ADDED

        for mat in a.get_all_materials():
            res = self.add_material(mat)
            if res == mat:
                mat.change_type = ChangeAction.ADDED

        for obj in a.get_all_physical_objects():
            if issubclass(type(obj), Shape):
                self.add_shape(obj)
            elif isinstance(obj, Beam):
                self.add_beam(obj)
            elif isinstance(obj, Plate):
                self.add_plate(obj)
            elif isinstance(obj, Wall):
                self.add_wall(obj)
            elif isinstance(obj, Pipe):
                self.add_pipe(obj)
            else:
                raise ValueError(f"Unrecognized {type(obj)=}")

    def read_step_file(
        self,
        step_path,
        name=None,
        scale=None,
        transform=None,
        rotate=None,
        colour=None,
        opacity=1.0,
        source_units=Units.M,
        include_shells=False,
        reader: Literal["occ", "stream", "auto", "tolerant", "native"] | None = None,
        product_tree: bool = False,
    ):
        """

        :param step_path: Can be path to stp file or path to directory of step files.
        :param name: Desired name of destination Shape object
        :param scale: Scale the step content upon import
        :param transform: Transform the step content upon import
        :param rotate: Rotate step content upon import
        :param colour: Assign a specific colour upon import
        :param opacity: Assign Opacity upon import
        :param source_units: Unit of the imported STEP file. Default is 'm'
        :param reader: STEP read path. ``None`` (default) resolves from the active
            ``CadConfig.step_reader`` (``"auto"`` out of the box). "occ" reads via the
            OpenCASCADE STEPControl_Reader. "stream" uses the kernel-free streaming reader
            (constant-memory parse, yields adapy geometry directly — see
            ada.cadit.step.read.stream_reader); "auto" tries the streaming reader first and
            falls back to OCC if the file uses any entity outside its scope; "tolerant"
            reads every supported solid kernel-free and *skips* the unsupported ones (no
            whole-file OCC fallback) — best for large mixed CAD that would OOM the OCC reader.
        """
        if reader is None:
            # Resolve the default read path from the active CAD config so it's configurable
            # via CadConfig.step_reader (default "auto": constant-memory streaming + OCC
            # fallback). The streaming readers can't apply scale/transform/rotate, so when
            # those are requested fall back to the OCC reader regardless of the configured
            # default.
            if scale is not None or transform is not None or rotate is not None:
                reader = "occ"
            else:
                reader = self._resolve_step_reader()

        if reader in ("stream", "auto", "tolerant", "native"):
            if self._read_step_streaming(
                step_path,
                name,
                scale,
                transform,
                rotate,
                colour,
                opacity,
                source_units,
                reader=reader,
                product_tree=product_tree,
            ):
                return
            # auto-fallback: the file is outside the streaming reader's scope.

        if scale is None and transform is None and rotate is None:
            # Backend-neutral path: route through the active document backend's
            # OCAF reader (works under adacpp as well as pythonocc). Carries the
            # per-shape name/colour the OCAF reader recovers.
            from ada.cad.doc import active_doc_backend

            shapes = [
                (s.shape, s.color, s.name)
                for s in active_doc_backend().step_reader(step_path).iter_all_shapes(include_colors=True)
            ]
        else:
            # Scale / transform / rotate on import is still the OCC-only path
            # (gp_Trsf via extract_occ_shapes); names/colours aren't recovered.
            from ada.occ.utils import extract_occ_shapes

            shapes = [
                (shp, None, None)
                for shp in extract_occ_shapes(step_path, scale, transform, rotate, include_shells=include_shells)
            ]

        if len(shapes) > 0:
            ada_name = name if name is not None else "CAD" + str(len(self.shapes) + 1)
            for i, (shp, shp_color, shp_name) in enumerate(shapes):
                ada_shape = Shape(ada_name + "_" + str(i), shp, colour or shp_color, opacity, units=source_units)
                self.add_shape(ada_shape)

    def _read_step_streaming(
        self,
        step_path,
        name,
        scale,
        transform,
        rotate,
        colour,
        opacity,
        source_units,
        reader: Literal["stream", "auto", "tolerant", "native"],
        product_tree=False,
    ) -> bool:
        """Read a STEP file via the kernel-free streaming reader, wrapping each
        yielded adapy ``Geometry`` in a ``Shape``. Returns True on success; False
        (only for ``reader='auto'``) if the file is outside the reader's scope,
        signalling the caller to fall back to the OCC path.

        The streaming parse touches no CAD kernel, so it avoids the whole-model
        materialisation that makes ``STEPControl_Reader`` OOM on large files; the
        per-object OCC body is built lazily only when an object is tessellated.

        ``product_tree`` (default False = flat list of Shapes under this Part): when True,
        reconstruct the STEP product/assembly tree as nested ``Part``s from each solid's
        assembly path (``Geometry.instance_paths`` — root-first ``(rep_id, product_name)``
        levels; the last level is the solid itself). Same-name products under a parent are
        merged into one Part, so the result mirrors the product tree rather than every
        placed instance.
        """
        from ada.cadit.step.read.stream_reader import (
            StepStreamUnsupported,
            stream_read_step,
        )

        if scale is not None or transform is not None or rotate is not None:
            raise ValueError("reader='stream'/'auto'/'tolerant' does not support scale/transform/rotate; use 'occ'")

        # "stream": constant-memory bottom-up parse (the adapy emitter's output — the
        # large-file OOM case). "auto"/"tolerant": two-pass deferred resolution so
        # forward-referenced solids (OpenCASCADE and most other writers) read too.
        # "tolerant" additionally skips unsupported solids instead of raising.
        local_pool = reader == "stream"
        tolerant = reader == "tolerant"

        ada_name = name if name is not None else "CAD" + str(len(self.shapes) + 1)
        asm_parts: dict[tuple, Part] = {}  # name-path -> Part (merges same-name siblings)

        def _tree_parent(paths) -> Part:
            """The nested Part a solid belongs under, per its assembly path. The last path
            level is the solid itself (excluded); intermediate levels become nested Parts,
            reusing an existing same-name child rather than colliding. Falls back to this
            Part when there is no real hierarchy."""
            path = paths[0] if paths else None
            if not path or len(path) <= 1:
                return self
            parent = self
            name_path: tuple = ()
            for level in path[:-1]:  # exclude the solid's own leaf level
                pname = (
                    (level[1] if level[1] else f"asm_{level[0]}") if isinstance(level, (tuple, list)) else str(level)
                )
                name_path += (pname,)
                p = asm_parts.get(name_path)
                if p is None:
                    existing = parent._parts.get(pname)
                    p = existing if existing is not None else parent.add_part(Part(pname))
                    asm_parts[name_path] = p
                parent = p
            return parent

        from ada.config import Config

        # Lazy shape store (default on): retain each solid as its compact blob and mint
        # ShapeProxy objects that hydrate on demand, instead of holding the whole model
        # as ada.geom trees (~10x resident memory on large assemblies). Opt out via
        # ADA_CAD_LAZY_SHAPE_STORE=false.
        store = None
        if Config().cad_lazy_shape_store:
            from ada.api.shapes import ShapeProxy, ShapeStore

            store = ShapeStore(compress=Config().cad_shape_store_compress)

        def _mint_shape(shp_name, geometry) -> Shape:
            if store is None:
                return Shape(
                    shp_name, geom=geometry, color=colour or geometry.color, opacity=opacity, units=source_units
                )
            idx = store.add_geometry(geometry)
            return ShapeProxy(shp_name, store, idx, color=colour or geometry.color, opacity=opacity, units=source_units)

        new_shapes = []  # (parent_part, shape)

        # "native": adacpp's C++ NGEOM parser. "auto" also probes it first (matching
        # iter_from_step's read_solids): the native parse is faster and, with the lazy
        # store, keeps the per-solid NGEOM buffer as-is — no hydration at import at all.
        # A first-solid hydrate probe guards against files whose buffers the Python
        # decode path can't handle; those fall back to the pure-Python reader ("auto")
        # or raise ("native").
        use_native = False
        if reader in ("native", "auto"):
            from ada.cadit.step.read.native_reader import native_adacpp_step_available

            if native_adacpp_step_available():
                use_native = True
            elif reader == "native":
                raise StepStreamUnsupported("reader='native' requires the adacpp stream_step_to_ngeom entry point")

        # The native reader is solid-only; for "auto" a file carrying loose curve/geometric-set
        # roots (wireframe bodies — SAT wire bodies, evaluated alignment reference curves) would
        # silently lose them. Route such files to the lossless pure-Python reader instead so
        # "auto" keeps its no-geometry-left-behind contract. ("native" stays as asked.)
        if use_native and reader == "auto":
            from ada.cadit.step.write._solid_source import step_has_curve_set_roots

            if step_has_curve_set_roots(step_path):
                use_native = False
                tolerant = True  # the pure-Python fallback must read the curve sets, not raise

        if use_native:
            from ada.cadit.ngeom.deserialize import (
                deserialize_geometries,
                promote_closed_shell,
            )
            from ada.cadit.step.read.native_reader import native_stream_read_step_blobs
            from ada.cadit.step.write._solid_source import NATIVE_DECODE_ERRORS
            from ada.geom import Geometry

            try:
                for i, (blob, gid, color, mats, paths) in enumerate(native_stream_read_step_blobs(step_path)):
                    if i == 0:
                        deserialize_geometries(blob)  # hydrate-probe (result discarded)
                    shp_name = gid if gid not in (None, "") else f"{ada_name}_{i}"
                    if store is not None:
                        idx = store.add_blob(
                            blob, gid=shp_name, color=color, transforms=(mats or None), instance_paths=(paths or None)
                        )
                        shp = ShapeProxy(
                            shp_name, store, idx, color=colour or color, opacity=opacity, units=source_units
                        )
                    else:
                        # Eager Shapes get the ClosedShell promotion here (the lazy
                        # store applies it at hydration) — the streaming reader itself
                        # yields bare face-sets to keep the exporters' hot loop lean.
                        geometry = Geometry(
                            id=shp_name,
                            geometry=promote_closed_shell(deserialize_geometries(blob)[0][1]),
                            color=color,
                            transforms=(mats or None),
                            instance_paths=(paths or None),
                        )
                        shp = _mint_shape(shp_name, geometry)
                    parent = _tree_parent(paths) if product_tree else self
                    new_shapes.append((parent, shp))
            except (*NATIVE_DECODE_ERRORS, RuntimeError) as exc:
                if reader == "native" or new_shapes:
                    # native was forced, or solids were already committed (a mid-stream
                    # failure can't be un-yielded) — fail loudly over silent truncation.
                    raise
                logger.info("read_step_file: native STEP reader failed (%s); using the pure-Python reader", exc)
                use_native = False
            if use_native and not new_shapes and reader == "auto":
                # Native recognised no solids; the pure-Python reader supports entity
                # kinds the native one skips, so give it the file before the OCC fallback.
                use_native = False

        if not use_native:
            geom_iter = stream_read_step(step_path, local_pool=local_pool, tolerant=tolerant)
            try:
                for i, geometry in enumerate(geom_iter):
                    shp_name = str(geometry.id) if geometry.id not in (None, "") else f"{ada_name}_{i}"
                    shp = _mint_shape(shp_name, geometry)
                    parent = _tree_parent(geometry.instance_paths) if product_tree else self
                    new_shapes.append((parent, shp))
            except StepStreamUnsupported:
                if reader != "auto":
                    raise
                logger.info("read_step_file: streaming reader hit an unsupported entity; falling back to OCC reader")
                return False

        # A zero-yield on a non-empty file means the streaming reader didn't
        # recognise the structure — fall back to OCC rather than silently
        # importing nothing (auto only; stream/tolerant honour the result).
        if not new_shapes and reader == "auto":
            logger.info("read_step_file: streaming reader produced no solids; falling back to OCC reader")
            return False

        for parent, shp in new_shapes:
            parent.add_shape(shp)
        return True

    def _resolve_step_reader(self) -> str:
        """The STEP read path to use when the caller didn't pass one explicitly.

        Pulled from the owning assembly's ``CadConfig.step_reader`` so the default is
        configurable through the CAD abstraction; falls back to ``"auto"`` (constant-memory
        streaming with an OCC fallback) when there's no assembly/config in the ancestry.
        """
        cfg = getattr(self.get_assembly(), "cad_config", None)
        reader = getattr(cfg, "step_reader", None)
        if reader is None:
            return "auto"
        return reader.value if hasattr(reader, "value") else str(reader)

    def calculate_cog(self) -> COG:
        import numpy as np

        from ada import Beam, Plate, Point, Shape
        from ada.fem.containers import COG

        beams_tot_mass = 0
        plates_tot_mass = 0
        shapes_tot_mass = 0
        node_masses_tot_mass = 0

        beams_cogs = []
        plates_cogs = []
        shapes_tot_cogs = []
        node_masses_tot_cogs = []

        tot_mass = 0
        cogs = []
        for obj in self.get_all_physical_objects():
            parent = obj.parent
            if hasattr(parent, "eq_repr") and parent.eq_repr != EquipRepr.AS_IS and not issubclass(type(obj), Shape):
                continue

            if issubclass(
                type(obj), Shape
            ):  # Assuming Mass & COG is manually assigned to arbitrary shape (Mass Point is Shape)
                cogs.append(np.array(obj.cog_abs) * obj.mass)
                tot_mass += obj.mass
                # shapes only
                shapes_tot_cogs.append(np.array(obj.cog_abs) * obj.mass)
                shapes_tot_mass += obj.mass
            elif isinstance(obj, Beam):
                cog, mass = obj.get_cog_and_mass()
                cogs.append(cog * mass)
                tot_mass += mass
                # beams only
                beams_cogs.append(cog * mass)
                beams_tot_mass += mass
            elif isinstance(obj, Plate):
                cog = obj.get_cog()
                mass = obj.get_mass()
                cogs.append(cog * mass)
                tot_mass += mass
                # plates only
                plates_cogs.append(cog * mass)
                plates_tot_mass += mass
        for mass in self.fem.masses.values():
            cogs.append(mass.nodes[0].p * mass.mass)
            tot_mass += mass.mass
            # node masses only
            node_masses_tot_cogs.append(mass.nodes[0].p * mass.mass)
            node_masses_tot_mass += mass.mass

        # for el in self.fem.elements

        if not cogs:
            raise ValueError("Cannot calculate COG: no mass contributions found")

        if abs(tot_mass) < 1e-12:
            raise ValueError("Cannot calculate COG: total mass is zero")

        cog = Point(sum(cogs) / tot_mass)

        if beams_tot_mass > 0:
            beams_cog = Point(sum(beams_cogs) / beams_tot_mass)
            logger.debug(f"{self.name}: beams cog: {beams_cog} mass: {beams_tot_mass}")
        if plates_tot_mass > 0:
            plates_cog = Point(sum(plates_cogs) / plates_tot_mass)
            logger.debug(f"{self.name}: plates cog: {plates_cog} mass: {plates_tot_mass}")
        if shapes_tot_mass > 0:
            shapes_tot_cog = Point(sum(shapes_tot_cogs) / shapes_tot_mass)
            logger.debug(
                f"{self.name}: shapes cog abs (equipments and point masses): {shapes_tot_cog} mass: {shapes_tot_mass}"
            )
        if node_masses_tot_mass > 0:
            node_masses_tot_cog = Point(sum(node_masses_tot_cogs) / node_masses_tot_mass)
            logger.debug(f"{self.name}: fem node masses cog: {node_masses_tot_cog} mass: {node_masses_tot_mass}")

        tot_vol = None

        return COG(
            p=cog,
            tot_mass=tot_mass,
            tot_vol=tot_vol,
            sh_mass=shapes_tot_mass,
            bm_mass=beams_tot_mass,
            pl_mass=plates_tot_mass,
            no_mass=node_masses_tot_mass,
        )

    def create_objects_from_fem(
        self, skip_plates=False, skip_beams=False, merge=False, reconstruct_surfaces=False
    ) -> None:
        """Build Beams and Plates from the contents of the local FEM object.

        ``merge`` folds the one-object-per-element output back down by
        merging coplanar shell plates (same material + thickness) and
        colinear beams (same section + material). Best-effort: a group is
        merged only when it collapses cleanly, else its elements are kept.
        Defaults off here to keep the 1:1 element→object mapping callers
        expect; the FEM→CAD conversion path opts in (``merge_fem_objects``).

        ``reconstruct_surfaces`` (opt-in) instead recovers smooth structured
        quad panels as single curved plates (NURBS B-rep) — a large size/time
        reduction for CAD export of meshes generated from curved panels.
        Non-reconstructable elements fall back to flat plates (coplanar-merged
        when ``merge`` is on). Beams are unaffected.
        """
        from ada import Assembly
        from ada.fem.formats.utils import convert_part_objects

        if isinstance(self, Assembly):
            for p_ in self.get_all_parts_in_assembly():
                logger.info(f'Beginning conversion from fem to structural objects for "{p_.name}"')
                convert_part_objects(
                    p_, skip_plates, skip_beams, merge=merge, reconstruct_surfaces=reconstruct_surfaces
                )
        else:
            logger.info(f'Beginning conversion from fem to structural objects for "{self.name}"')
            convert_part_objects(self, skip_plates, skip_beams, merge=merge, reconstruct_surfaces=reconstruct_surfaces)
        logger.info("Conversion complete")

    def iter_objects_from_fem(
        self,
        beams: bool = True,
        plates: bool = True,
        detached: bool = True,
        mat_cache: dict | None = None,
        merge_strategy=None,
    ) -> Iterable[Beam | Plate]:
        """Lazily build concept objects from this part's FEM mesh.

        Streaming sibling of :meth:`create_objects_from_fem`: yields one object
        at a time WITHOUT materialising the full set or adding them to the
        part's containers, so a streaming exporter (e.g.
        ``Assembly.to_ifc(streaming=True)``) keeps peak memory bounded. Beams
        are yielded before plates.

        ``detached`` (default) yields transient plates carrying no material
        back-reference, so each frees as soon as the consumer drops it.

        ``merge_strategy`` selects how shells fold into plates: ``None`` (default)
        keeps the legacy 1:1 element→plate mapping; any strategy value
        (``"coplanar"``/...) sources plates from the object-free vectorized face
        engine (:func:`ada.fem.formats.mesh_faces.faces_from_fem`) and wraps each
        merged face in a single transient :class:`Plate`. This is the one place
        the merge strategy lives, so every streaming consumer (Genie XML, IFC,
        STEP) folds shells the same way. Beams are unaffected (they fold via the
        colinear pass on the object create path; the strategy is shell-only).

        ``mat_cache`` (name → :class:`Material`) lets the caller pin which
        material objects the plates reference — pass the already-consolidated
        materials so the streamed plates share the exporter's material identity
        (else a post-consolidation ``materials.add`` would mint a fresh copy).
        """
        from ada.fem.formats.utils import (
            convert_shell_elem_to_plates,
            line_elem_to_beam,
        )

        if self.fem is None:
            return
        if beams:
            for elem in self.fem.elements.lines:
                yield line_elem_to_beam(elem, self)
        if not plates:
            return
        if merge_strategy is None:
            mat_dict: dict = {} if mat_cache is None else mat_cache
            for elem in self.fem.elements.shell:
                yield from convert_shell_elem_to_plates(elem, self, mat_dict, detached=detached)
        else:
            yield from self._iter_merged_plates_from_fem(merge_strategy, detached, mat_cache)

    def _iter_merged_plates_from_fem(self, merge_strategy, detached: bool, mat_cache: dict | None, chunk: int = 2048):
        """Wrap the object-free merged :class:`FaceData` records in transient Plates.

        Tri/quad faces are buffered into bounded chunks and built through the
        vectorized :meth:`CurvePoly2d.from_fem_shells_batch` (bitwise-identical to
        the per-face ``Plate.from_fem_shell`` — the batch path escapes degenerate
        rows back to it). ``chunk`` bounds the buffered face count, keeping the
        streaming exporters' peak memory bounded; yield order matches the face
        engine's stream order exactly. Merged N-gons use the general
        ``from_3d_points`` path."""
        import numpy as np

        from ada import Plate
        from ada.api.curves import CurvePoly2d
        from ada.fem.formats.mesh_faces import faces_from_fem

        # name -> Material, resolved once from this part's shell sections so the
        # wrapped plates reference the real material object (faces carry names).
        mats: dict = dict(mat_cache) if mat_cache else {}
        for sec in self.fem.sections.shells:
            mat = getattr(sec, "material", None)
            if mat is not None and mat.name not in mats:
                mats[mat.name] = mat

        buf: list = []  # pending tri/quad FaceData, flushed as one vectorized batch

        def _flush():
            if not buf:
                return
            by_k: dict[int, list[int]] = {}
            for i, f in enumerate(buf):
                by_k.setdefault(len(f.outline), []).append(i)
            polys: list = [None] * len(buf)
            for _k, idxs in by_k.items():
                pts = np.stack([np.asarray(buf[i].outline, dtype=float) for i in idxs])
                for i, poly in zip(idxs, CurvePoly2d.from_fem_shells_batch(pts, parent=self)):
                    polys[i] = poly
            for f, poly in zip(buf, polys):
                mat = mats.get(f.material, f.material)
                if poly is None:
                    yield Plate.from_fem_shell(f.name, f.outline, f.thickness, mat=mat, parent=self, detached=detached)
                else:
                    yield Plate(f.name, poly, f.thickness, mat=mat, parent=self, detached=detached)
            buf.clear()

        for face in faces_from_fem(self.fem, merge_strategy):
            if face.geom_face is not None:
                # Analytic patch (SURFACE/PANEL: trimmed cylinder or B-spline
                # panel) → one curved-plate concept, mirroring the FEM surface
                # reconstruction path. Flush pending tri/quads first so the
                # stream order is unchanged.
                yield from _flush()
                from ada import PlateCurved
                from ada.core.guid import create_guid
                from ada.geom import Geometry

                mat = mats.get(face.material, face.material)
                yield PlateCurved(
                    face.name,
                    Geometry(create_guid(), face.geom_face, None),
                    float(face.thickness),
                    mat=mat,
                    extrude_as_solid=True,
                    parent=self,
                )
            elif len(face.outline) <= 4:
                buf.append(face)
                if len(buf) >= chunk:
                    yield from _flush()
            else:
                # Flush pending tri/quads first so the stream order is unchanged.
                yield from _flush()
                mat = mats.get(face.material, face.material)
                yield Plate.from_3d_points(face.name, face.outline, face.thickness, mat=mat, parent=self)
        yield from _flush()

    def get_part(self, name: str, search_all_parts_in_assembly=False) -> Part | None:
        """Get part by name."""
        if search_all_parts_in_assembly:
            all_parts = self.get_all_parts_in_assembly(include_self=True)
            for part in all_parts:
                if part.name == name:
                    return part
            return None

        key_map = {key.lower(): key for key in self.parts.keys()}
        return self.parts.get(key_map[name.lower()])

    def _get_by_prop(self, value: str, prop: str) -> Part | Plate | Beam | Shape | Material | Pipe | None:
        pmap = {getattr(p, prop): p for p in self.get_all_subparts() + [self]}
        result = pmap.get(value)
        if result is not None:
            return result

        for p in self.get_all_subparts() + [self]:
            for stru_cont in [p.beams, p.plates]:
                if prop == "guid":
                    res = stru_cont.from_id(value)
                else:
                    res = stru_cont.from_name(value)
                if res is not None:
                    return res

            for shp in p.shapes:
                if getattr(shp, prop) == value:
                    return shp

            for pi in p.pipes:
                if getattr(pi, prop) == value:
                    return pi
                for seg in pi.segments:
                    if getattr(seg, prop) == value:
                        return seg

            for wl in p.walls:
                if getattr(wl, prop) == value:
                    return wl

            for ms in p.masses:
                if getattr(ms, prop) == value:
                    return ms

            for wld in p.welds:
                if getattr(wld, prop) == value:
                    return wld

            for mat in p.materials:
                if getattr(mat, prop) == value:
                    return mat

        logger.debug(f'Unable to find"{value}". Check if the element type is evaluated in the algorithm')
        return None

    def get_by_guid(self, guid) -> Part | Plate | Beam | Shape | Material | Pipe | None:
        return self._get_by_prop(guid, "guid")

    def get_by_name(self, name) -> Part | Plate | Beam | Shape | Material | Pipe | None:
        """Get element of any type by its name."""
        return self._get_by_prop(name, "name")

    def get_all_materials(self, include_self=True) -> list[Material]:
        materials = []

        for part in filter(lambda x: len(x.materials) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            materials += part.materials.materials

        return materials

    def get_all_sections(self, include_self=True) -> list[Section]:
        sections = []
        for sec in filter(lambda x: len(x.sections) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            sections += sec.sections.sections
        return sections

    def consolidate_sections(self, include_self=True):
        """Moves all sections from all sub-parts to this part"""
        from ada import Beam

        new_sections = Sections(parent=self)

        for sec in self.get_all_sections(include_self=include_self):
            res = new_sections.add(sec)
            if res.guid == sec.guid:
                continue
            # ``Sections.add`` above already redirected every ``sec.refs``
            # element's section/taper pointer to ``res`` and merged them into
            # ``res.refs`` via the ``_ref_id_set`` cache (O(N) amortised, only
            # for props-matching refs). Just drop the orphaned source list — the
            # sec_map check below raises if add() left any beam unconsolidated
            # (e.g. a same-name section with mismatched properties). The old
            # per-element ``pop(index(...))`` + ``elem not in res.refs`` +
            # ``elem.section = res`` was three O(N) ops against a growing list,
            # i.e. O(N²) and the dominant Genie-XML write cost.
            sec.refs.clear()

        for part in filter(lambda x: len(x.sections) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            part.sections = Sections(parent=part)

        sec_map = {sec.guid: sec for sec in new_sections.sections}

        not_found = []
        for beam in self.get_all_physical_objects(by_type=Beam):
            if isinstance(beam, BeamTapered) and beam.taper.guid not in sec_map.keys():
                not_found.append((beam, "taper", beam.taper))
            if beam.section.guid not in sec_map.keys():
                not_found.append((beam, "section", beam.section))

        if len(not_found) > 0:
            raise ValueError(f"The following beam sections are not consolidated {not_found}")

        self.sections = new_sections

        return self.sections.sections

    def consolidate_materials(self, include_self=True):
        from ada import Beam, Pipe, PipeSegElbow, PipeSegStraight, Plate
        from ada.fem import FemSection

        # Copy all materials assigned to fem section objects up to their
        # parent parts. FEM-section materials are heavily shared — a ship
        # model has tens of thousands of sections referencing a handful of
        # Material objects — so dedup by object identity and call
        # ``Materials.add`` once per distinct material rather than once per
        # section. The per-section call was the dominant cost of large
        # FEM → IFC / XML exports: every call re-scanned the target
        # material's (growing, tens-of-thousands-long) ``refs`` list,
        # turning consolidation into an O(sections × refs) blow-up
        # (~7.4 billion id() calls, ~11 min on Ship1T1.FEM with 66k
        # sections sharing 2 materials). One add per distinct material is
        # identical in effect — repeat adds of an already-registered
        # material are no-ops.
        for part in filter(lambda x: not x.fem.is_empty(), self.get_all_parts_in_assembly(include_self=include_self)):
            consolidated_by_id = {}
            for sec in part.fem.sections:
                mat = sec.material
                ext_mat = consolidated_by_id.get(id(mat))
                if ext_mat is None:
                    ext_mat = part.materials.add(mat)
                    consolidated_by_id[id(mat)] = ext_mat
                sec.material = ext_mat

        num_elem_changed = 0
        new_materials = Materials(parent=self)
        _reassignable = (Beam, Plate, FemSection, PipeSegStraight, PipeSegElbow, Pipe)
        for mat in self.get_all_materials(include_self=include_self):
            res = new_materials.add(mat)
            if res.guid == mat.guid:
                continue
            # ``Materials.add`` above already merged ``mat.refs`` into
            # ``res.refs`` via the ``_ref_id_set`` cache (O(N) amortised).
            # All the outer loop needs to do is flip the source
            # elements' ``.material`` pointer at ``res`` and clear the
            # now-orphaned source list. The pre-fix loop used
            # ``mat.refs.pop(mat.refs.index(elem))`` (two O(N) ops per
            # element) plus ``elem not in res.refs`` (O(N) list
            # membership) — O(N²) overall, and the dominant cost in
            # large FEM → IFC / XML conversions where ``res.refs``
            # grew into the tens of thousands.
            refs_snapshot = list(mat.refs)
            mat.refs.clear()
            for elem in refs_snapshot:
                if isinstance(elem, _reassignable) or issubclass(type(elem), Shape):
                    elem.material = res
                    num_elem_changed += 1
                else:
                    raise NotImplementedError(f"Not yet support section {type(elem)=}")

        for part in filter(lambda x: len(x.materials) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            part.materials = Materials(parent=part)

        for i, mat in enumerate(new_materials.materials, start=1):
            mat.id = i

        self.materials = new_materials

        return self.materials.materials

    def get_all_parts_in_assembly(self, include_self=False, by_type=None) -> list[Part]:
        parent = self.get_assembly()
        list_of_ps = []
        self._flatten_list_of_subparts(parent, list_of_ps)
        if include_self:
            list_of_ps += [self]

        if by_type is not None:
            return list(filter(lambda x: issubclass(type(x), by_type), list_of_ps))

        return list_of_ps

    def get_all_subparts(self, include_self=False) -> list[Part]:
        list_of_parts = [] if include_self is False else [self]
        self._flatten_list_of_subparts(self, list_of_parts)
        return list_of_parts

    def get_all_physical_objects(
        self,
        sub_elements_only=False,
        by_type=None,
        filter_by_guids: list[str] = None,
        pipe_to_segments=False,
        by_metadata: dict = None,
    ) -> Iterable[Beam | BeamTapered | Plate | Wall | Pipe | Shape | MassPoint]:
        physical_objects = []
        if sub_elements_only:
            iter_parts = iter([self])
        else:
            iter_parts = iter(self.get_all_subparts(include_self=True))

        for p in iter_parts:
            if pipe_to_segments:
                segments = chain.from_iterable([pipe.segments for pipe in p.pipes])
                all_as_iterable = chain(p.plates, p.beams, p.shapes, segments, p.walls, p.masses)
            else:
                all_as_iterable = chain(p.plates, p.beams, p.shapes, p.pipes, p.walls, p.masses)
            physical_objects.append(all_as_iterable)

        if by_type is not None:
            from ada.api.shapes import ShapeProxy

            if not isinstance(by_type, (list, tuple)):
                by_type = (by_type,)

            def _match_type(x, _by=tuple(by_type)):
                # Exact-type filter, but a lazy ShapeProxy counts as its public type
                # Shape (a proxy is an implementation detail of the import path, not
                # a distinct kind — Shape subclasses like PrimBox stay excluded).
                t = type(x)
                if t is ShapeProxy:
                    t = Shape
                return t in _by

            res = filter(_match_type, chain.from_iterable(physical_objects))
        elif by_metadata is not None:
            res = filter(
                lambda x: all(x.metadata.get(key) == value for key, value in by_metadata.items()),
                chain.from_iterable(physical_objects),
            )
        else:
            res = chain.from_iterable(physical_objects)

        if filter_by_guids is not None:
            res = filter(lambda x: x.guid in filter_by_guids, res)

        return res

    def get_all_welds(self) -> Iterable[Weld]:
        """Single source of truth for iterating welds across the part tree.

        Welds live in ``Part._welds`` — a container intentionally
        separate from ``get_all_physical_objects`` because the IFC /
        FEM / GXML writers can't process them (no `.material`, no
        ``solid_geom`` until the Weld.solid_geom delegation, etc.).
        The GLB pipeline composes both iterators explicitly: tessellation
        + GraphStore add welds via this method on top of the physical
        objects. Avoids scattering ``include_welds=False`` opt-outs
        across every non-GLB caller.
        """
        for p in self.get_all_subparts(include_self=True):
            yield from p._welds

    def get_graph_store(self) -> GraphStore:
        nid = 0
        root = GraphNode(self.name, nid, hash=self.guid)
        graph: dict[int, GraphNode] = {nid: root}
        hash_map: dict[str, GraphNode] = {self.guid: root}
        nid += 1
        objects = self.get_all_physical_objects(pipe_to_segments=True)
        containers = self.get_all_parts_in_assembly()

        for p in chain.from_iterable([containers, objects]):
            if p == self:
                continue
            if p.guid in hash_map.keys():
                logger.error(f"Duplicate GUID found for {p}")
                continue
            parent_node = hash_map.get(p.parent.guid)
            n = GraphNode(p.name, nid, hash=p.guid)
            nid += 1
            if parent_node is not None:
                n.parent = parent_node
                parent_node.children.append(n)
            graph[n.node_id] = n
            hash_map[p.guid] = n

        return GraphStore(root, graph, hash_map)

    def beam_clash_check(self, margins=5e-5):
        """
        For all beams in a Assembly get all beams touching or within the beam. Essentially a clash check is performed
        and it returns a dictionary of all beam ids and the touching beams. A margin to the beam volume can be included.

        :param margins: Add margins to the volume box (equal in all directions). Input is in meters. Can be negative.
        :return: A map generator for the list of beams and resulting intersecting beams
        """
        from ada.core.clash_check import basic_intersect

        all_parts = self.get_all_subparts() + [self]
        all_beams = [bm for p in all_parts for bm in p.beams]
        all_bm_containers = [p.beams for p in all_parts]

        return filter(None, [basic_intersect(bm, margins, all_bm_containers) for bm in all_beams])

    def move_all_mats_and_sec_here_from_subparts(self):
        for p in self.get_all_subparts():
            self._materials += p.materials
            self._sections += p.sections
            p._materials = Materials(parent=p)
            p._sections = Sections(parent=p)

        self.sections.merge_sections_by_properties()
        self.materials.merge_materials_by_properties()

    def move_all_nodes_here_from_subparts(self):
        for p in self.get_all_subparts():
            self._nodes += p.nodes

    def move_all_masses_here_from_subparts(self):
        for p in self.get_all_subparts():
            self._masses += p.masses

    def _flatten_list_of_subparts(self, p, list_of_parts=None):
        for value in p.parts.values():
            list_of_parts.append(value)
            self._flatten_list_of_subparts(value, list_of_parts)

    def _on_import(self):
        """A method call that will be triggered when a Part is imported into an existing Assembly/Part"""
        raise NotImplementedError()

    def copy_to(
        self,
        name: str = None,
        position: list[float] | Point = None,
        rotation_axis: Iterable[float] = None,
        rotation_angle: float = None,
        add_object_copy_suffix: bool = True,
    ) -> Part:
        """Copy the part and all its sub_parts to a new part. Optionally add translation and/or rotation to the new part"""
        from ada import Placement

        if position is None:
            position = self.placement.origin

        if name is None:
            name = self.name

        new_part = Part(name, placement=Placement(origin=position))

        for obj in self.get_all_physical_objects(sub_elements_only=True):
            if add_object_copy_suffix:
                copy_obj = obj.copy_to(name=f"{obj.name}_copy")
            else:
                copy_obj = obj.copy_to()

            new_part.add_object(copy_obj)

        for sub_part in self.parts.values():
            if add_object_copy_suffix:
                new_part.add_part(sub_part.copy_to(f"{sub_part.name}_copy"))
            else:
                new_part.add_part(sub_part.copy_to())

        if rotation_axis is not None:
            if rotation_angle is None:
                raise ValueError("To apply rotation you also need to specify a rotation angle")

            new_part.placement = new_part.placement.rotate(rotation_axis, rotation_angle)
        else:
            if rotation_angle is not None:
                raise ValueError("To apply rotation you also need to specify a rotation axis")

        return new_part

    def to_fem_obj(
        self,
        mesh_size: float,
        bm_repr: GeomRepr = GeomRepr.LINE,
        pl_repr: GeomRepr = GeomRepr.SHELL,
        shp_repr: GeomRepr = GeomRepr.SOLID,
        options: GmshOptions = None,
        silent=True,
        interactive=False,
        use_quads=False,
        use_hex=False,
        experimental_bm_splitting=True,
        experimental_pl_splitting=True,
        name=None,
        debug_mode=False,
        merge_coincident_nodes=True,
    ) -> FEM:
        from ada import Beam, Plate, Shape
        from ada.fem.elements import Mass
        from ada.fem.meshing import GmshOptions, GmshSession

        if isinstance(bm_repr, str):
            bm_repr = GeomRepr.from_str(bm_repr)
        if isinstance(pl_repr, str):
            pl_repr = GeomRepr.from_str(pl_repr)
        if isinstance(shp_repr, str):
            shp_repr = GeomRepr.from_str(shp_repr)

        options = GmshOptions(Mesh_Algorithm=8) if options is None else options
        masses: list[Shape] = []

        with GmshSession(silent=silent, options=options, debug_mode=debug_mode) as gs:
            for obj in self.get_all_physical_objects(sub_elements_only=False):
                if isinstance(obj, Beam):
                    gs.add_obj(obj, geom_repr=bm_repr, build_native_lines=False)
                elif isinstance(obj, Plate):
                    gs.add_obj(obj, geom_repr=pl_repr)
                elif issubclass(type(obj), Shape) and obj.mass is not None:
                    masses.append(obj)
                elif issubclass(type(obj), Shape):
                    gs.add_obj(obj, geom_repr=shp_repr)
                else:
                    logger.error(f'Unsupported object type "{obj}". Should be either plate or beam objects')

            if interactive is True:
                gs.open_gui()

            gs.check_model_entities()
            gs.partition_plates()
            gs.check_model_entities()
            gs.partition_beams()

            if interactive is True:
                gs.open_gui()

            gs.mesh(mesh_size, use_quads=use_quads, use_hex=use_hex)

            if interactive is True:
                gs.open_gui()

            fem = gs.get_fem(name=name if name is not None else f"{self.name}-FEM")

        for mass_shape in masses:
            cog_absolute = mass_shape.placement.get_absolute_placement().origin + mass_shape.cog
            n = fem.nodes.add(Node(cog_absolute))
            fem.add_mass(Mass(f"{mass_shape.name}_mass", [n], mass_shape.mass))

        if merge_coincident_nodes:
            n_before = len(fem.nodes)
            fem.nodes.remove_standalones()
            n_after = len(fem.nodes)
            logger.info(f"Removed {n_before - n_after} standalone nodes")

        if Config().meshing_check_hanging_nodes:
            from ada.fem.conformality import check_conformal_mesh

            check_conformal_mesh(fem, raise_on_fail=Config().meshing_raise_on_hanging_nodes)

        return fem

    def to_gltf(
        self,
        gltf_file: str | pathlib.Path | BinaryIO,
        render_override: dict[str, GeomRepr | str] = None,
        filter_by_guids=None,
        merge_meshes=True,
        stream_from_ifc=False,
        embed_object_metadata: bool = True,
        params: RenderParams = None,
        solid_beams: bool = False,
    ):
        if params is None:
            from ada.visit.render_params import FEARenderParams

            params = RenderParams(
                stream_from_ifc_store=stream_from_ifc,
                merge_meshes=merge_meshes,
                render_override=render_override,
                filter_by_guids=filter_by_guids,
                embed_object_metadata=embed_object_metadata,
                fea_params=FEARenderParams(solid_beams=solid_beams),
            )

        converter = SceneConverter(self, params)

        if isinstance(gltf_file, io.IOBase):
            # It's a file-like object
            gltf_file.write(converter.build_glb())
        else:
            # It's a path
            if isinstance(gltf_file, str):
                gltf_file = pathlib.Path(gltf_file)
            gltf_file.parent.mkdir(parents=True, exist_ok=True)

            with open(gltf_file, "wb") as f:
                f.write(converter.build_glb())

    def to_trimesh_scene(
        self,
        render_override: dict[str, GeomRepr | str] = None,
        filter_by_guids=None,
        merge_meshes=True,
        stream_from_ifc=False,
        params: RenderParams = None,
        include_ada_ext: bool = False,
    ) -> trimesh.Scene:
        """Create a Trimesh.Scene from ada.Part."""
        if params is None:
            params = RenderParams(
                stream_from_ifc_store=stream_from_ifc,
                merge_meshes=merge_meshes,
                render_override=render_override,
                filter_by_guids=filter_by_guids,
            )

        converter = SceneConverter(self, params)
        scene = converter.build_processed_scene()

        # trimesh does not automatically include gltf_extensions when creating a scene in memory.
        # You will have to export to GLTF and re-import it
        if include_ada_ext:
            if "gltf_extensions" not in scene.metadata.keys():
                scene.metadata["gltf_extensions"] = {}
            scene.metadata["gltf_extensions"]["ADA_EXT_data"] = converter.ada_ext.model_dump(mode="json")

        return scene

    def render_offscreen(
        self,
        camera: Camera | None = None,
        *,
        backend: Literal["pygfx", "chromium"] = "pygfx",
        preset: dict | None = None,
        size: tuple[int, int] = (640, 480),
    ) -> Image:
        """Render the part to a PIL Image.

        Parameters
        ----------
        camera
            Legacy pygfx camera. When supplied with ``backend="pygfx"``,
            the trimesh-scene render path is used (kept for callers that
            already pass a hand-built ``Camera``). When ``None``, both
            backends route through the embed's ``applyCameraPreset``
            math so pygfx, chromium, and the live 3D viewer all use
            identical camera setup.
        backend
            ``"pygfx"`` (default) — fast offscreen render via wgpu.
            ``"chromium"`` drives the production adapy embed in headless
            Chromium via Playwright. ``camera`` is ignored by chromium;
            pass ``preset`` to override the embed's ``CameraPreset``.
        preset
            Camera preset dict (azimuth_deg, elevation_deg, fov_deg,
            distance, margin, …). Honored by *both* backends when
            ``camera`` is None — same field names as
            ``paradoc.camera.presets.CameraPreset`` so the three
            render paths read from a single source of truth.
        size
            Viewport size (also the output PNG size at DPR=1).
        """
        # Both default-path branches roundtrip via a temp GLB then call
        # one of the two glb_to_image* helpers. That keeps the pygfx
        # and chromium framing identical (same math, same defaults).
        # Custom camera arg keeps the legacy trimesh path so existing
        # callers don't have to re-rig.
        if backend == "chromium" or (backend == "pygfx" and camera is None):
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
                tmp_path = pathlib.Path(tmp.name)
            try:
                self.to_gltf(tmp_path)
                preset_kwargs: dict = dict(preset or {})
                if backend == "chromium":
                    from ada.visit.rendering.chromium_offscreen_utils import (
                        glb_to_image_via_browser,
                    )

                    return glb_to_image_via_browser(
                        tmp_path,
                        preset=preset,
                        size=size,
                    )
                # backend == "pygfx" and no Camera arg → embed-port path.
                from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image

                # Translate paradoc-style preset keys into glb_to_image kwargs
                # (it takes the same fields, no nesting). Fall through to
                # function defaults for any key the caller didn't supply.
                allowed = {
                    "azimuth_deg",
                    "elevation_deg",
                    "fov_deg",
                    "distance",
                    "margin",
                    "z_up",
                }
                kwargs = {k: v for k, v in preset_kwargs.items() if k in allowed}
                return glb_to_image(tmp_path, size=size, **kwargs)
            finally:
                tmp_path.unlink(missing_ok=True)

        # Legacy pygfx path: trimesh scene + hand-built Camera. Kept for
        # callers that already supply a Camera; new code should drop the
        # arg and use the preset-driven path above.
        from ada.visit.rendering.pygfx_offscreen_utils import trimesh_scene_to_image

        return trimesh_scene_to_image(self.to_trimesh_scene(), camera=camera)

    def to_stp(
        self,
        destination_file,
        geom_repr: GeomRepr = GeomRepr.SOLID,
        progress_callback: Callable[
            [int, int],
            None,
        ] = None,
        geom_repr_override: dict[str, GeomRepr] = None,
        evict_solid_cache: bool = True,
        writer: str = "occ",
        schema: str = "AP242",
        fuse_fem: bool = True,
        merge_strategy=None,
    ):
        # The "stream" writer authors AP242 B-rep text directly from parametric
        # geometry without building any OCC/adacpp shapes — constant memory, so
        # it does not OOM on large FEM models the way the OCC XCAF path does. It
        # covers extruded solids only (plates, straight beams, straight pipe
        # segments); other geometry is skipped. ``fuse_fem`` streams Beam/Plate
        # straight from the FEM mesh when they aren't built (no concept-object
        # peak); ``merge_strategy`` folds shells via the shared object-free face
        # engine. See cadit.step.write.ap242_stream.
        if writer == "stream":
            from ada.cadit.step.write.ap242_stream import write_step_stream

            return write_step_stream(
                self,
                destination_file,
                schema=schema,
                progress_callback=progress_callback,
                fuse_fem=fuse_fem,
                merge_strategy=merge_strategy,
            )
        if writer != "occ":
            raise ValueError(f"unknown writer {writer!r}; expected 'occ' or 'stream'")

        from ada.cad.doc import active_doc_backend
        from ada.occ.geom.cache import invalidate
        from ada.occ.store import OCCStore

        step_writer = active_doc_backend().step_writer()

        num_shapes = len(list(self.get_all_physical_objects()))
        shape_iter = OCCStore.shape_iterator(self, geom_repr=geom_repr, render_override=geom_repr_override)
        for i, (obj, shape) in enumerate(shape_iter, start=1):
            step_writer.add_shape(shape, obj.name, rgb_color=obj.color.rgb)
            # Drop this object's cached (untransformed) OCC solid now that the
            # writer holds its own transformed copy. Otherwise the process-global
            # occ_solid_cache retains every built solid for the whole export — on
            # a 100k-solid model that doubles the geometry held in RAM (cache +
            # writer compound) and can OOM a memory-constrained worker. The cache
            # buys nothing for a one-shot export. Opt out with evict_solid_cache.
            if evict_solid_cache:
                guid = getattr(obj, "guid", None)
                if guid is not None:
                    invalidate(guid)
            if progress_callback is not None:
                progress_callback(i, num_shapes)

        step_writer.export(destination_file)

    def to_aveva_mac(
        self,
        destination_file: str | pathlib.Path | io.TextIOBase,
        beam_spec_map: dict[str, str] | Callable[[Beam], str],
        panel_spec_map: dict[str, str] | Callable[[Plate], str],
        beam_material_map: dict[str, str] | Callable[[Beam], str],
        panel_material_map: dict[str, str] | Callable[[Plate], str],
    ):
        from ada.cadit.e3d.write_mac import E3DWriter

        if isinstance(destination_file, str):
            destination_file = pathlib.Path(destination_file)

        writer = E3DWriter(
            beam_spec_map=beam_spec_map,
            panel_spec_map=panel_spec_map,
            beam_material_map=beam_material_map,
            panel_material_map=panel_material_map,
        )
        mac_str = writer.write_macro(self)
        if isinstance(destination_file, pathlib.Path) and not destination_file.parent.exists():
            destination_file.parent.mkdir(parents=True)

        if hasattr(destination_file, "write"):
            destination_file.write(mac_str)
        else:
            destination_file.write_text(mac_str, encoding="utf-8-sig")

        logger.info(f'AVEVA MAC file "{destination_file}" created')

    def _sync_ifc_backend(self, backend_file_dir, wc):
        """Handles syncing the IFC backend if enabled."""
        from ada import Assembly

        if isinstance(self, Assembly) is False:
            return None

        if isinstance(backend_file_dir, str):
            backend_file_dir = pathlib.Path(backend_file_dir)

        if backend_file_dir is None:
            backend_file_dir = pathlib.Path.cwd() / "temp"

        ifc_file = backend_file_dir / f"{self.name}.ifc"
        self.to_ifc(ifc_file)

        wc.update_file_server(FileObjectDC(self.name, FileTypeDC.IFC, FilePurposeDC.DESIGN, ifc_file))

    @property
    def parts(self) -> dict[str, Part]:
        return self._parts

    @property
    def shapes(self) -> list[Shape]:
        return self._shapes

    @shapes.setter
    def shapes(self, value: list[Shape]):
        self._shapes = value

    @property
    def beams(self) -> Beams:
        return self._beams

    @beams.setter
    def beams(self, value: Beams):
        self._beams = value

    @property
    def plates(self) -> Plates:
        return self._plates

    @plates.setter
    def plates(self, value: Plates):
        self._plates = value

    @property
    def pipes(self) -> list[Pipe]:
        return self._pipes

    @pipes.setter
    def pipes(self, value: list[Pipe]):
        self._pipes = value

    @property
    def welds(self) -> list[Weld]:
        return self._welds

    @welds.setter
    def welds(self, value: list[Weld]):
        self._welds = list(value)
        self._invalidate_welds_for_cache()

    @property
    def walls(self) -> list[Wall]:
        return self._walls

    @walls.setter
    def walls(self, value: list[Wall]):
        self._walls = value

    @property
    def nodes(self) -> Nodes:
        return self._nodes

    @property
    def fem(self) -> FEM:
        return self._fem

    @fem.setter
    def fem(self, value: FEM):
        value.parent = self
        for sec in value.sections.sections:
            sec.material = self.add_material(sec.material)
        self._fem = value

    @property
    def presentation_layers(self) -> PresentationLayers:
        return self._presentation_layers

    @presentation_layers.setter
    def presentation_layers(self, value):
        self._presentation_layers = value

    @property
    def connections(self) -> Connections:
        return self._connections

    @property
    def sections(self) -> Sections:
        return self._sections

    @property
    def masses(self) -> [MassPoint]:
        return self._masses

    @sections.setter
    def sections(self, value: Sections):
        self._sections = value

    @property
    def materials(self) -> Materials:
        return self._materials

    @materials.setter
    def materials(self, value: Materials):
        self._materials = value

    @property
    def colour(self):
        if self._colour is None:
            from random import randint

            self._colour = randint(0, 255) / 255, randint(0, 255) / 255, randint(0, 255) / 255

        return self._colour

    @colour.setter
    def colour(self, value):
        self._colour = value

    @property
    def placement(self) -> Placement:
        return self._placement

    @placement.setter
    def placement(self, value: Placement):
        self._placement = value

    @property
    def instances(self) -> dict[Any, Instance]:
        return self._instances

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        from ada import Assembly

        if isinstance(value, str):
            value = Units.from_str(value)

        if value != self._units:
            for bm in self.beams:
                bm.units = value

            for pl in self.plates:
                pl.units = value

            for pipe in self._pipes:
                pipe.units = value

            for shp in self._shapes:
                shp.units = value

            for wall in self.walls:
                wall.units = value

            for pen in self.booleans:
                pen.units = value

            for p in self.get_all_subparts():
                p.units = value

            self.sections.units = value
            self.materials.units = value
            self._units = value
            if isinstance(self, Assembly):
                f = self.ifc_store.f

                res = {x.UnitType.upper(): x for x in f.by_type("IFCSIUNIT")}
                length_unit = res.get("LENGTHUNIT", None)
                area_unit = res.get("AREAUNIT", None)
                volume_unit = res.get("VOLUMEUNIT", None)
                prefix = None if value == Units.M else "MILLI"

                if length_unit:
                    length_unit.Prefix = prefix
                if area_unit:
                    area_unit.Prefix = prefix
                if volume_unit:
                    volume_unit.Prefix = prefix

    @property
    def groups(self) -> dict[str, Group]:
        return self._groups

    @property
    def ifc_class(self) -> SpatialTypes:
        return self._ifc_class

    @property
    def concept_fem(self) -> ConceptFEM:
        """Returns the ConceptFEM object associated with this Part."""
        return self._concept_fem

    def get_all_groups_as_merged(self) -> dict[str, list[Group]]:
        from collections import defaultdict

        merged_sets_by_name = defaultdict(list)
        for p in self.get_all_parts_in_assembly(include_self=True):
            for group in p.groups.values():
                merged_sets_by_name[group.name].append(group)

        return merged_sets_by_name

    def __truediv__(self, other_object):
        from ada import Beam, Plate

        if type(other_object) in [list, tuple, set]:
            beams_and_plates = list(filter(lambda x: isinstance(x, (Beam, Plate)), other_object))
            self.add_objects_in_batch(beams_and_plates)
            not_bm_or_plates = list(filter(lambda x: not isinstance(x, (Beam, Plate)), other_object))
            for obj in not_bm_or_plates:
                self.add_object(obj)
        else:
            self.add_object(other_object)

        return self

    def __repr__(self):
        nbms = len(self.beams) + len([bm for p in self.get_all_subparts() for bm in p.beams])
        npls = len(self.plates) + len([pl for p in self.get_all_subparts() for pl in p.plates])
        npipes = len(self.pipes) + len([pl for p in self.get_all_subparts() for pl in p.pipes])
        nshps = len(self.shapes) + len([shp for p in self.get_all_subparts() for shp in p.shapes])
        nels = len(self.fem.elements) + len([el for p in self.get_all_subparts() for el in p.fem.elements])
        nnodes = len(self.fem.nodes) + len([no for p in self.get_all_subparts() for no in p.fem.nodes])
        return (
            f'Part("{self.name}": Beams: {nbms}, Plates: {npls}, '
            f"Pipes: {npipes}, Shapes: {nshps}, Elements: {nels}, Nodes: {nnodes})"
        )
