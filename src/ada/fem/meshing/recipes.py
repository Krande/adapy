from ada import Beam, Part, Plate
from ada.core.clash_check import find_beams_connected_to_plate

from . import GmshOptions, GmshSession


def mesh_mixed_shell_and_beams(p: Part):
    p.connections.find()
    with GmshSession(silent=True, options=GmshOptions(Mesh_Algorithm=8)) as gs:
        gmap = dict()
        for obj in p.get_all_physical_objects():
            if type(obj) is Beam:
                li = gs.add_obj(obj, geom_repr="line", build_native_lines=False)
                gmap[obj] = li.entities
            elif type(obj) is Plate:
                pl = gs.add_obj(obj, geom_repr="shell")
                gmap[obj] = pl.entities

        beams = list(p.get_all_physical_objects(by_type=Beam))
        gs.open_gui()
        for pl in p.get_all_physical_objects(by_type=Plate):
            intersecting_beams = []
            for pl_dim, pl_ent in gmap[pl]:
                for bm in find_beams_connected_to_plate(pl, beams):
                    for li_dim, li_ent in gmap[bm]:
                        intersecting_beams.append(li_ent)

            gs.model.mesh.embed(1, intersecting_beams, 2, pl_ent)
            gs.model.geo.synchronize()

        gs.mesh(0.1)
        p.fem = gs.get_fem()
