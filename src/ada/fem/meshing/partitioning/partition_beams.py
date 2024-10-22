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


def split_intersecting_beams(gmsh_session: GmshSession, margins=5e-5, out_of_plane_tol=0.1, point_tol=Config().general_point_tol):
    from ada import Beam, Node
    from ada.api.containers import Beams, Nodes
    from ada.core.clash_check import basic_intersect, are_beams_connected

    br_names = Config().meshing_open_viewer_breakpoint_names

    all_beams = [obj for obj in gmsh_session.model_map.keys() if type(obj) is Beam]
    bm_cont = Beams(all_beams)
    if len(all_beams) == 1:
        return None

    nodes = Nodes()
    nmap: dict[Node, list[Beam]] = dict()
    for bm, cbeams in filter(None, [basic_intersect(bm, margins, [bm_cont]) for bm in all_beams]):
        are_beams_connected(bm, cbeams, out_of_plane_tol, point_tol, nodes, nmap)

    for n, beams in nmap.items():
        split_point = gmsh_session.model.occ.addPoint(n.x, n.y, n.z)
        for bm in beams:
            if n == bm.n1 or n == bm.n2:
                continue
            bm_gmsh_obj = gmsh_session.model_map[bm]
            if len(bm_gmsh_obj.entities) != 1:
                # This beam has already been split
                continue

            # entities_1 = gmsh_session.model.occ.get_entities(1)
            res, res_map = gmsh_session.model.occ.fragment(bm_gmsh_obj.entities, [(0, split_point)])

            bm_gmsh_obj.entities = [x for x in res_map[0] if x[0] == 1]
            gmsh_session.model.occ.synchronize()
            if br_names is not None and "partition_isect_bm_loop" in br_names:
                gmsh_session.open_gui()

    gmsh_session.model.occ.synchronize()