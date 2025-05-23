from ada.config import Config, logger
from ada.fem.meshing import GmshSession


def split_crossing_beams(gmsh_session: GmshSession):
    from ada import Beam

    br_names = Config().meshing_open_viewer_breakpoint_names

    beams = [obj for obj in gmsh_session.model_map.keys() if type(obj) is Beam]
    if len(beams) <= 1:
        return None

    logger.info("Running 'split_crossing_beams' partitioning function")

    if br_names is not None and "partition_isect_bm_pre" in br_names:
        gmsh_session.open_gui()

    for bm in beams:
        bm_gmsh_obj = gmsh_session.model_map[bm]
        for other_bm in beams:
            if bm == other_bm:
                continue
            bm_other_gmsh_obj = gmsh_session.model_map[other_bm]

            res, res_map = gmsh_session.model.occ.fragment(
                bm_gmsh_obj.entities, bm_other_gmsh_obj.entities, removeTool=False, removeObject=True
            )

            num_object_entities = len(bm_gmsh_obj.entities)

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
            bm_gmsh_obj.entities = object_entities_new
            bm_other_gmsh_obj.entities = tool_entities_new
            gmsh_session.model.occ.synchronize()


def split_intersecting_beams(
    gmsh_session: GmshSession, margins=5e-5, out_of_plane_tol=0.1, point_tol=Config().general_point_tol
):
    logger.info("Running 'split_intersecting_beams' partitioning function")
    from ada import Beam, Node
    from ada.api.containers import Beams, Nodes
    from ada.core.clash_check import are_beams_connected, basic_intersect

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
            if n.p.is_equal(bm.n1.p) or n.p.is_equal(bm.n2.p):
                continue

            bm_gmsh_obj = gmsh_session.model_map[bm]
            res, res_map = gmsh_session.model.occ.fragment(bm_gmsh_obj.entities, [(0, split_point)], removeTool=True)
            num_object_entities = len(bm_gmsh_obj.entities)

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
            bm_gmsh_obj.entities = object_entities_new

            gmsh_session.model.occ.synchronize()
            gmsh_session.check_model_entities()

            if br_names is not None and "partition_isect_bm_loop" in br_names:
                gmsh_session.open_gui()
