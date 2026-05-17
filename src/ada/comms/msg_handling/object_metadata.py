"""Per-object metadata dicts shipped to the viewer's selected-object panel.

The frontend renders typed Properties for clicked beams and plates (section
profile, material elastics, thickness). This module produces the dicts that
populate ``SceneBackend.object_meta`` at file-load time and the responses
returned by ``mesh_info_callback`` at click time.

The serialization pattern mirrors ``ada.fem.formats.code_aster.write.beams_sidecar``
(profile fields walked via ``getattr(..., None)`` then float-coerced) but
broadened to also cover plates and to drop the GENBEAM filter — the viewer
still wants to show what data exists even if the section can't be tessellated.
"""
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.spatial import Assembly
    from ada.comms.fb_wrap_model_gen import FileObjectDC
    from ada.comms.wsock.scene_model import SceneBackend
    from ada.materials import Material
    from ada.sections import Section


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def section_to_dict(section: Section | None) -> dict | None:
    """Flatten a :class:`ada.Section` into a JSON-friendly dict.

    ``None`` in → ``None`` out. Unknown attributes silently become ``None``
    so callers don't need to discriminate I-profile vs tubular vs box at
    serialization time."""
    if section is None:
        return None
    sec_type = getattr(section, "type", None)
    if sec_type is None:
        type_name = None
    elif hasattr(sec_type, "value"):
        type_name = sec_type.value
    else:
        type_name = str(sec_type)
    return {
        "name": getattr(section, "name", None),
        "type": type_name,
        "h": _maybe_float(getattr(section, "h", None)),
        "w_top": _maybe_float(getattr(section, "w_top", None)),
        "w_btn": _maybe_float(getattr(section, "w_btn", None)),
        "t_w": _maybe_float(getattr(section, "t_w", None)),
        "t_ftop": _maybe_float(getattr(section, "t_ftop", None)),
        "t_fbtn": _maybe_float(getattr(section, "t_fbtn", None)),
        "r": _maybe_float(getattr(section, "r", None)),
        "wt": _maybe_float(getattr(section, "wt", None)),
    }


def material_to_dict(material: Material | None) -> dict | None:
    """Flatten an :class:`ada.Material` (+ its ``.model``) into a JSON dict.

    The numeric properties live on ``material.model`` (a CarbonSteel/Metal
    instance). Older Material entries built from FEA-side dumps may have
    ``model=None`` — return name-only in that case rather than crashing the
    metadata lookup."""
    if material is None:
        return None
    out: dict[str, Any] = {"name": getattr(material, "name", None)}
    model = getattr(material, "model", None)
    if model is None:
        return out
    out.update(
        {
            "E": _maybe_float(getattr(model, "E", None)),
            "rho": _maybe_float(getattr(model, "rho", None)),
            "sig_y": _maybe_float(getattr(model, "sig_y", None)),
            "sig_u": _maybe_float(getattr(model, "sig_u", None)),
            "v": _maybe_float(getattr(model, "v", None)),
        }
    )
    return out


def beam_metadata(
    name: str,
    section: Section | None,
    material: Material | None,
) -> dict:
    return {
        "type": "Beam",
        "name": name,
        "section": section_to_dict(section),
        "material": material_to_dict(material),
    }


def plate_metadata(
    name: str,
    thickness: float | None,
    material: Material | None,
) -> dict:
    return {
        "type": "Plate",
        "name": name,
        "thickness": _maybe_float(thickness),
        "material": material_to_dict(material),
    }


def _register_beam(scene: SceneBackend, file_name: str, beam) -> None:
    name = getattr(beam, "name", None)
    if not name:
        return
    file_meta = scene.object_meta.setdefault(file_name, {})
    file_meta[name] = beam_metadata(
        name=name,
        section=getattr(beam, "section", None),
        material=getattr(beam, "material", None),
    )


def _register_plate(scene: SceneBackend, file_name: str, plate) -> None:
    name = getattr(plate, "name", None)
    if not name:
        return
    file_meta = scene.object_meta.setdefault(file_name, {})
    file_meta[name] = plate_metadata(
        name=name,
        thickness=getattr(plate, "t", None),
        material=getattr(plate, "material", None),
    )


