from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from ada.base.physical_objects import BackendGeom
from ada.fem import Elem
from ada.visit.gltf.graph import GraphNode

# Element member lists at or above this length are written as a binary
# bufferView (uint32 IDs) instead of inline string arrays. Below the
# threshold, inline JSON keeps the GLB human-readable. Picked to keep
# small CAD-derived meshes inline while engaging the binary path for
# real FEA models that would otherwise inflate the JSON chunk.
_MEMBER_BUFFER_VIEW_THRESHOLD = 256

if TYPE_CHECKING:
    import trimesh

    from ada import FEM
    from ada.fem import FemSection
    from ada.visit.scene_converter import SceneConverter


def scene_from_fem(fem: FEM, converter: SceneConverter) -> trimesh.Scene:
    """Appends a FE mesh to scene or creates a new scene if no scene is provided."""

    import trimesh

    from ada import Node
    from ada.extension import simulation_extension_schema as sim_meta
    from ada.extension.simulation_extension_schema import FeObjectType

    params = converter.params
    graph = converter.graph

    if fem.parent is not None:
        parent_part_node = graph.hash_map.get(fem.parent.guid)
    else:
        parent_part_node = graph.top_level

    parent_node = graph.add_node(GraphNode(fem.name, graph.next_node_id(), parent=parent_part_node))

    use_solid_beams = params.fea_params is not None and params.fea_params.solid_beams is True

    ms = fem.to_mesh().create_mesh_stores(
        fem.name,
        graph,
        parent_node,
        use_solid_beams=use_solid_beams,
    )

    scene = trimesh.Scene(base_frame=graph.top_level.name) if converter.scene is None else converter.scene

    ms.add_to_scene(scene, graph)

    groups = []
    for fset in fem.sets.sets:
        ftype = FeObjectType.node if fset.type == fset.TYPES.NSET else FeObjectType.element
        members = []

        # Note! The node/elem id are not the right reference iD's, the id needs to refer to the mesh node/elem id.
        for m in fset.members:
            if isinstance(m, (Elem, Node)):
                name = m.id
            elif isinstance(m, BackendGeom):
                name = m.name
            else:
                raise ValueError(f"Unsupported type of set member: {type(m)}")

            if fset.type == fset.TYPES.NSET:
                members.append(f"P{name}")
            else:
                elem_ref = f"EL{name}"
                members.append(elem_ref)

        g = sim_meta.SimGroup(
            name=fset.name,
            members=members,
            parent_name=fem.name,
            description=fset.type,
            fe_object_type=ftype,
        )
        groups.append(g)

    # Lineage groups: one SimGroup per FemSection whose elements were
    # meshed from a CAD beam/plate. Sits alongside the user-defined sets
    # above and carries `parent_object_guid` so the viewer can resolve
    # the CAD parent without name matching. Large groups are written as
    # a bufferView (uint32 IDs) to keep the JSON chunk bounded.
    for fem_sec in _iter_fem_sections(fem):
        sim_group = _build_lineage_simgroup(fem, fem_sec, converter)
        if sim_group is not None:
            groups.append(sim_group)

    sim_data = sim_meta.SimulationDataExtensionMetadata(
        name=fem.name,
        date=datetime.datetime.now(),
        fea_software="N/A",
        fea_software_version="N/A",
        steps=[],
        node_references=sim_meta.SimNodeReference(
            points=ms.points_node_name,
            edges=ms.edges_node_name,
            faces=ms.faces_node_name,
            solid_beams=ms.bm_solid_node_name,
        ),
        groups=groups,
        stats=_build_sim_stats(fem),
        fem_concepts=_build_bc_concepts(fem),
    )
    converter.ada_ext.simulation_objects.append(sim_data)

    return scene


