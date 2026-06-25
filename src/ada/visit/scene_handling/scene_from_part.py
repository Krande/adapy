from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ada.extension.fem_concepts_builder import build_design_fem_concepts

if TYPE_CHECKING:
    import trimesh

    from ada import Assembly, Part
    from ada.visit.scene_converter import SceneConverter


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, converter: SceneConverter) -> trimesh.Scene:
    import ada.extension.design_extension_schema as design_ext
    from ada import Assembly, Beam, Plate, PlateCurved
    from ada.comms.msg_handling.object_metadata import (
        beam_metadata,
        curved_plate_metadata,
        plate_metadata,
    )
    from ada.config import logger
    from ada.occ.tessellating import BatchTessellator

    params = converter.params

    if params.stream_from_ifc_store and params.auto_sync_ifc_store and isinstance(part_or_assembly, Assembly):
        part_or_assembly.ifc_store.sync()

    bt = BatchTessellator()

    graph = converter.graph
    graph.add_nodes_from_part(part_or_assembly)

    groups = []

    for group_name, groups_ in part_or_assembly.get_all_groups_as_merged().items():
        group0 = groups_[0]
        members = [m.name for g in groups_ for m in g.members]
        g = design_ext.Group(
            name=group_name, members=members, description=group0.description, parent_name=part_or_assembly.name
        )
        groups.append(g)

    # The IFC-stream path tessellates via ifcopenshell's own geometry iterator (OCC), which
    # never sees ada.geom — so it can't honour an NGEOM pipeline. When one is requested
    # (ADA_STREAM_TESS_PIPELINE=libtess2|occ|cgal|hybrid) route through the ada.geom
    # part-tessellation instead (BatchTessellator.tessellate_geom carries the NGEOM hook).
    _ngeom_pipeline = os.environ.get("ADA_STREAM_TESS_PIPELINE")
    scene = None
    if params.stream_from_ifc_store and not _ngeom_pipeline:
        if isinstance(part_or_assembly, Assembly):
            scene = bt.ifc_to_trimesh_scene(
                part_or_assembly.get_assembly().ifc_store, merge_meshes=params.merge_meshes, graph=graph
            )
        else:
            logger.warning(
                "Stream from ifc store is only supported from Assembly objects, not Part objects. "
                "Will use default tessellation using pythonocc-core"
            )
    if scene is None:
        scene = bt.tessellate_part(part_or_assembly, params=params, graph=graph)

    nodes_geom = set(scene.graph.nodes_geometry)
    # Per-object guid map: lets the frontend resolve a clicked CAD
    # object name (Beam/Plate) to its stable adapy guid. A derived FEA
    # file's SimGroup.parent_object_guid points back at these values so
    # the cross-model link doesn't depend on name matching.
    object_guids: dict[str, str] = {}
    # Per-object section/material metadata. On by default
    # (``params.embed_object_metadata=True``); the Properties panel
    # reads from here instead of going back to the server with a
    # MESH_INFO_REQUEST that would need the source IFC to be
    # uploaded alongside the GLB. Costs roughly the typed dict size
    # (~120-250 B / Beam, ~80-150 B / Plate) per physical object —
    # for a 1000-object model that's ~100-250 KB of extra JSON.
    embed_metadata = bool(getattr(params, "embed_object_metadata", True))
    object_metadata: dict[str, dict] | None = {} if embed_metadata else None
    for obj in part_or_assembly.get_all_physical_objects():
        if obj.name and obj.guid:
            object_guids[obj.name] = obj.guid
        if object_metadata is not None and obj.name:
            if isinstance(obj, Beam):
                object_metadata[obj.name] = beam_metadata(obj.name, obj.section, obj.material)
            elif isinstance(obj, PlateCurved):
                object_metadata[obj.name] = curved_plate_metadata(obj.name, obj.t, obj.material)
            elif isinstance(obj, Plate):
                object_metadata[obj.name] = plate_metadata(obj.name, obj.t, obj.material)

    # Welds are owned per-Part (`Part._welds`) rather than emitted by
    # get_all_physical_objects; iterate the part tree ourselves to
    # ship the per-weld metadata the viewer needs (type, throat,
    # member names) for the inspector + reverse-graph lookup.
    if object_metadata is not None:
        for subp in part_or_assembly.get_all_subparts(include_self=True):
            for weld in getattr(subp, "_welds", []):
                if weld.name:
                    object_metadata[weld.name] = _weld_metadata(weld)
                if weld.guid:
                    object_guids[weld.name] = weld.guid

    # Per-Connection roll-up: list every ada.Connection Part below
    # this Assembly so the inspector can show "Connections (N)" for
    # a clicked member and expand into per-connection details (spec
    # lineage + member roles + weld names) without walking the
    # THREE scene graph at runtime. One entry per Connection Part.
    connections = _build_connection_entries(part_or_assembly)

    converter.ada_ext.design_objects.append(
        design_ext.DesignDataExtension(
            name=part_or_assembly.name,
            description=type(part_or_assembly).__name__,
            groups=groups,
            node_references=design_ext.DesignNodeReference(faces=list(nodes_geom)),
            object_guids=object_guids or None,
            object_metadata=object_metadata or None,
            stats=_build_design_stats(part_or_assembly),
            connections=connections or None,
            fem_concepts=build_design_fem_concepts(part_or_assembly),
        )
    )

    return scene


