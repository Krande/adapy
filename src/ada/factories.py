"""Top-level ``from_*`` factory functions (kept out of ``ada/__init__.py`` for readability).

Re-exported from ``ada`` so the public API is unchanged: ``ada.from_step(...)`` still works.

Each Assembly-returning factory accepts an optional ``cad_config`` (``ada.cad.CadConfig``) that is
attached to the returned assembly, so a downstream conversion (e.g.
``stream_step_to_glb(..., cad_config=asm.cad_config)``) uses the chosen tessellation path.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
from typing import TYPE_CHECKING, Literal

from ada.api.primitives import Shape
from ada.api.spatial import Assembly, Part
from ada.base.units import Units
from ada.config import logger

if TYPE_CHECKING:
    from collections.abc import Iterator

    import ifcopenshell

    from ada.cad import CadConfig
    from ada.fem.formats.sesam.results.read_cc import CCData
    from ada.fem.results.common import FEAResult
    from ada.geom import Geometry


def from_pickle(pickle_file: str | os.PathLike) -> Assembly:
    """Load an Assembly previously written with :meth:`Assembly.to_pickle`.

    Round-trips the parametric model so a source parsed once can be reused for many exports
    without re-reading/re-parsing it. Each call returns a fresh deep copy (downstream mutation
    of one export can't leak into another)."""
    import pickle

    with open(pathlib.Path(pickle_file), "rb") as f:
        obj = pickle.load(f)
    if not isinstance(obj, Assembly):
        raise TypeError(f"from_pickle: expected an Assembly, got {type(obj).__name__}")
    return obj


def from_ifc(
    ifc_file: os.PathLike | ifcopenshell.file,
    units=Units.M,
    name="Ada",
    cad_config: "CadConfig | None" = None,
    reader: Literal["ifcopenshell", "native"] | None = None,
) -> Assembly:
    """Create an Assembly object from an IFC file.

    ``reader="native"`` uses adacpp's pure-C++ IFC reader (no ifcopenshell/OCC) to build a
    geometry-shapes tree — pairs with ``Assembly.to_ifc(writer="native")`` for a fully native
    round-trip. Default (``ifcopenshell``) is the full typed reader (Beam/Plate/Pipe/...).
    """
    if isinstance(ifc_file, (os.PathLike, str)):
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        logger.info(f'Reading "{ifc_file.name}"')
    else:
        logger.info("Reading IFC file object")

    a = Assembly(units=units, name=name, cad_config=cad_config)
    a.read_ifc(ifc_file, reader=reader)
    return a


def from_step(
    step_file: str | pathlib.Path,
    source_units=Units.M,
    cad_config: "CadConfig | None" = None,
    name: str | None = None,
    scale: float | None = None,
    transform=None,
    rotate=None,
    colour=None,
    opacity: float = 1.0,
    include_shells: bool = False,
    reader: Literal["occ", "stream", "auto", "tolerant", "native"] | None = None,
    product_tree: bool = False,
) -> Assembly:
    """Create an Assembly object from a STEP file.

    The read path defaults to ``cad_config.step_reader`` (``StepReader.AUTO`` out of the box:
    constant-memory streaming with an OCC fallback for out-of-scope files — the most
    memory-efficient + robust choice). Pass a ``cad_config`` with a different ``step_reader`` to
    override, or set ``reader=`` to force one for this call. ``product_tree=True`` reconstructs the
    STEP assembly tree as nested Parts (default: a flat list of Shapes).
    """
    a = Assembly(cad_config=cad_config)
    a.read_step_file(
        step_file,
        name=name,
        scale=scale,
        transform=transform,
        rotate=rotate,
        colour=colour,
        opacity=opacity,
        source_units=source_units,
        include_shells=include_shells,
        reader=reader,
        product_tree=product_tree,
    )
    return a


def iter_from_step(
    step_file: str | pathlib.Path,
    *,
    reader: Literal["auto", "native", "stream", "tolerant"] = "auto",
) -> Iterator[Geometry]:
    """Stream a STEP file solid-by-solid as ``ada.geom.Geometry`` — bounded memory,
    one solid resident at a time. The streaming counterpart to :func:`from_step`
    (which materialises the whole Assembly): the per-solid foundation the kernel-free
    exporters (STEP→IFC/STEP/OBJ/STL) and the cross-format validation pass build on,
    so a multi-GB assembly never has to fit in memory.

    Each yielded ``Geometry`` carries ``id``, ``geometry`` (analytic ``ada.geom``),
    ``color``, ``transforms`` (per-instance world matrices) and ``instance_paths``
    (the STEP product/assembly breadcrumb, root-first).

    ``reader`` selects the parse path:

    * ``"auto"`` (default) — the native adacpp C++ NGEOM parser when it decodes
      cleanly, else the pure-Python stream reader for that file (lossless fallback).
    * ``"native"`` — force the adacpp C++ parser (raises if it is unavailable).
    * ``"stream"`` — the pure-Python streaming parser (bottom-up, constant memory).
    * ``"tolerant"`` — pure-Python, skipping unsupported solids instead of raising.
    """
    if reader == "auto":
        from ada.cadit.step.write._solid_source import read_solids

        yield from read_solids(step_file)
    elif reader == "native":
        from ada.cadit.step.read.native_reader import (
            native_adacpp_step_available,
            native_stream_read_step,
        )

        if not native_adacpp_step_available():
            raise RuntimeError("reader='native' requires the adacpp stream_step_to_ngeom entry point")
        yield from native_stream_read_step(step_file)
    elif reader in ("stream", "tolerant"):
        from ada.cadit.step.read.stream_reader import stream_read_step

        yield from stream_read_step(step_file, local_pool=(reader == "stream"), tolerant=(reader == "tolerant"))
    else:
        raise ValueError(f"unknown reader {reader!r}; expected auto|native|stream|tolerant")


def from_acis(
    sat_file: str | pathlib.Path,
    source_units=Units.M,
    split: bool = False,
    limit: int = None,
    cad_config: "CadConfig | None" = None,
) -> Assembly:
    """
    Create an Assembly object from an ACIS SAT file.

    Args:
        sat_file: Path to ACIS SAT file
        source_units: Units of the SAT file
        split: If True, split shells into individual AdvancedFace objects
        limit: Limit the number of geometries to export (useful for debugging)
        cad_config: Optional CAD/tessellation config attached to the returned assembly

    Returns:
        Assembly object with parsed geometry
    """
    from ada.cadit.sat.parser import AcisSatParser, AcisToAdaConverter
    from ada.geom import Geometry

    # Parse the SAT file
    parser = AcisSatParser(sat_file)
    parser.parse()

    # Convert to adapy geometry using body-based organization
    converter = AcisToAdaConverter(parser)
    bodies = converter.convert_all_bodies()

    # Create assembly
    a = Assembly(units=source_units, name="ACIS_Import", cad_config=cad_config)

    # Create a part for each body
    for body_idx, (body_name, geometries) in enumerate(bodies):
        if not geometries:
            logger.debug(f"Skipping body {body_name} - no geometries")
            continue

        # Apply limit if specified
        if limit is not None and limit > 0:
            geometries = geometries[:limit]
            logger.info(f"Limiting body {body_name} to {len(geometries)} geometries (limit={limit})")

        # Suffix part name in split mode to indicate faces
        part = Part(body_name if not split else f"{body_name}_faces")

        # When split is False: add each shell/face geometry as-is (one shape per geometry)
        # When split is True: decompose shells into individual AdvancedFace shapes
        shape_count = 0
        if not split:
            for i, geom in enumerate(geometries):
                logger.debug(f"Body {body_name}: geometry {i} type={type(geom).__name__}")
                shape = Shape(f"shape{i}", Geometry(i, geom))
                part.add_shape(shape)
                shape_count += 1
        else:
            import ada.geom.surfaces as geo_su

            for i, geom in enumerate(geometries):
                logger.debug(f"[split] Body {body_name}: geometry {i} type={type(geom).__name__}")
                # If geometry is a ClosedShell/OpenShell, split into faces
                if isinstance(geom, (geo_su.ClosedShell, geo_su.OpenShell)):
                    faces = getattr(geom, "cfs_faces", [])

                    # Apply limit to faces if specified
                    if limit is not None and limit > 0:
                        remaining_limit = limit - shape_count
                        if remaining_limit <= 0:
                            break
                        faces = faces[:remaining_limit]

                    for j, face in enumerate(faces):
                        # face is expected to be geo_su.AdvancedFace
                        shape = Shape(f"face_{i}_{j}", Geometry(j, face))
                        part.add_shape(shape)
                        shape_count += 1

                        # Check if we hit the limit
                        if limit is not None and shape_count >= limit:
                            break
                elif isinstance(geom, geo_su.AdvancedFace):
                    shape = Shape(f"face_{i}", Geometry(i, geom))
                    part.add_shape(shape)
                    shape_count += 1
                else:
                    # Fallback: keep as one shape
                    shape = Shape(f"shape_{i}", Geometry(i, geom))
                    part.add_shape(shape)
                    shape_count += 1

                # Check if we hit the limit
                if limit is not None and shape_count >= limit:
                    break

        logger.info(f"Added part '{part.name}' with {shape_count} shape(s) ({'split' if split else 'grouped'} mode)")

        a.add_part(part)

    # Wire (sectionless) bodies — beam centerlines / construction wireframes ACIS stores with no
    # bounding face. The face-based body loop above drops them (empty geometries → skipped), which
    # left wire-only SAT files (e.g. a single Sesam beam) rendering as an empty scene. A wire has
    # no surface and no section, so it is NOT a Beam — it is a Shape carrying a curve Geometry, which
    # glTF renders as line geometry. No geometry left behind, nothing fabricated.
    from ada.visit.colors import Color

    n_wire = 0
    for body_name, wire_geoms in converter.convert_all_wire_bodies():
        part = a.parts.get(body_name)
        if part is None:
            part = Part(body_name)
            a.add_part(part)
        for i, geom in enumerate(wire_geoms):
            # A colour is required for the glTF line material (the line store has no default); it
            # must live on the Geometry — that's what the tessellator reads for the line material.
            gray = Color.from_str("gray")
            part.add_shape(Shape(f"wire{i}", Geometry(i, geom, color=gray), color=gray))
            n_wire += 1
    if n_wire:
        logger.info(f"Imported {n_wire} wire/line shape(s) from sectionless bodies in ACIS SAT file")

    logger.info(f"Imported {len(bodies)} bodies from ACIS SAT file")

    return a


def from_fem(
    fem_file: str | list | pathlib.Path,
    fem_format: str | list = None,
    name: str | list = None,
    source_units=Units.M,
    fem_converter="default",
    create_concept_objects=False,
    convert_skip_plates=False,
    convert_skip_beams=False,
    cad_config: "CadConfig | None" = None,
) -> Assembly:
    """Create an Assembly object from a FEM file."""
    a = Assembly(units=source_units, cad_config=cad_config)
    if isinstance(fem_file, str) or issubclass(type(fem_file), pathlib.Path):
        a.read_fem(fem_file, fem_format, name, fem_converter=fem_converter)
    elif isinstance(fem_file, list):
        for i, f in enumerate(fem_file):
            fem_format_in = fem_format if fem_format is None else fem_format[i]
            name_in = name if name is None else name[i]
            a.read_fem(f, fem_format_in, name_in, fem_converter=fem_converter)
    else:
        raise ValueError(f'fem_file must be either string or list. Passed type was "{type(fem_file)}"')

    if create_concept_objects:
        a.create_objects_from_fem(skip_beams=convert_skip_beams, skip_plates=convert_skip_plates)

    return a


def from_fem_res(fem_file: str | pathlib.Path, fem_format: str = None) -> FEAResult:
    from ada.fem.formats.postprocess import postprocess

    return postprocess(fem_file, fem_format)


def from_sesam_cc(fem_file: str | pathlib.Path) -> dict[str, CCData]:
    from ada.fem.formats.sesam.results.read_cc import read_cc_file

    return read_cc_file(fem_file)


def from_genie_xml(
    xml_path,
    ifc_schema="IFC4",
    name: str = None,
    extract_joints=False,
    cad_config: "CadConfig | None" = None,
    build_topology_store: bool = False,
) -> Assembly:
    """Create an Assembly object from a Genie XML file.

    With ``build_topology_store`` the source ACIS body is also read into a neutral
    :class:`~ada.geom.brep.BRepStore` and attached, so a subsequent
    ``to_genie_xml(embed_sat=True)`` re-exports the exact source topology (1 lump,
    every shared edge) instead of re-welding the plate outlines — which keeps every
    beam referenced and avoids Genie re-imprinting on import. Off by default (it
    reads the SAT a second time).
    """
    from ada.cadit.gxml.store import GxmlStore

    gxml = GxmlStore(xml_path)
    p = gxml.to_part(extract_joints=extract_joints)
    name = name if name is not None else p.name
    a = Assembly(name=name, schema=ifc_schema, cad_config=cad_config) / p
    if build_topology_store:
        from ada.cadit.sat.read.to_brep import sat_store_to_brep

        if len(gxml.sat_factory.sat_store.sat_records) == 0:
            gxml.sat_factory.load_sat_data_from_file()
        store = sat_store_to_brep(gxml.sat_factory.sat_store)
        a._topology_store = store
        p._topology_store = store
    return a


def dexpi_to_procedural(
    path: str | pathlib.Path,
    *,
    flavour: str | None = None,
    definitions=None,
    layout=None,
    base_doc: dict | None = None,
    inline_components: Literal["metadata", "equipment"] = "metadata",
) -> tuple[dict, dict]:
    """Read a DEXPI P&ID and return ``(procedural document, equipment catalog)``.

    The useful seam under :func:`from_dexpi`: the document is the compiler's own commit format, so
    it feeds ``ProceduralBuilder.from_dict`` or ``to_excel`` for inspection and hand-editing before
    anything is built, and the catalog's ``.get`` is already a valid ``equipment_resolver``. See
    :func:`ada.cadit.dexpi.read.to_procedural.dexpi_to_procedural_doc` for the arguments.
    """
    from ada.cadit.dexpi.read.to_procedural import dexpi_to_procedural_doc
    from ada.cadit.dexpi.store import read_dexpi

    return dexpi_to_procedural_doc(
        read_dexpi(path, flavour=flavour),
        definitions=definitions,
        layout=layout,
        base_doc=base_doc,
        inline_components=inline_components,
    )


def from_dexpi(
    path: str | pathlib.Path,
    *,
    name: str | None = None,
    flavour: str | None = None,
    definitions=None,
    layout=None,
    base_doc: dict | None = None,
    inline_components: Literal["metadata", "equipment"] = "metadata",
    build_3d: bool = True,
    route: bool = True,
    design_rules: str = "standard",
    strict: bool = False,
    cad_config: "CadConfig | None" = None,
) -> Assembly:
    """Build a 3D model from a DEXPI P&ID -- either flavour, sniffed from the root tag.

    A P&ID says what the plant is and how it is connected, and nothing about where any of it
    stands. The gap is closed in three steps (see
    :mod:`ada.cadit.dexpi.read.to_procedural`): each item resolves through the equipment definition
    list to a physical envelope with real 3D nozzles, :func:`ada.topo_model.layout.plan_layout`
    generates the decks and places everything on them, and each DEXPI ``PipingNetworkSegment``
    becomes a two-ended system. With ``build_3d`` (the default) the result is then compiled --
    structure, placed equipment, A*-routed runs and the wall/deck penetrations they cross -- and
    returned as an :class:`~ada.Assembly`. With ``build_3d=False`` you get the schematic-only
    assembly: the same equipment and ports, no structure and no routing, which is what a
    write-back path wants.

    **The layout is generated, not designed.** Shelf packing on physical size has no process sense
    whatsoever: a pump can land at the far end of a deck from the vessel it feeds. Pass
    ``layout=LayoutRules(...)`` to set the deck bounds and pitch, ``base_doc`` to keep placements
    you have already corrected, and expect to move things. DEXPI's own 2D coordinates are drawing
    millimetres and are never read as plant coordinates.

    ``definitions`` is the equipment definition list (a JSON/XLSX path or a loaded dict) that
    overrides the shipped class defaults per tag or per class. ``route=False`` places the equipment
    but leaves the systems unrouted. ``inline_components="equipment"`` materialises each in-line
    valve as its own small equipment instead of recording it in the run's metadata.

    **Nothing is dropped quietly.** The compiler skips an unwireable system and an unroutable run
    with only a log warning, which is how an import comes back looking complete with half the pipes
    missing. Every one of them is collected into
    ``assembly.metadata["dexpi"]["report"]``, summarised in one WARNING, and rendered by
    :func:`ada.cadit.dexpi.read.to_procedural.dexpi_import_report`. ``strict=True`` raises instead.
    """
    from ada.cadit.dexpi.read.to_procedural import (
        DexpiImportReport,
        dexpi_to_procedural_doc,
    )
    from ada.cadit.dexpi.store import read_dexpi
    from ada.topo_model.compile import build_procedural_assembly

    dexpi_doc = read_dexpi(path, flavour=flavour)
    name = name or (dexpi_doc.header.project or pathlib.Path(path).stem)
    doc, catalog = dexpi_to_procedural_doc(
        dexpi_doc,
        definitions=definitions,
        layout=layout,
        base_doc=base_doc,
        inline_components=inline_components,
    )
    report = DexpiImportReport.from_dict((doc.get("dexpi") or {}).get("report"))
    doc["design_rules"] = design_rules
    if not route:
        doc["systems"] = []

    if build_3d:
        with _dexpi_build_warnings() as records:
            a = build_procedural_assembly(doc, name=name, equipment_resolver=catalog.get)
        _collect_build_issues(a, doc, records, report)
    else:
        a = _dexpi_schematic_assembly(name, doc, catalog)

    # Stashed so Assembly.to_dexpi(from_scratch=False) has a source document to merge edits into --
    # see ada.cadit.dexpi.write.from_ada.
    a._dexpi_store = dexpi_doc

    if cad_config is not None:
        a.cad_config = cad_config
    a.metadata["dexpi"] = {
        "source": str(path),
        "flavour": dexpi_doc.flavour.value,
        "reader_warnings": list(dexpi_doc.warnings),
        "report": report.as_dict(),
    }
    if not report.is_clean:
        if strict:
            raise ValueError(f"DEXPI import of {pathlib.Path(path).name}: {report.format()}")
        logger.warning("dexpi: %s (see assembly.metadata['dexpi']['report'])", report.summary())
    return a


@contextlib.contextmanager
def _dexpi_build_warnings():
    """Collect the warnings the procedural compiler drops systems with.

    ``_wire_systems`` and ``run_design(skip_failed=True)`` both report a lost run with nothing but
    ``logger.warning``, which is right for a compiler and useless to someone who asked for their
    P&ID. adapy's logger does not propagate to the root, so this attaches to it directly rather
    than going through ``logging.capture``.
    """
    import logging

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    ada_logger = logging.getLogger("ada")
    handler = _Collector(level=logging.WARNING)
    previous = ada_logger.level
    ada_logger.addHandler(handler)
    if previous > logging.WARNING or previous == logging.NOTSET:
        ada_logger.setLevel(logging.WARNING)
    try:
        yield records
    finally:
        ada_logger.removeHandler(handler)
        ada_logger.setLevel(previous)


def _collect_build_issues(assembly: Assembly, doc: dict, records, report) -> None:
    """Record every system that asked for geometry and did not get it.

    Presence of geometry is the check, not the log line: a run is in the model or it is not, and
    that survives any rewording of the compiler's warnings. The captured warnings only supply the
    *reason*, and a system with no matching warning still gets an entry.
    """
    routed: set[str] = set()
    for part in assembly.get_all_parts_in_assembly(include_self=True):
        if part.name != "Systems":
            continue
        for obj in part.get_all_physical_objects():
            routed.add(str(obj.name).rsplit("_route", 1)[0])

    reasons: dict[str, str] = {}
    for record in records:
        message = str(record.msg)
        if "skipping system" not in message and "skipping geometry for system" not in message:
            continue
        args = record.args if isinstance(record.args, tuple) else (record.args,)
        if not args:
            continue
        reasons.setdefault(str(args[0]), str(args[-1]) if len(args) > 1 else "no route found")

    for spec in doc.get("systems") or []:
        system_name = spec.get("NAME")
        if system_name in routed:
            continue
        stage = "wiring" if system_name in reasons and "unknown" in reasons[system_name] else "routing"
        report.add("system", system_name, stage, reasons.get(system_name, "no routed geometry was produced"))


def _dexpi_schematic_assembly(name: str, doc: dict, catalog: dict) -> Assembly:
    """The schematic-only assembly: placed equipment with their ports, and unrouted systems.

    No structure, no grid, no routing -- the model a write-back path needs, and the fastest way to
    look at what a P&ID resolved to before committing to a 3D build.
    """
    from ada.api.spatial.equipment import Equipment
    from ada.topo_model.compile import (
        _equipment_to_object,
        _wire_systems,
        equipment_space_offset,
    )
    from ada.topology.entities import TopoEquipment, TopoSpace

    spaces = {row["NAME"]: TopoSpace(**_strip_none(row)) for row in doc.get("spaces") or []}
    objects = []
    for row in doc.get("equipments") or []:
        entity = TopoEquipment(**_strip_none(row))
        offset = equipment_space_offset(entity, spaces.get(entity.SPACE_NAME))
        objects.append(_equipment_to_object(entity, catalog.get, offset))

    a = Assembly(name=name) / (Part("Equipment") / objects)
    equipment_map = {obj.name: obj for obj in objects if isinstance(obj, Equipment)}
    a.systems.extend(_wire_systems(doc.get("systems") or [], equipment_map))
    return a


def _strip_none(row: dict) -> dict:
    """Drop explicit nulls before ``Topo*(**row)`` -- a field typed ``float`` with a ``None``
    default accepts the default but rejects ``None`` as an argument (mirrors
    ``ada.topo_model.builder._strip_none``)."""
    return {k: v for k, v in row.items() if v is not None}
