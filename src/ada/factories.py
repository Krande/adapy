"""Top-level ``from_*`` factory functions (kept out of ``ada/__init__.py`` for readability).

Re-exported from ``ada`` so the public API is unchanged: ``ada.from_step(...)`` still works.

Each Assembly-returning factory accepts an optional ``cad_config`` (``ada.cad.CadConfig``) that is
attached to the returned assembly, so a downstream conversion (e.g.
``stream_step_to_glb(..., cad_config=asm.cad_config)``) uses the chosen tessellation path.
"""

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from ada.api.primitives import Shape
from ada.api.spatial import Assembly, Part
from ada.base.units import Units
from ada.config import logger

if TYPE_CHECKING:
    import ifcopenshell

    from ada.cad import CadConfig
    from ada.fem.formats.sesam.results.read_cc import CCData
    from ada.fem.results.common import FEAResult


def from_ifc(
    ifc_file: os.PathLike | ifcopenshell.file, units=Units.M, name="Ada", cad_config: "CadConfig | None" = None
) -> Assembly:
    """Create an Assembly object from an IFC file."""
    if isinstance(ifc_file, (os.PathLike, str)):
        ifc_file = pathlib.Path(ifc_file).resolve().absolute()
        logger.info(f'Reading "{ifc_file.name}"')
    else:
        logger.info("Reading IFC file object")

    a = Assembly(units=units, name=name, cad_config=cad_config)
    a.read_ifc(ifc_file)
    return a


def from_step(
    step_file: str | pathlib.Path, source_units=Units.M, cad_config: "CadConfig | None" = None, **kwargs
) -> Assembly:
    """Create an Assembly object from a STEP file.

    The read path defaults to ``cad_config.step_reader`` (``StepReader.AUTO`` out of the box:
    constant-memory streaming with an OCC fallback for out-of-scope files — the most
    memory-efficient + robust choice). Pass a ``cad_config`` with a different ``step_reader`` to
    override, or pass ``reader=`` directly via ``**kwargs`` to force one for this call.
    """
    a = Assembly(cad_config=cad_config)
    a.read_step_file(step_file, source_units=source_units, **kwargs)
    return a


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
    xml_path, ifc_schema="IFC4", name: str = None, extract_joints=False, cad_config: "CadConfig | None" = None
) -> Assembly:
    """Create an Assembly object from a Genie XML file."""
    from ada.cadit.gxml.store import GxmlStore

    gxml = GxmlStore(xml_path)
    p = gxml.to_part(extract_joints=extract_joints)
    name = name if name is not None else p.name
    return Assembly(name=name, schema=ifc_schema, cad_config=cad_config) / p