def _build_connection_entries(part_or_assembly):
    """Walk the part tree and emit one ConnectionInfo per ada.Connection Part.

    Each entry carries the connection's spec lineage (when present)
    plus the names of every beam, plate, and weld it owns — the same
    identifiers the inspector uses elsewhere on the extension, so the
    panel can join by name with no extra lookups.
    """
    from ada.api.connections.joints import Connection
    from ada.extension import design_extension_schema as design_ext

    entries: list = []
    for subp in part_or_assembly.get_all_subparts(include_self=True):
        if not isinstance(subp, Connection):
            continue

        # Roles encoded in sample_<role>_<...> beam names from the
        # build_sample synthesiser. Real-model Connections won't have
        # this naming pattern — member_roles stays None and the
        # inspector falls back to the flat beam_names list.
        member_roles: dict[str, list[str]] = {}
        beam_names: list[str] = []
        for beam in subp.beams:
            if beam.name:
                beam_names.append(beam.name)
                if beam.name.startswith("sample_"):
                    role = beam.name[len("sample_") :]
                    member_roles.setdefault(role, []).append(beam.name)

        plate_names = [p.name for p in subp.plates if p.name]
        weld_names = [w.name for w in subp.welds if w.name]

        entries.append(
            design_ext.ConnectionInfo(
                name=subp.name,
                spec_name=getattr(subp, "spec_name", None),
                spec_inputs=getattr(subp, "spec_inputs", None),
                member_roles=member_roles or None,
                beam_names=beam_names or None,
                plate_names=plate_names or None,
                weld_names=weld_names or None,
            )
        )
    return entries


def _weld_metadata(weld) -> dict:
    """Serialise a Weld for object_metadata.

    Forward edge for the viewer's weld graph: each weld lists its member
    names; the frontend builds the reverse index (member → list[weld])
    from this on GLB load. ``type`` is the discriminator on the
    metadata dict — `{"type": "weld", ...}` distinguishes weld entries
    from beam/plate ones that already live in the same map.
    """
    return {
        "type": "weld",
        "weld_type": weld.type.value if weld.type is not None else None,
        "throat": weld.throat,
        "leg1": weld.leg1,
        "leg2": weld.leg2,
        "sided": weld.sided,
        "intermittent": (
            {
                "pitch": weld.intermittent.pitch,
                "length_on": weld.intermittent.length_on,
                "length_off": weld.intermittent.length_off,
            }
            if weld.intermittent is not None
            else None
        ),
        "sweep_curve_present": weld.sweep_curve is not None,
        "members": [m.name for m in weld.members if getattr(m, "name", None)],
    }


def _build_design_stats(part_or_assembly):
    """Aggregate per-type counts + COG (volume- and mass-weighted) for the Scene > Stats panel.

    cog_volume is emitted whenever any contributing object has a non-zero volume.
    cog_mass is only emitted when every physical object successfully contributed a
    mass value — Shape/Pipe/Wall don't expose uniform volume helpers, so their
    presence suppresses cog_mass to keep the aggregate honest."""
    from collections import Counter

    import numpy as np

    from ada import Beam, Plate
    from ada.config import logger
    from ada.extension import design_extension_schema as design_ext

    counts: Counter[str] = Counter()
    vol_total = 0.0
    mass_total = 0.0
    cog_vol_accum = np.zeros(3, dtype=float)
    cog_mass_accum = np.zeros(3, dtype=float)
    all_have_mass = True

    for obj in part_or_assembly.get_all_physical_objects():
        counts[type(obj).__name__.lower()] += 1

        if not isinstance(obj, (Beam, Plate)):
            # No uniform volume / cog API on Shape / Pipe / Wall; their mass
            # contribution is unaccounted for, so cog_mass would mislead.
            all_have_mass = False
            continue

        try:
            cog = np.asarray(obj.get_cog(), dtype=float)
            vol = float(obj.get_volume())
        except Exception as e:
            logger.debug(f"Design stats: skipping {obj.name!r} ({type(e).__name__}: {e})")
            all_have_mass = False
            continue

        if vol > 0:
            vol_total += vol
            cog_vol_accum += cog * vol

        try:
            rho = float(obj.material.model.rho)
        except (AttributeError, TypeError):
            rho = None

        if not rho or rho <= 0:
            all_have_mass = False
        else:
            mass = vol * rho
            if mass > 0:
                mass_total += mass
                cog_mass_accum += cog * mass

    if not counts:
        return None

    cog_volume = None
    if vol_total > 0:
        c = cog_vol_accum / vol_total
        cog_volume = design_ext.COG(x=float(c[0]), y=float(c[1]), z=float(c[2]), total_volume=vol_total)

    cog_mass = None
    if all_have_mass and mass_total > 0:
        c = cog_mass_accum / mass_total
        cog_mass = design_ext.COG(x=float(c[0]), y=float(c[1]), z=float(c[2]), total_mass=mass_total)

    return design_ext.DesignStats(
        cog_volume=cog_volume,
        cog_mass=cog_mass,
        object_counts=dict(counts),
    )