def build_object_meta_from_assembly(
    scene: SceneBackend,
    file_name: str,
    assembly: Assembly,
    *,
    cad_side: bool,
) -> None:
    """Populate ``scene.object_meta[file_name]`` from an in-memory Assembly.

    ``cad_side=True`` for IFC / native CAD loads — walks
    ``assembly.get_all_physical_objects`` and registers Beam/Plate entries.
    ``cad_side=False`` for FEA loads — walks the FEM and registers one
    entry per element with the section / material data from its FemSection.

    The CAD↔FEA linkage that used to live here is now resolved on the
    frontend via the ``ADA_EXT_data`` extension's ``assembly_guid`` and
    ``parent_object_guid`` fields. This index only carries the
    per-object Properties payload."""
    # Reset this file's slice so reloads don't leave stale entries.
    scene.object_meta[file_name] = {}

    if cad_side:
        _build_cad_index(scene, file_name, assembly)
    else:
        _build_fea_index(scene, file_name, assembly)


def _build_cad_index(scene: SceneBackend, file_name: str, assembly: Assembly) -> None:
    from ada import Beam, Plate

    # ``get_all_physical_objects`` already walks every subpart, so we
    # don't iterate ``parts`` ourselves — that would visit each Beam /
    # Plate N times for an N-part assembly.
    for obj in assembly.get_all_physical_objects():
        if isinstance(obj, Beam):
            _register_beam(scene, file_name, obj)
        elif isinstance(obj, Plate):
            _register_plate(scene, file_name, obj)


def _build_fea_index(scene: SceneBackend, file_name: str, assembly: Assembly) -> None:
    """Walk every FemSection and register one entry per element.

    Element naming matches what the GLB exporter emits for FEA scenes
    (``Li{elem.id}`` for line elements, ``EL{elem.id}`` for shell/solid faces
    — see ``ada/fem/results/common.py:292-303`` and
    ``ada/visit/gltf/graph.py``). If the GLB pipeline ever changes these
    prefixes, mirror the change here."""
    fems = [assembly.fem] if assembly.fem is not None else []
    for part in assembly.get_all_parts_in_assembly(True):
        if part.fem is not None and part.fem is not assembly.fem:
            fems.append(part.fem)

    for fem in fems:
        for fem_sec in getattr(fem, "sections", []):
            elset = getattr(fem_sec, "elset", None)
            members = getattr(elset, "members", []) if elset is not None else []
            is_line = fem_sec.section is not None
            is_shell = getattr(fem_sec, "thickness", None) is not None
            for elem in members:
                node_name = _fem_elem_node_name(elem, is_line)
                if node_name is None:
                    continue
                file_meta = scene.object_meta.setdefault(file_name, {})
                if is_line:
                    file_meta[node_name] = beam_metadata(
                        name=node_name,
                        section=fem_sec.section,
                        material=fem_sec.material,
                    )
                elif is_shell:
                    file_meta[node_name] = plate_metadata(
                        name=node_name,
                        thickness=fem_sec.thickness,
                        material=fem_sec.material,
                    )
                # Solid / connector / unknown — skip silently.


def _fem_elem_node_name(elem, is_line: bool) -> str | None:
    elem_id = getattr(elem, "id", None)
    if elem_id is None:
        return None
    prefix = "Li" if is_line else "EL"
    return f"{prefix}{int(elem_id)}"


def populate_for_file_object(scene: SceneBackend, file_object: FileObjectDC) -> None:
    """Lazy-load entry point called after the viewer pulls a file into the
    scene. Reads the file, builds an in-memory Assembly, populates the index.

    Errors are logged and swallowed — a missing metadata index degrades to
    "No metadata available" in the panel; it must not break the click path.
    """
    if file_object is None or file_object.filepath is None:
        return
    file_name = file_object.name
    if file_name in scene.object_meta and scene.object_meta[file_name]:
        return  # already built
    fp = pathlib.Path(file_object.filepath)
    try:
        suffix = fp.suffix.lower()
        if suffix == ".ifc":
            _populate_ifc(scene, file_name, fp)
        elif suffix in {".sif", ".sin", ".fem", ".inp", ".bdf"}:
            _populate_fem(scene, file_name, fp, suffix)
        # GLB-only files have no source Assembly to walk; skip.
    except Exception as exc:  # noqa: BLE001 — metadata is non-critical
        logger.warning(f"object_metadata: failed to populate index for {file_name}: {exc}")


def _populate_ifc(scene: SceneBackend, file_name: str, fp: pathlib.Path) -> None:
    import ada

    assembly = ada.from_ifc(fp)
    build_object_meta_from_assembly(scene, file_name, assembly, cad_side=True)


def _populate_fem(scene: SceneBackend, file_name: str, fp: pathlib.Path, suffix: str) -> None:
    import ada

    assembly = ada.from_fem(fp)
    build_object_meta_from_assembly(scene, file_name, assembly, cad_side=False)


__all__ = [
    "section_to_dict",
    "material_to_dict",
    "beam_metadata",
    "plate_metadata",
    "build_object_meta_from_assembly",
    "populate_for_file_object",
]
