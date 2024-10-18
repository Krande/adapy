from __future__ import annotations

from ada.config import Config
from ada.core.clash_check import PlateConnections, filter_away_beams_along_plate_edges, find_beams_connected_to_plate
from ada.fem.meshing import GmshSession


def fragment_plates(plate_con: PlateConnections, gmsh_session: GmshSession):
    """fragment plates (ie. making all interfaces conformal) that are connected at their edges"""

    for pl1, con_plates in plate_con.edge_connected.items():
        pl1_gmsh_obj = gmsh_session.model_map[pl1]
        intersecting_plates = []
        for pl_entity in pl1_gmsh_obj.entities:
            pl1_dim, pl1_ent = pl_entity

            for pl2 in con_plates:
                pl2_gmsh_obj = gmsh_session.model_map[pl2]

                for pl2_dim, pl2_ent in pl2_gmsh_obj.entities:
                    intersecting_plates.append((pl2_dim, pl2_ent))

            gmsh_session.model.occ.fragment(intersecting_plates, [(pl1_dim, pl1_ent)], removeTool=False)
            gmsh_session.model.occ.synchronize()


def partition_intersected_plates(plate_con: PlateConnections, gmsh_session: GmshSession):
    """split plates that have plate connections at their mid-span"""

    for pl1, con_plates in plate_con.mid_span_connected.items():
        pl1_gmsh_obj = gmsh_session.model_map[pl1]
        intersecting_plates = set()
        replaced_pl_entities = []
        for pl_entity in pl1_gmsh_obj.entities:
            pl1_dim, pl1_ent = pl_entity

            for pl2 in con_plates:
                pl2_gmsh_obj = gmsh_session.model_map[pl2]

                for pl2_dim, pl2_ent in pl2_gmsh_obj.entities:
                    intersecting_plates.add((pl2_dim, pl2_ent))

            res, res_map = gmsh_session.model.occ.fragment(
                list(intersecting_plates), [(pl1_dim, pl1_ent)], removeTool=False
            )
            replaced_pl_entities += [(dim, r) for dim, r in res if dim == 2]

        pl1_gmsh_obj.entities = replaced_pl_entities
        gmsh_session.model.occ.synchronize()


def split_plates_by_beams(gmsh_session: GmshSession):
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
        for pl_dim, pl_ent in pl_gmsh_obj.entities:
            intersecting_beams = []
            int_bm_map = dict()
            all_contained_beams = find_beams_connected_to_plate(pl, beams)
            inside_beams = filter_away_beams_along_plate_edges(pl, all_contained_beams)
            for bm in inside_beams:
                bm_gmsh_obj = gmsh_session.model_map[bm]
                for li_dim, li_ent in bm_gmsh_obj.entities:
                    intersecting_beams.append((li_dim, li_ent))
                    int_bm_map[(li_dim, li_ent)] = bm_gmsh_obj

            # Using Embed fails during meshing
            # res = self.model.mesh.embed(1, [t for e,t in intersecting_beams], 2, pl_ent)

            res, res_map = gmsh_session.model.occ.fragment(intersecting_beams, [(pl_dim, pl_ent)], removeTool=False, removeObject=False)

            if br_names is not None and "partition_bm_split_cut_1" in br_names:
                gmsh_session.open_gui()

            replaced_pl_entities = [(dim, r) for dim, r in res if dim == 2]
            if len(replaced_pl_entities) == 0:
                continue
            for i, int_bm in enumerate(intersecting_beams):
                bm_gmsh_obj = int_bm_map[int_bm]
                new_ents = res_map[i]
                bm_gmsh_obj.entities = new_ents
            pl_gmsh_obj.entities = replaced_pl_entities

            gmsh_session.model.occ.synchronize()
            if br_names is not None and "partition_bm_split_cut" in br_names:
                gmsh_session.open_gui()

    gmsh_session.model.occ.synchronize()
