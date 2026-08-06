"""ProceduralBuilder: the root object of a procedural cell-model compile.

This mirrors the ``Builder`` pattern used in sibling procedural-modelling
codebases: **one object owns the whole model** — the parsed document, the
topology cell graph, the structural blueprint, the placed equipment, the wired
systems and the design ruleset — and every child reaches back to it through an
injected ``.procedural`` reference:

    blueprint.procedural   -> ProceduralBuilder     (set when the structure is built)
    cell_graph.procedural  -> ProceduralBuilder
    face                   -> face.parent_cell.cell_graph.procedural

The topology engine (:class:`ada.topology.TopologyBuilder`) keeps its own
``.builder`` back-reference for the grid + cell graph; the ProceduralBuilder is
the *procedural* root layered on top and is reached as ``.procedural``. Their
mutual link is named after each side: the root drives the engine through
``self.topology``, the engine's children reach the root through ``.procedural``.

:meth:`ProceduralBuilder.compile` runs the phases in order and returns GLB
bytes. The public :func:`ada.topo_model.compile.compile_procedural_doc` is a thin
functional wrapper over it, so nothing downstream changes.

The per-phase leaf helpers (space->box, opening cuts, equipment placement, the
routing/penetration engine driver) live in
:mod:`ada.topo_model.compile`; this module orchestrates them so the compile
reads as a short, named phase list rather than one long function.
"""

from __future__ import annotations

import pathlib
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Literal

import ada
from ada.topology import TopologyBuilder
from ada.topology.entities import TopoEquipment, TopoSpace

from .blueprint import SteelStru
from .compile import (
    _apply_girder_joints,
    _apply_openings,
    _blueprint_options,
    _build_systems,
    _cad_transform,
    _equipment_to_object,
    _require_coords,
    _space_to_box,
    _stream_tessellation,
)

__all__ = ["ProceduralBuilder"]


