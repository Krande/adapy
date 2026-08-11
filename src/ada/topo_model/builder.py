"""ProceduralBuilder: the root object of a procedural cell-model compile.

This mirrors the ``Builder`` pattern used in sibling procedural-modelling
codebases: **one object owns the whole model** — the spaces, equipment, systems,
openings, structural blueprint, topology cell graph and design ruleset — and
every child reaches back to it through an injected ``.procedural`` reference:

    blueprint.procedural   -> ProceduralBuilder     (set when the structure is built)
    cell_graph.procedural  -> ProceduralBuilder
    face                   -> face.parent_cell.cell_graph.procedural

The topology engine (:class:`ada.topology.TopologyBuilder`) keeps its own
``.builder`` back-reference for the grid + cell graph; the ProceduralBuilder is
the *procedural* root layered on top and is reached as ``.procedural``. Their
mutual link is named after each side: the root drives the engine through
``self.topology``, the engine's children reach the root through ``.procedural``.

The builder is **object-first**: construct it from explicit
:mod:`ada.topology.entities` value objects (``TopoSpace``/``TopoEquipment``/
``TopoSystem``/``TopoOpening``) rather than a hand-written dict, so the input is
validated pydantic rather than a fragile mapping. The document, JSON and Excel
formats are supported through the :meth:`from_dict` / :meth:`from_json` /
:meth:`from_excel` constructors (each parses/validates into those objects), with
:meth:`to_doc` / :meth:`to_excel` for the inverse.

:meth:`compile` runs the phases in order and returns GLB bytes; the public
:func:`ada.topo_model.compile.compile_procedural_doc` is a thin functional
wrapper over :meth:`from_dict` + :meth:`compile`.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Literal

import ada
from ada.config import logger
from ada.topology import TopologyBuilder
from ada.topology.entities import (
    TopoEquipment,
    TopoLoftMember,
    TopoOpening,
    TopoSpace,
    TopoStructure,
    TopoSystem,
)

from .blueprint import SteelStru
from .engines import DEFAULT_ENGINE_SLUG, PROCEDURAL_SCHEMA_VERSION, EngineBinding
from .compile import (
    _BLUEPRINT_OPTION_KEYS,
    _apply_openings,
    _build_systems,
    _cad_transform,
    _equipment_to_object,
    _require_coords,
    _space_to_box,
    _stream_tessellation,
)

__all__ = ["ProceduralBuilder"]

BlueprintName = Literal["steel_stru", "none"]
Lod = Literal["sim", "detail"]


@dataclass
class ProceduralBuilder:
    """Root of a procedural cell-model compile.

    Construct it from explicit entity objects and call :meth:`compile` for GLB
    bytes; or drive the phases individually (:meth:`build_structure`,
    :meth:`build_equipment`, :meth:`build_systems`, :meth:`to_glb`) and inspect
    the owned state (``blueprint``, ``cell_graph``, ``equipment_map``,
    ``systems_parts``, ``assembly``) between them.

    ``spaces`` are required (the model boxes); ``equipments``/``systems``/
    ``openings`` default empty. ``blueprint_name`` selects the structural
    blueprint (``"steel_stru"`` builds :class:`~ada.topo_model.blueprint.SteelStru`;
    ``"none"`` renders the raw space boxes). ``blueprint_options`` are the
    whitelisted structural options (reinforce walls, enclosed cells, plate
    thickness, …). ``lod`` selects the level of detail (surfaced to the blueprint
    as :attr:`detail`). ``design_rules`` is either a named ruleset slug, a
    :class:`~ada.topology.design_rules.DesignRules`, or ``None`` (defaults to the
    standard ruleset). ``equipment_resolver`` maps an equipment DESCRIPTION (a
    catalog slug) to a catalog doc; ``cad_scene_resolver`` maps a slug to a
    trimesh mesh for the *use CAD models* path.
    """

    spaces: list[TopoSpace]
    equipments: list[TopoEquipment] = field(default_factory=list)
    systems: list[TopoSystem] = field(default_factory=list)
    openings: list[TopoOpening] = field(default_factory=list)
    # Optional multi-structure grouping: 1..N topology models placed at their
    # origins. Empty = a single implicit structure (spaces build as-is, today's
    # behaviour). When present, spaces/openings are grouped by STRUCTURE_NAME and
    # each structure is built + placed; equipment and systems stay a SINGLE shared
    # layer (not duplicated per structure).
    structures: list[TopoStructure] = field(default_factory=list)
    # Optional swept ("lofted") members: each is an ordered stack of section
    # profiles that decomposes into inter-station BAND cells + renders as plates.
    # Additive to ``spaces`` — a model may carry boxes, loft members, or both.
    loft_members: list[TopoLoftMember] = field(default_factory=list)
    name: str = "ProceduralModel"
    # Routing/identity header (see ada.topo_model.engines.EngineBinding): ``engine``
    # is the slug that compiles this model (default = the built-in adapy engine; a
    # registered engine's slug routes the compile to its capability worker), and
    # ``schema_version`` is the doc-schema this model was authored against.
    engine: str = DEFAULT_ENGINE_SLUG
    schema_version: str = PROCEDURAL_SCHEMA_VERSION
    blueprint_name: BlueprintName = "steel_stru"
    blueprint_options: dict = field(default_factory=dict)
    lod: Lod = "sim"
    # Selected detailing engine (fabrication-detail stage that adds connection
    # joints AFTER the structural build). ``None``/``"none"`` = no detailing —
    # byte-identical to today. ``"adapy-default"`` runs the built-in in-process
    # detailing engine (:func:`ada.topo_model.detailing.detail`). A compile-time
    # choice, NOT part of the document. See :meth:`_effective_detailing`.
    detailing: str | None = None
    detailing_options: dict = field(default_factory=dict)
    design_rules: object | None = None
    equipment_cad: bool = False
    no_go_walls: bool = False
    equipment_resolver: Callable | None = None
    cad_scene_resolver: Callable | None = None

    # Retained slug for round-tripping a named ruleset back to doc/excel; None
    # when ``design_rules`` was passed as a concrete (unnamed) DesignRules.
    design_rules_slug: str | None = field(init=False, default=None)

    # Built up across the compile phases (None/empty until their phase runs).
    topology: TopologyBuilder | None = field(init=False, default=None)
    # Per-structure topology engines (multi-structure builds); the primary
    # (first) one is also exposed as ``topology`` for back-compat.
    topologies: dict[str, TopologyBuilder] = field(init=False, default_factory=dict)
    blueprint: SteelStru | None = field(init=False, default=None)
    # Cell graph of the inter-station band cells derived from ``loft_members``
    # (None until :meth:`build_lofts` runs, or when there are no loft members).
    loft_cell_graph: object | None = field(init=False, default=None)
    assembly: ada.Assembly | None = field(init=False, default=None)
    equipment_map: dict[str, ada.Equipment] = field(init=False, default_factory=dict)
    systems_parts: list[ada.Part] = field(init=False, default_factory=list)
    _cad_placements: list[tuple] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        if not self.spaces and not self.loft_members:
            raise ValueError("document has no spaces or loft_members to compile")
        # Coerce plain dicts (a convenience for callers that mix objects + dicts)
        # into the typed value objects, so downstream is uniformly object-based.
        self.spaces = [s if isinstance(s, TopoSpace) else TopoSpace(**s) for s in self.spaces]
        self.equipments = [e if isinstance(e, TopoEquipment) else TopoEquipment(**e) for e in self.equipments]
        self.systems = [s if isinstance(s, TopoSystem) else TopoSystem(**s) for s in self.systems]
        self.openings = [o if isinstance(o, TopoOpening) else TopoOpening(**o) for o in self.openings]
        self.structures = [s if isinstance(s, TopoStructure) else TopoStructure(**s) for s in self.structures]
        self.loft_members = [m if isinstance(m, TopoLoftMember) else TopoLoftMember(**m) for m in self.loft_members]
        # Whitelist the structural options so an unknown key can't reach SteelStru.
        self.blueprint_options = {k: v for k, v in dict(self.blueprint_options).items() if k in _BLUEPRINT_OPTION_KEYS}
        # Resolve a named/absent ruleset to a DesignRules; keep the slug for round-trip.
        rules = self.design_rules
        if rules is None or isinstance(rules, str):
            from .design_rulesets import resolve_design_rules

            self.design_rules_slug = rules
            self.design_rules = resolve_design_rules(rules)

    # --- alternate constructors --------------------------------------------
    @classmethod
    def from_dict(
        cls,
        doc: dict,
        *,
        name: str = "ProceduralModel",
        blueprint_name: BlueprintName = "steel_stru",
        lod: Lod = "sim",
        detailing: str | None = None,
        detailing_options: dict | None = None,
        equipment_resolver: Callable | None = None,
        cad_scene_resolver: Callable | None = None,
        design_rules: object | None = None,
    ) -> "ProceduralBuilder":
        """Build from a procedural *document* (the viewer's commit format): a
        mapping of ``spaces``/``equipments``/``openings``/``systems`` entity
        dumps plus the ``blueprint``/``design_rules``/``equipment_cad`` scalars.
        All dict parsing (and its validation) happens here, once."""
        return cls(
            spaces=[TopoSpace(**s) for s in doc.get("spaces", [])],
            equipments=[TopoEquipment(**e) for e in doc.get("equipments", [])],
            systems=[TopoSystem(**s) for s in doc.get("systems", [])],
            openings=[TopoOpening(**o) for o in doc.get("openings", [])],
            structures=[TopoStructure(**s) for s in doc.get("structures", [])],
            loft_members=[TopoLoftMember(**m) for m in doc.get("loft_members", [])],
            name=name,
            # engine + schema_version are persisted in the doc (routing header).
            engine=doc.get("engine") or DEFAULT_ENGINE_SLUG,
            schema_version=doc.get("schema_version") or PROCEDURAL_SCHEMA_VERSION,
            blueprint_name=blueprint_name,
            blueprint_options=doc.get("blueprint") or {},
            lod=lod,
            # Detailing is a compile-time choice threaded from the worker/API, not
            # persisted on the document; a doc value is a tolerated fallback.
            detailing=detailing if detailing is not None else doc.get("detailing"),
            detailing_options=detailing_options if detailing_options is not None else (doc.get("detailing_options") or {}),
            # An explicit design_rules argument wins; else the doc's named slug.
            design_rules=design_rules if design_rules is not None else doc.get("design_rules"),
            equipment_cad=bool(doc.get("equipment_cad")),
            no_go_walls=bool(doc.get("no_go_walls")),
            equipment_resolver=equipment_resolver,
            cad_scene_resolver=cad_scene_resolver,
        )

    @classmethod
    def from_json(cls, source: str | pathlib.Path, **kwargs) -> "ProceduralBuilder":
        """Build from a JSON document — a path to a ``.json`` file or a JSON
        string. Extra keyword arguments forward to :meth:`from_dict`."""
        text = source
        p = pathlib.Path(source) if isinstance(source, (str, pathlib.Path)) else None
        if p is not None and p.suffix.lower() == ".json" and p.exists():
            text = p.read_text()
        return cls.from_dict(json.loads(text), **kwargs)

    @classmethod
    def from_excel(cls, path: str | pathlib.Path, **kwargs) -> "ProceduralBuilder":
        """Build from a multi-sheet Excel workbook (``Spaces``/``Equipments``/
        ``Openings``/``Systems`` + a vertical ``Model`` sheet carrying the name,
        blueprint, blueprint options, design ruleset and toggles). Keyword
        arguments override the workbook's ``Model`` sheet values."""
        from .excel import read_procedural_excel

        data = read_procedural_excel(path, multi=True)
        meta = data["meta"]
        # Warn (don't fail) if the workbook was authored against an incompatible
        # major doc-schema — the reader may silently miss/misread newer columns.
        if not EngineBinding(schema_version=meta.SCHEMA_VERSION).is_compatible():
            logger.warning(
                "procedural workbook schema_version %s is incompatible with this build's %s",
                meta.SCHEMA_VERSION,
                PROCEDURAL_SCHEMA_VERSION,
            )
        return cls(
            spaces=data["spaces"],
            equipments=data["equipments"],
            openings=data["openings"],
            systems=data["systems"],
            structures=data.get("structures", []),
            name=kwargs.get("name", meta.NAME),
            engine=kwargs.get("engine", meta.ENGINE),
            schema_version=kwargs.get("schema_version", meta.SCHEMA_VERSION),
            blueprint_name=kwargs.get("blueprint_name", meta.BLUEPRINT),
            blueprint_options=kwargs.get("blueprint_options", meta.blueprint_options()),
            lod=kwargs.get("lod", meta.LOD),
            design_rules=kwargs.get("design_rules", meta.DESIGN_RULES),
            equipment_cad=kwargs.get("equipment_cad", meta.EQUIPMENT_CAD),
            no_go_walls=kwargs.get("no_go_walls", meta.NO_GO_WALLS),
            equipment_resolver=kwargs.get("equipment_resolver"),
            cad_scene_resolver=kwargs.get("cad_scene_resolver"),
        )

    # --- serialization back out --------------------------------------------
    def to_doc(self) -> dict:
        """The procedural document (round-trips :meth:`from_dict`). Entity objects
        are dumped in JSON mode; only a *named* ruleset (``design_rules_slug``)
        round-trips — a concrete DesignRules object is dropped."""
        # exclude_none so a re-parse (from_dict) never passes an explicit None to
        # a non-optional entity field (e.g. an opening's SPACE_NAME / a space's
        # coords) — absent means "use the default", which is None anyway.
        doc: dict = {
            # Routing/identity header — always stamped so the document is
            # self-describing (which engine compiles it, at which schema version).
            "engine": self.engine,
            "schema_version": self.schema_version,
            "spaces": [s.model_dump(mode="json", exclude_none=True) for s in self.spaces],
            "equipments": [e.model_dump(mode="json", exclude_none=True) for e in self.equipments],
            "openings": [o.model_dump(mode="json", exclude_none=True) for o in self.openings],
            "systems": [s.model_dump(mode="json", exclude_none=True) for s in self.systems],
        }
        if self.structures:
            doc["structures"] = [s.model_dump(mode="json") for s in self.structures]
        # Only stamp loft_members when present, so box-only docs are byte-identical.
        if self.loft_members:
            doc["loft_members"] = [m.model_dump(mode="json", exclude_none=True) for m in self.loft_members]
        if self.blueprint_options:
            doc["blueprint"] = dict(self.blueprint_options)
        if self.design_rules_slug is not None:
            doc["design_rules"] = self.design_rules_slug
        if self.equipment_cad:
            doc["equipment_cad"] = True
        if self.no_go_walls:
            doc["no_go_walls"] = True
        return doc

    def to_json(self, path: str | pathlib.Path | None = None, *, indent: int = 2) -> str:
        """Serialize :meth:`to_doc` to a JSON string (and write it to ``path`` when
        given)."""
        text = json.dumps(self.to_doc(), indent=indent)
        if path is not None:
            pathlib.Path(path).write_text(text)
        return text

    def to_excel(self, path: str | pathlib.Path) -> None:
        """Write the whole model to a multi-sheet Excel workbook (round-trips
        :meth:`from_excel`)."""
        from .excel import write_procedural_excel

        write_procedural_excel(self, path)

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

    def _effective_detailing(self) -> str | None:
        """The detailing engine to run during the structural build. An explicit
        ``detailing`` selection wins; absent one, ``lod=="detail"`` keeps firing
        the built-in ``adapy-default`` detailing for backward-compat (the old
        detail-mode joint pass — see the detailing-engine proposal Migration note /
        OQ-4). ``None``/``"none"`` with a ``"sim"`` LOD means no detailing, so the
        output is byte-identical to today."""
        if self.detailing and self.detailing != "none":
            return self.detailing
        if self.lod == "detail":
            return "adapy-default"
        return None

    # --- phases -------------------------------------------------------------
    def compile(self) -> bytes:
        """Run every phase in order and return the model as GLB bytes."""
        self.build_structure()
        self.build_lofts()
        self.build_equipment()
        self.build_systems()
        return self.to_glb()

    def build_structure(self) -> None:
        """Spaces -> ``PrimBox`` es -> topology + structural blueprint -> assembly.

        Wires the ``.procedural`` root reference onto the blueprint and cell
        graph, cuts openings into the built plates and (in detail mode) models the
        I-girder joints. With ``blueprint_name='none'`` the raw space boxes are
        wrapped in a ``Spaces`` part and no topology is built.

        With no :attr:`structures` this builds a single model (the common case,
        unchanged). With structures present it builds one topology model per
        structure — spaces/openings grouped by ``STRUCTURE_NAME`` — and places
        each at its origin in a combined assembly. Equipment and systems are added
        once (a single shared layer) over the primary (first) structure."""
        # Loft-only model: there is no box structure to build; open an empty
        # assembly for :meth:`build_lofts` to add the swept plates into.
        if not self.spaces:
            self.assembly = ada.Assembly(self.name)
            return
        if not self.structures:
            self.assembly, topo, bp = self._build_structure_group(self.spaces, self.openings, self.name)
            self.topology, self.blueprint = topo, bp
            if topo is not None:
                self.topologies[self.name] = topo
            return

        self.assembly = ada.Assembly(self.name)
        for st in self.structures:
            if not st.INCLUDE:
                continue
            st_spaces = [s for s in self.spaces if (s.STRUCTURE_NAME or None) == st.NAME]
            if not st_spaces:
                logger.warning("procedural: structure %r has no spaces; skipping", st.NAME)
                continue
            st_openings = [o for o in self.openings if (o.STRUCTURE_NAME or None) == st.NAME]
            sub, topo, bp = self._build_structure_group(st_spaces, st_openings, st.NAME)
            if topo is not None:
                self.topologies[st.NAME] = topo
                if self.topology is None:  # primary = first built structure
                    self.topology, self.blueprint = topo, bp
            # Place the structure's built parts at its origin.
            origin = st.origin()
            wrapper = (
                ada.Part(st.NAME)
                if origin == (0.0, 0.0, 0.0)
                else ada.Part(st.NAME, placement=ada.Placement(origin=ada.Point(*origin)))
            )
            for part in list(sub.parts.values()):
                wrapper.add_part(part)
            self.assembly.add_part(wrapper)

    def _build_structure_group(
        self, spaces: list[TopoSpace], openings: list[TopoOpening], name: str
    ) -> tuple[ada.Assembly, TopologyBuilder | None, SteelStru | None]:
        """Build one structure's topology + blueprint (+ openings + joints) from
        ``spaces``/``openings`` and return ``(assembly, topology, blueprint)``.
        ``blueprint_name='none'`` renders the raw boxes (topology/blueprint None)."""
        boxes = [_space_to_box(s) for s in spaces]
        if self.blueprint_name != "steel_stru":
            return ada.Assembly(name) / (ada.Part("Spaces") / boxes), None, None

        bp = SteelStru(**self.blueprint_options)
        topo = TopologyBuilder.from_prim_boxes(boxes, blueprint=bp)
        # Root back-references: the blueprint (and any GraphFace, via
        # face.parent_cell.cell_graph.procedural) can now reach the whole model.
        bp.procedural = self
        topo.cell_graph.procedural = self
        topo.build()
        a = topo.get_output_assembly(name)

        # Negative-volume openings cut the built wall/floor plates and add their
        # door/window reinforcement framing (no-op when there are none). exclude_none
        # so a sparsely-specified opening is not re-validated with an explicit None
        # against a non-optional field — the fields default to None when absent.
        _apply_openings(bp, a, spaces, [o.model_dump(exclude_none=True) for o in openings])
        # DETAILING stage: the selected detailing engine adds connection joints
        # (gusset/end/base plates + welds) as a Part("Joints") on the assembly,
        # right where the old girder-joint pass ran, before to_glb(). Driven off
        # the ``detailing`` option; ``lod=="detail"`` keeps firing adapy-default
        # for backward-compat. ``none`` (default sim) adds nothing.
        detailing = self._effective_detailing()
        if detailing == "adapy-default":
            from .detailing import detail as _apply_detailing

            _apply_detailing(a, self.detailing_options)
        elif detailing is not None:
            # An external (Tier-B) detailing engine is not applied in-process here
            # (that is a chained capability job — Phase 2); the structural build
            # proceeds unchanged so its GLB is still produced.
            logger.info("procedural: detailing engine %r is external; in-process stage skipped", detailing)
        return a, topo, bp

    def build_lofts(self) -> None:
        """Swept ``loft_members`` -> band-cell topology + geometry in the assembly.

        Runs only when :attr:`loft_members` are present (byte-identical no-op for
        box-only models). For the included members it (a) derives the lossless
        inter-station BAND :class:`~ada.topology.CellGraph` via
        :func:`ada.topology.io.from_section_loft` — one cell per inter-station
        band, ``Sum(stations - 1)`` in total, stored on :attr:`loft_cell_graph` for
        selection/face-picking — and then emits geometry per member:

        * A **structural** member (``SURFACE_ONLY=False``) under the ``steel_stru``
          blueprint is *framed*: the :class:`~ada.topo_model.blueprint.SteelStru`
          blueprint runs over the member's loft-derived cell graph and emits beams
          (columns/girders/stringers + floor plates), exactly as it does for box
          ``spaces`` — a loft is just another topology source, mirroring the
          pm-engine's ``SECTION_LOFT`` path. ``TopologyBuilder`` consumes the
          prebuilt cell graph directly.
        * A **skin** member (``SURFACE_ONLY=True``, or ``blueprint_name='none'``)
          lofts its placed station profiles into a part of plates
          (:func:`ada.topology.io.loft_member_to_part`); each plate is named by its
          ``loft_face_id`` so a picked plate maps back to the band-cell face, and
          ``EXCLUDE_FACES`` drops addressed plates.

        Both views share the same profiles, so the band cells sit inside the
        emitted geometry."""
        members = [m for m in self.loft_members if m.INCLUDE]
        if not members:
            return

        from ada.topology.io import from_section_loft, loft_member_to_part

        # (a) lossless cell decomposition (Sum(stations-1) band cells) over the WHOLE
        # member set — the selection/face-picking topology. Each band-cell face also
        # carries a loft-native ``loft_face_id`` (Phase 3b) for per-face addressing.
        self.loft_cell_graph = from_section_loft([m.to_loft_member() for m in members])

        # (b) route each member by how it should be built. A SURFACE_ONLY member (or
        # blueprint 'none') is a plate SKIN; otherwise its REPRESENTATION selects the
        # blueprint run over the member's loft cell graph — FRAME -> SteelStru (decked
        # framework), JACKET -> JacketStru (open tubular truss). Both blueprints go
        # through TopologyBuilder, same pipeline as box spaces.
        if self.blueprint_name == "none":
            skinned, framed, jacketed = members, [], []
        else:
            skinned = [m for m in members if m.SURFACE_ONLY]
            structural = [m for m in members if not m.SURFACE_ONLY]
            framed = [m for m in structural if m.REPRESENTATION != "JACKET"]
            jacketed = [m for m in structural if m.REPRESENTATION == "JACKET"]

        lofts_part = ada.Part("Lofts")

        # Run a blueprint over a subset's loft cell graph (a separate graph from
        # ``loft_cell_graph`` so the blueprint's builder back-refs don't touch the
        # picking topology). Root back-refs mirror ``_build_structure_group``.
        def _framed(subset, blueprint) -> None:
            from ada.topology.builder import TopologyBuilder

            cg = from_section_loft([m.to_loft_member() for m in subset])
            blueprint.procedural = self
            cg.procedural = self
            lofts_part.add_part(TopologyBuilder(blueprint=blueprint, cell_graph=cg).build())

        if framed:
            _framed(framed, SteelStru(**self.blueprint_options))
        if jacketed:
            from .jacket import JacketStru

            _framed(jacketed, JacketStru())

        for m in skinned:
            lofts_part.add_part(
                loft_member_to_part(
                    m.NAME, m.world_profiles(), thickness=m.THICKNESS, exclude_faces=m.EXCLUDE_FACES
                )
            )

        if self.assembly is None:
            self.assembly = ada.Assembly(self.name)
        self.assembly.add_part(lofts_part)

    def build_equipment(self) -> None:
        """Place each equipment entity into the assembly under an ``Equipment``
        part, collecting the :class:`ada.Equipment` instances (the ones with
        ports) the systems will wire to.

        An equipment whose catalog slug resolves to a linked CAD asset (and
        ``equipment_cad`` is on) is built without its placeholder box body; the
        real CAD mesh is recorded for splicing in :meth:`to_glb`."""
        if not self.equipments:
            return

        from .compile import equipment_space_offset
        from .equipment import apply_equipment_rotation

        # Cell lookup so an equipment associated with a cell (SPACE_NAME, and not
        # GLOBAL_COORDS) is seated at that cell's origin — the default placement,
        # matching the entity's own get_origin() and the sibling engine. Keyed by
        # (STRUCTURE_NAME, NAME) with a bare-NAME fallback for single-structure docs.
        space_lookup: dict = {}
        for s in self.spaces:
            space_lookup[(getattr(s, "STRUCTURE_NAME", None), s.NAME)] = s
            space_lookup.setdefault(s.NAME, s)

        def _space_for(e):
            return space_lookup.get((getattr(e, "STRUCTURE_NAME", None), e.SPACE_NAME)) or space_lookup.get(
                e.SPACE_NAME
            )

        use_cad = self.equipment_cad and self.cad_scene_resolver is not None
        objects: list = []
        for e in self.equipments:
            offset = equipment_space_offset(e, _space_for(e))
            slug = (e.DESCRIPTION or "").strip()
            cad_mesh = self.cad_scene_resolver(slug) if (use_cad and slug) else None
            if cad_mesh is not None:
                from .equipment import build_equipment_from_catalog

                _require_coords(e, ("X", "Y", "Z", "LX", "LY", "LZ"))
                origin = (e.X + offset[0] + e.LX / 2, e.Y + offset[1] + e.LY / 2, e.Z + offset[2])
                catalog_doc = self.equipment_resolver(slug) if self.equipment_resolver is not None else None
                obj = build_equipment_from_catalog(
                    e.NAME, origin, catalog_doc or {}, lx=e.LX, ly=e.LY, lz=e.LZ, add_body=False
                )
                # The box body is omitted (CAD splices in), but the ports still
                # rotate so routing meets the spun CAD geometry at the right face.
                apply_equipment_rotation(obj, *e.rotation_deg())
                obj._topo_rotation_deg = e.rotation_deg()  # rotated footprint for occupancy/clash
                self._cad_placements.append((cad_mesh, _cad_transform(e, cad_mesh, offset)))
                objects.append(obj)
            else:
                objects.append(_equipment_to_object(e, self.equipment_resolver, offset))

        for obj in objects:
            if isinstance(obj, ada.Equipment):
                self.equipment_map[obj.name] = obj
        self.assembly.add_part(ada.Part("Equipment") / objects)

    def build_systems(self) -> None:
        """Wire each system's ports, route the runs over the model grid and model
        the penetrations where a run crosses a built wall/deck; add the resulting
        ``Systems`` (and ``Penetrations``) parts to the assembly. No-op when there
        are no systems."""
        spec_doc = {
            "systems": [s.model_dump() for s in self.systems],
            "no_go_walls": self.no_go_walls,
        }
        self.systems_parts = _build_systems(
            spec_doc, self.equipment_map, self.spaces, self.cell_graph, self.design_rules
        )
        for part in self.systems_parts:
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
