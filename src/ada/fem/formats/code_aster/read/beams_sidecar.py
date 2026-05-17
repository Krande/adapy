"""Read side of the ``<name>.adapy_fem.json`` sidecar emitted by the
Code Aster MED writer.

Reconstructs lightweight :class:`ada.Beam` instances from the
serialized per-line-element metadata so the streaming bake can
tessellate beams the same way it does for SIF / Abaqus, and
surfaces the per-element CAD lineage to the bake's manifest. See
:mod:`ada.fem.formats.code_aster.write.beams_sidecar` for the schema.
"""
from __future__ import annotations

import json
import pathlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


_SIDECAR_SUFFIX = ".adapy_fem.json"


def _sidecar_path_for(rmed_path: pathlib.Path) -> pathlib.Path:
    """Sidecar lives next to the .rmed with a ``.adapy_fem.json``
    extension.

    Code Aster round-trips the input ``<name>.med`` to an output
    ``<name>.rmed`` in the same directory, so the writer-side sidecar
    naturally sits next to the .rmed by the time the bake worker
    opens it.
    """
    return rmed_path.with_suffix(_SIDECAR_SUFFIX)


def _reconstruct_section(section_dict: dict):
    """Materialise an :class:`ada.Section` from the serialized dict.

    All geometric dims are passed through as-is; ``type`` is the
    canonical adapy section-type string (e.g. ``"BG"``, ``"IPE"``,
    ``"RHS"``) which the Section constructor maps via
    :class:`BaseTypes.from_str`.
    """
    from ada.sections.concept import Section

    return Section(
        name=section_dict["name"],
        sec_type=section_dict.get("type"),
        h=section_dict.get("h"),
        w_top=section_dict.get("w_top"),
        w_btn=section_dict.get("w_btn"),
        t_w=section_dict.get("t_w"),
        t_ftop=section_dict.get("t_ftop"),
        t_fbtn=section_dict.get("t_fbtn"),
        r=section_dict.get("r"),
        wt=section_dict.get("wt"),
    )


def try_load_beams_sidecar(
    rmed_path: pathlib.Path,
    nmap: dict[int, int],
) -> tuple[list, dict[str, int]]:
    """Load the sidecar (if present) and return tessellation-ready beams.

    Returns ``(beams, extra_skip)`` where:

    * ``beams`` is a list of ``(beam, elem_id, n0_idx, n1_idx, n0_pos, n1_pos)``
      tuples in the shape :func:`tessellate_beams_to_solid_mesh`
      expects. Empty when the sidecar is missing or contains no
      reconstructable entries.
    * ``extra_skip`` is a per-reason count of sidecar entries that
      were rejected before tessellation (missing endpoint in the
      mesh, section reconstruction blew up, ...). Folded into the
      coverage summary so the user can tell from one log line whether
      the bake covered the model.
    """
    from ada import Beam

    sidecar = _sidecar_path_for(rmed_path)
    if not sidecar.is_file():
        return [], {}

    try:
        payload = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        # Corrupt or unreadable sidecar — fall back to line-only.
        return [], {"sidecar-unreadable": 1}

    raw_beams = payload.get("beams", [])
    if not raw_beams:
        return [], {}

    beams: list = []
    extra_skip: dict[str, int] = {}
    for entry in raw_beams:
        try:
            n0_id = int(entry["n0_id"])
            n1_id = int(entry["n1_id"])
        except (KeyError, TypeError, ValueError):
            extra_skip["sidecar-bad-node-ids"] = extra_skip.get("sidecar-bad-node-ids", 0) + 1
            continue

        n0_idx = nmap.get(n0_id)
        n1_idx = nmap.get(n1_id)
        if n0_idx is None or n1_idx is None:
            extra_skip["endpoint-not-in-mesh"] = extra_skip.get("endpoint-not-in-mesh", 0) + 1
            continue

        section_dict = entry.get("section")
        if not section_dict:
            extra_skip["no-section"] = extra_skip.get("no-section", 0) + 1
            continue

        try:
            section = _reconstruct_section(section_dict)
        except Exception:  # noqa: BLE001 — defensive
            extra_skip["section-reconstruct-failed"] = (
                extra_skip.get("section-reconstruct-failed", 0) + 1
            )
            continue

        local_z = entry.get("local_z")
        if local_z is None:
            extra_skip["missing-local-z"] = extra_skip.get("missing-local-z", 0) + 1
            continue

        n0_pos = entry["n0"]
        n1_pos = entry["n1"]
        material_name = entry.get("material_name") or "mat"

        try:
            beam = Beam(
                f"BM{entry['elem_id']}",
                n0_pos,
                n1_pos,
                sec=section,
                mat=material_name,
                up=local_z,
            )
        except Exception:  # noqa: BLE001 — defensive
            extra_skip["beam-construct-failed"] = (
                extra_skip.get("beam-construct-failed", 0) + 1
            )
            continue

        beams.append((beam, int(entry["elem_id"]), n0_idx, n1_idx, n0_pos, n1_pos))

    return beams, extra_skip


def try_load_lineage_payload(rmed_path: pathlib.Path) -> dict | None:
    """Read the sidecar and surface just the CAD↔FEA lineage data.

    Returns a dict the bake injects into ``fea.manifest.json.lineage``:

    .. code-block:: json

        {
          "assembly_guid": "<adapy Assembly.guid>",
          "groups": [
            {"parent_object_guid": "<beam.guid>",
             "parent_object_name": "BM_FLOOR_01",
             "members": ["E17", "E18", ...]}
          ]
        }

    ``None`` when the sidecar is missing, unreadable, or carries no
    ``parent_object_guid`` entries (e.g. a v1 sidecar from before the
    lineage schema bump). Members use the ``E{elem_id}`` naming scheme
    the bake's element-ranges writer emits, so frontend selection
    state ties back to the same labels.
    """
    sidecar = _sidecar_path_for(rmed_path)
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return None

    assembly_guid = payload.get("assembly_guid")
    by_parent: dict[str, dict] = {}

    # Beams: one sidecar entry per line element, group by parent
    # guid so the frontend can list "N elements meshed from this beam"
    # without a second pass.
    for entry in payload.get("beams", []) or []:
        parent_guid = entry.get("parent_object_guid")
        if not parent_guid:
            continue
        elem_id = entry.get("elem_id")
        if elem_id is None:
            continue
        bucket = by_parent.setdefault(
            parent_guid,
            {
                "parent_object_guid": parent_guid,
                "parent_object_name": entry.get("parent_object_name"),
                "members": [],
            },
        )
        bucket["members"].append(f"E{int(elem_id)}")

    # Plates: one sidecar entry per FemSection (already aggregated on
    # the write side). Same shape, just iterate the ``elem_ids`` list.
    for entry in payload.get("plates", []) or []:
        parent_guid = entry.get("parent_object_guid")
        if not parent_guid:
            continue
        elem_ids = entry.get("elem_ids") or []
        if not elem_ids:
            continue
        bucket = by_parent.setdefault(
            parent_guid,
            {
                "parent_object_guid": parent_guid,
                "parent_object_name": entry.get("parent_object_name"),
                "members": [],
            },
        )
        bucket["members"].extend(f"E{int(eid)}" for eid in elem_ids)

    if not assembly_guid and not by_parent:
        return None
    return {
        "assembly_guid": assembly_guid,
        "groups": list(by_parent.values()),
    }


__all__ = ["try_load_beams_sidecar", "try_load_lineage_payload"]
