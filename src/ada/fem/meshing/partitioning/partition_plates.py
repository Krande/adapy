from __future__ import annotations

from ada.config import Config, logger
from ada.core.clash_check import (
    PlateConnections,
    filter_away_beams_along_plate_edges,
    find_beams_connected_to_plate,
)
from ada.fem.meshing import GmshSession


def fragment_plates(plate_con: PlateConnections, gmsh_session: GmshSession):
    """fragment plates (ie. making all interfaces conformal) that are connected at their edges"""
    logger.info("Running 'fragment_plates' partitioning function")
    br_names = Config().meshing_open_viewer_breakpoint_names

    if br_names is not None and "pre_fragment_plates" in br_names:
        gmsh_session.open_gui()

    for pl1, con_plates in plate_con.edge_connected.items():
        pl1_gmsh_obj = gmsh_session.model_map[pl1]
        for pl2 in con_plates:
            pl2_gmsh_obj = gmsh_session.model_map[pl2]
            res, res_map = gmsh_session.model.occ.fragment(
                pl1_gmsh_obj.entities, pl2_gmsh_obj.entities, removeTool=False, removeObject=False
            )

            num_object_entities = len(pl1_gmsh_obj.entities)

            # Split res_map into two parts: one for pl_gmsh_obj.entities and one for bm_gmsh_obj.entities
            object_entities_new = []
            tool_entities_new = []

            for i, new_entities in enumerate(res_map):
                if i < num_object_entities:
                    # These correspond to the original object entities
                    object_entities_new.extend(new_entities)
                else:
                    # These correspond to the original tool entities
                    tool_entities_new.extend(new_entities)

            # Update the entities for both objects
            pl1_gmsh_obj.entities = object_entities_new
            pl2_gmsh_obj.entities = tool_entities_new

            gmsh_session.model.occ.synchronize()
            gmsh_session.check_model_entities()

    if br_names is not None and "post_fragment_plates" in br_names:
        gmsh_session.open_gui()


def partition_intersected_plates(plate_con: PlateConnections, gmsh_session: GmshSession):
    """split plates that have plate connections at their mid-span"""
    logger.info("Running 'partition_intersected_plates' partitioning function")

    for pl1, con_plates in plate_con.mid_span_connected.items():
        pl1_gmsh_obj = gmsh_session.model_map[pl1]

        for pl2 in con_plates:
            pl2_gmsh_obj = gmsh_session.model_map[pl2]
            if pl2 == pl1:
                continue

            res, res_map = gmsh_session.model.occ.fragment(
                pl1_gmsh_obj.entities, pl2_gmsh_obj.entities, removeObject=False, removeTool=False
            )

            num_object_entities = len(pl1_gmsh_obj.entities)

            # Split res_map into two parts: one for pl_gmsh_obj.entities and one for bm_gmsh_obj.entities
            object_entities_new = []
            tool_entities_new = []

            for i, new_entities in enumerate(res_map):
                if i < num_object_entities:
                    # These correspond to the original object entities
                    object_entities_new.extend(new_entities)
                else:
                    # These correspond to the original tool entities
                    tool_entities_new.extend(new_entities)

            # Update the entities for both objects
            pl1_gmsh_obj.entities = object_entities_new
            pl2_gmsh_obj.entities = tool_entities_new

            gmsh_session.model.occ.synchronize()
            gmsh_session.check_model_entities()

    gmsh_session.check_model_entities()


def split_plates_by_beams(gmsh_session: GmshSession):
    """split plates that are intersected by beams"""
    logger.info("Running 'split_plates_by_beams' partitioning function")
    from ada import Beam, Plate

    br_names = Config().meshing_open_viewer_breakpoint_names
    if br_names is not None and "partition_bm_split_pre" in br_names:
        gmsh_session.open_gui()

    beams = [obj for obj in gmsh_session.model_map.keys() if type(obj) is Beam]
    if len(beams) == 0:
        return None

    plates = [obj for obj in gmsh_session.model_map.keys() if type(obj) is Plate]
    for pl in plates:
        pl_gmsh_obj = gmsh_session.model_map[pl]
        int_bm_map = dict()
        all_contained_beams = find_beams_connected_to_plate(pl, beams)
        inside_beams = filter_away_beams_along_plate_edges(pl, all_contained_beams)
        if len(inside_beams) == 0:
            continue

        for bm in inside_beams:
            bm_gmsh_obj = gmsh_session.model_map[bm]
            for li_dim, li_ent in bm_gmsh_obj.entities:
                int_bm_map[(li_dim, li_ent)] = bm_gmsh_obj

            res, res_map = gmsh_session.model.occ.fragment(
                pl_gmsh_obj.entities, bm_gmsh_obj.entities, removeTool=False, removeObject=False
            )
            num_object_entities = len(pl_gmsh_obj.entities)

            # Split res_map into two parts: one for pl_gmsh_obj.entities and one for bm_gmsh_obj.entities
            object_entities_new = []
            tool_entities_new = []

            for i, new_entities in enumerate(res_map):
                if i < num_object_entities:
                    # These correspond to the original object entities
                    object_entities_new.extend(new_entities)
                else:
                    # These correspond to the original tool entities
                    tool_entities_new.extend(new_entities)

            # Update the entities for both objects
            pl_gmsh_obj.entities = object_entities_new
            bm_gmsh_obj.entities = tool_entities_new

            gmsh_session.model.occ.synchronize()
            gmsh_session.check_model_entities()

            if br_names is not None and "partition_bm_split_cut" in br_names:
                gmsh_session.open_gui()
