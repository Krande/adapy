from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import trimesh

    from ada import Assembly, Part
    from ada.visit.scene_converter import SceneConverter


def scene_from_part_or_assembly(part_or_assembly: Part | Assembly, converter: SceneConverter) -> trimesh.Scene:
    import ada.extension.design_extension_schema as design_ext
    from ada import Assembly, Beam, Plate
    from ada.comms.msg_handling.object_metadata import beam_metadata, plate_metadata
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

    scene = None
    if params.stream_from_ifc_store:
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
                object_metadata[obj.name] = beam_metadata(
                    obj.name, obj.section, obj.material
                )
            elif isinstance(obj, Plate):
                object_metadata[obj.name] = plate_metadata(
                    obj.name, obj.t, obj.material
                )
    converter.ada_ext.design_objects.append(
        design_ext.DesignDataExtension(
            name=part_or_assembly.name,
            description=type(part_or_assembly).__name__,
            groups=groups,
            node_references=design_ext.DesignNodeReference(faces=list(nodes_geom)),
            object_guids=object_guids or None,
            object_metadata=object_metadata or None,
            stats=_build_design_stats(part_or_assembly),
        )
    )

    return scene


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
        cog_volume = design_ext.COG(
            x=float(c[0]), y=float(c[1]), z=float(c[2]), total_volume=vol_total
        )

    cog_mass = None
    if all_have_mass and mass_total > 0:
        c = cog_mass_accum / mass_total
        cog_mass = design_ext.COG(
            x=float(c[0]), y=float(c[1]), z=float(c[2]), total_mass=mass_total
        )

    return design_ext.DesignStats(
        cog_volume=cog_volume,
        cog_mass=cog_mass,
        object_counts=dict(counts),
    )