def _build_bc_concepts(fem: FEM):
    """Emit boundary conditions (restrained node positions + dofs) for the
    viewer's FEM visualization mode. Returns None when the FEM has no BCs."""
    from ada import Node
    from ada.extension import fem_concepts_schema as fem_ext

    bcs = []
    for bc in getattr(fem, "bcs", None) or []:
        fset = getattr(bc, "fem_set", None)
        if fset is None:
            continue
        positions = [
            [float(m.p[0]), float(m.p[1]), float(m.p[2])] for m in fset.members if isinstance(m, Node)
        ]
        if not positions:
            continue
        bcs.append(
            fem_ext.BcGlyph(
                name=bc.name,
                positions=positions,
                dofs=[int(d) for d in bc.dofs],
                bc_type=str(getattr(bc, "type", "") or "") or None,
            )
        )

    if not bcs:
        return None
    return fem_ext.FemConcepts(bcs=bcs)


def _build_sim_stats(fem: FEM):
    """Aggregate COG + per-category element counts for the Scene > Stats panel.

    COG is mass-weighted; FEMs whose elements lack assigned materials (or are
    empty) emit only the counts. Counts always reflect what reached the bake."""
    from ada.config import logger
    from ada.extension import simulation_extension_schema as sim_meta

    beam_count = sum(1 for _ in fem.elements.lines)
    shell_count = sum(1 for _ in fem.elements.shell)
    solid_count = sum(1 for _ in fem.elements.solids)
    if beam_count + shell_count + solid_count == 0:
        return None

    counts = sim_meta.ElementCounts(beam=beam_count, shell=shell_count, solid=solid_count)

    cog_block = None
    try:
        cog_result = fem.elements.calc_cog()
        tot_mass = float(cog_result.tot_mass or 0.0)
        if tot_mass > 0:
            p = cog_result.p
            cog_block = sim_meta.COG(
                x=float(p[0]),
                y=float(p[1]),
                z=float(p[2]),
                total_mass=tot_mass,
                total_volume=float(cog_result.tot_vol or 0.0),
            )
    except Exception as e:  # materials/sections missing — stats are best-effort
        logger.debug(f"FEM '{fem.name}' stats: COG skipped ({type(e).__name__}: {e})")

    return sim_meta.SimStats(cog=cog_block, element_counts=counts)


def _iter_fem_sections(fem: FEM):
    yield from fem.sections


def _build_lineage_simgroup(fem: FEM, fem_sec: FemSection, converter: SceneConverter):
    """Build a SimGroup that links a FemSection's elements back to their
    CAD parent's guid, using inline strings for small groups and a uint32
    bufferView for large ones."""
    from ada.extension import simulation_extension_schema as sim_meta
    from ada.extension.simulation_extension_schema import FeObjectType

    refs = getattr(fem_sec, "refs", None)
    if not refs:
        return None
    parent_obj = refs[0]
    parent_guid = getattr(parent_obj, "guid", None)
    if not parent_guid:
        return None
    elset = getattr(fem_sec, "elset", None)
    members = getattr(elset, "members", None) if elset is not None else None
    if not members:
        return None
    elem_ids = [int(e.id) for e in members if isinstance(e, Elem)]
    if not elem_ids:
        return None

    # Element-name prefix matches what the existing per-element-name
    # group emission uses (line 66) so a click on "EL17" resolves to
    # the same SimGroup the lineage path provides.
    prefix = "EL"

    common: dict = dict(
        name=f"lineage::{fem_sec.name}",
        parent_name=fem.name,
        description="adapy lineage group",
        parent_object_guid=parent_guid,
        fe_object_type=FeObjectType.element,
    )

    if len(elem_ids) < _MEMBER_BUFFER_VIEW_THRESHOLD:
        common["members"] = [f"{prefix}{eid}" for eid in elem_ids]
        return sim_meta.SimGroup(**common)

    view_idx = _write_uint32_buffer_view(converter, elem_ids)
    common["members_buffer_view"] = view_idx
    common["members_prefix"] = prefix
    return sim_meta.SimGroup(**common)


def _write_uint32_buffer_view(converter: SceneConverter, ids) -> int:
    """Queue a uint32 element-ID array for emission as a glTF bufferView.

    The bytes are staged on the converter and emitted by
    ``SceneConverter.buffer_postprocessor`` once trimesh has assigned
    real bufferView indices. The returned placeholder integer goes onto
    ``SimGroup.members_buffer_view`` and is rewritten in-place during
    that postprocessor pass."""
    import numpy as np

    buf = np.asarray(list(ids), dtype="<u4").tobytes()
    return converter.queue_lineage_buffer(buf)
