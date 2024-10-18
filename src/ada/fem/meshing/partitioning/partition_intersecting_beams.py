from ada.config import Config
from ada.fem.meshing import GmshSession


def split_crossing_beams(gmsh_session: GmshSession):
    from ada import Beam
    br_names = Config().meshing_open_viewer_breakpoint_names

    beams = [obj for obj in gmsh_session.model_map.keys() if type(obj) is Beam]
    if len(beams) == 1:
        return None

    if br_names is not None and "partition_isect_bm_pre" in br_names:
        gmsh_session.open_gui()

    intersecting_beams = []
    int_bm_map = dict()
    for bm in beams:
        bm_gmsh_obj = gmsh_session.model_map[bm]
        for li_dim, li_ent in bm_gmsh_obj.entities:
            intersecting_beams.append((li_dim, li_ent))
            int_bm_map[(li_dim, li_ent)] = bm_gmsh_obj

    res, res_map = gmsh_session.model.occ.fragment(
        intersecting_beams, intersecting_beams, removeTool=True, removeObject=True
    )

    for i, int_bm in enumerate(intersecting_beams):
        bm_gmsh_obj = int_bm_map[int_bm]
        new_ents = res_map[i]
        bm_gmsh_obj.entities = new_ents

    gmsh_session.model.occ.synchronize()