@dataclass
class ProceduralBuilder:
    """Root of a procedural cell-model compile.

    Construct it from a cellbuilder document and call :meth:`compile` for GLB
    bytes; or drive the phases individually (:meth:`build_structure`,
    :meth:`build_equipment`, :meth:`build_systems`, :meth:`to_glb`) and inspect
    the owned state (``blueprint``, ``cell_graph``, ``equipment_map``,
    ``systems``, ``assembly``) between them.

    ``blueprint_name`` selects the structural blueprint (``"steel_stru"`` builds
    :class:`~ada.topo_model.blueprint.SteelStru`; ``"none"`` renders the raw
    space boxes). ``lod`` selects the level of detail (``"sim"`` vs ``"detail"``,
    surfaced to the blueprint as :attr:`detail` — one home for the LOD).

    ``equipment_resolver`` maps an equipment DESCRIPTION (a catalog slug) to a
    catalog document; ``cad_scene_resolver`` maps a slug to a trimesh mesh for
    the *use CAD models* path; ``design_rules`` overrides the routing/penetration
    ruleset (defaults to the document's ``design_rules`` slug, then ``standard``).
    """

    doc: dict
    name: str = "ProceduralModel"
    blueprint_name: Literal["steel_stru", "none"] = "steel_stru"
    lod: Literal["sim", "detail"] = "sim"
    equipment_resolver: Callable | None = None
    cad_scene_resolver: Callable | None = None
    design_rules: object | None = None

    # Parsed from ``doc`` in __post_init__.
    spaces: list[TopoSpace] = field(init=False, default_factory=list)
    equipments: list[TopoEquipment] = field(init=False, default_factory=list)

    # Built up across the compile phases (None/empty until their phase runs).
    topology: TopologyBuilder | None = field(init=False, default=None)
    blueprint: SteelStru | None = field(init=False, default=None)
    assembly: ada.Assembly | None = field(init=False, default=None)
    equipment_map: dict[str, ada.Equipment] = field(init=False, default_factory=dict)
    systems: list[ada.Part] = field(init=False, default_factory=list)
    _cad_placements: list[tuple] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if self.design_rules is None:
            from .design_rulesets import resolve_design_rules

            self.design_rules = resolve_design_rules(self.doc.get("design_rules"))
        self.spaces = [TopoSpace(**s) for s in self.doc.get("spaces", [])]
        self.equipments = [TopoEquipment(**e) for e in self.doc.get("equipments", [])]
        if not self.spaces:
            raise ValueError("document has no spaces to compile")

    # --- convenience views onto the owned state ----------------------------
    @property
    def cell_graph(self):
        """The topology cell graph — ``None`` until :meth:`build_structure` runs,
        or when ``blueprint_name='none'`` (no structural blueprint is built)."""
        return self.topology.cell_graph if self.topology is not None else None

    @property
    def detail(self) -> bool:
        """Whether this is the detail-level build (``lod == "detail"``). The
        blueprint reads its own detail flag from here, so the LOD lives in one
        place on the root."""
        return self.lod == "detail"

    # --- phases -------------------------------------------------------------
    def compile(self) -> bytes:
        """Run every phase in order and return the model as GLB bytes."""
        self.build_structure()
        self.build_equipment()
        self.build_systems()
        return self.to_glb()

    def build_structure(self) -> None:
        """Spaces -> ``PrimBox`` es -> topology + structural blueprint -> assembly.

        Wires the ``.procedural`` root reference onto the blueprint and cell
        graph, then cuts the document's openings into the built plates and (in
        detail mode) models the I-girder joints. With ``blueprint_name='none'``
        the raw space boxes are wrapped in a ``Spaces`` part and no topology is
        built."""
        boxes = [_space_to_box(s) for s in self.spaces]

        if self.blueprint_name != "steel_stru":
            self.assembly = ada.Assembly(self.name) / (ada.Part("Spaces") / boxes)
            return

        self.blueprint = SteelStru(**_blueprint_options(self.doc))
        self.topology = TopologyBuilder.from_prim_boxes(boxes, blueprint=self.blueprint)
        # Root back-references: the blueprint (and any GraphFace, via
        # face.parent_cell.cell_graph.procedural) can now reach the whole model.
        self.blueprint.procedural = self
        self.topology.cell_graph.procedural = self
        self.topology.build()
        self.assembly = self.topology.get_output_assembly(self.name)

        # Negative-volume openings cut the built wall/floor plates and add their
        # door/window reinforcement framing (no-op when the doc has no openings).
        _apply_openings(self.blueprint, self.assembly, self.spaces, self.doc.get("openings", []))
        # Detail mode upgrades each I-girder to I-girder intersection into a
        # modelled joint (gusset plate + weld beads); sim mode is untouched.
        if self.detail:
            _apply_girder_joints(self.assembly)

    def build_equipment(self) -> None:
        """Place each equipment entity into the assembly under an ``Equipment``
        part, collecting the :class:`ada.Equipment` instances (the ones with
        ports) the systems will wire to.

        An equipment whose catalog slug resolves to a linked CAD asset (and the
        document opted into ``equipment_cad``) is built without its placeholder
        box body; the real CAD mesh is recorded for splicing in :meth:`to_glb`."""
        if not self.equipments:
            return

        from .equipment import apply_equipment_rotation

        use_cad = bool(self.doc.get("equipment_cad")) and self.cad_scene_resolver is not None
        objects: list = []
        for e in self.equipments:
            slug = (e.DESCRIPTION or "").strip()
            cad_mesh = self.cad_scene_resolver(slug) if (use_cad and slug) else None
            if cad_mesh is not None:
                from .equipment import build_equipment_from_catalog

                _require_coords(e, ("X", "Y", "Z", "LX", "LY", "LZ"))
                origin = (e.X + e.LX / 2, e.Y + e.LY / 2, e.Z)
                catalog_doc = self.equipment_resolver(slug) if self.equipment_resolver is not None else None
                obj = build_equipment_from_catalog(
                    e.NAME, origin, catalog_doc or {}, lx=e.LX, ly=e.LY, lz=e.LZ, add_body=False
                )
                # The box body is omitted (CAD splices in), but the ports still
                # rotate so routing meets the spun CAD geometry at the right face.
                apply_equipment_rotation(obj, *e.rotation_deg())
                obj._topo_rotation_deg = e.rotation_deg()  # rotated footprint for occupancy/clash
                self._cad_placements.append((cad_mesh, _cad_transform(e, cad_mesh)))
                objects.append(obj)
            else:
                objects.append(_equipment_to_object(e, self.equipment_resolver))

        for obj in objects:
            if isinstance(obj, ada.Equipment):
                self.equipment_map[obj.name] = obj
        self.assembly.add_part(ada.Part("Equipment") / objects)

    def build_systems(self) -> None:
        """Wire each system's ports, route the runs over the model grid and model
        the penetrations where a run crosses a built wall/deck; add the resulting
        ``Systems`` (and ``Penetrations``) parts to the assembly. No-op when the
        document declares no systems."""
        self.systems = _build_systems(
            self.doc, self.equipment_map, self.spaces, self.cell_graph, self.design_rules
        )
        for part in self.systems:
            self.assembly.add_part(part)

    def to_glb(self) -> bytes:
        """Tessellate the assembled model to GLB bytes.

        Renders through the NGEOM stream so the analytic swept duct/cable-tray
        runs tessellate upright along their curve; splices any recorded CAD
        meshes into the scene at their footprint transform."""
        with _stream_tessellation():
            if self._cad_placements:
                scene = self.assembly.to_trimesh_scene()
                for mesh, transform in self._cad_placements:
                    scene.add_geometry(mesh, transform=transform)
                exported = scene.export(file_type="glb")
                return exported if isinstance(exported, bytes) else bytes(exported)

            with tempfile.TemporaryDirectory(prefix="procedural_glb_") as tmp:
                glb_path = pathlib.Path(tmp) / "model.glb"
                self.assembly.to_gltf(glb_path)
                return glb_path.read_bytes()
