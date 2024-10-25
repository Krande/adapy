import ada
from ada.config import Config, logger
from ada.fem.meshing import GmshOptions

Config().update_config_globally(
    "meshing_open_viewer_breakpoint_names",
    [
        # "partition_isect_bm_loop",
        # "partition_bm_split_pre",
        # "partition_bm_split_cut_1"
    ],
)

def main():
    corner_points = [(0,0), (1,0), (1,1), (0,1)]

    midpoints_input = [0.5]
    plates = []
    pl_btn = ada.Plate("pl_btn", corner_points, 0.01)
    plates += [pl_btn]
    beams = []
    for j, midp in enumerate(midpoints_input):
        midpoints = [(midp, 0, 0), (midp, 1, 0)]
        midpoints_y = [(0, midp, 0), (1, midp, 0)]
        pl_mid = ada.Plate(f'pl_mid{j}', corner_points, 0.01, origin=midpoints[0], n=(-1,0,0), xdir=(0,0,1))
        plates.append(pl_mid)
        mid_bm = ada.Beam(f'mid_bm{j}', *midpoints, sec="IPE100")
        beams.append(mid_bm)
        mid_bm_y = ada.Beam(f'mid_bm_y{j}', *midpoints_y, sec="IPE100")
        beams.append(mid_bm_y)

    beams += ada.Beam.array_from_list_of_coords([(*x,0) for x in corner_points], sec="IPE100", make_closed=True)
    columns = []
    normals = [(0,1,0), (-1,0,0), (0,-1,0), (1,0,0)]

    for i, (x,y) in enumerate(corner_points):
        # columns.append(ada.Beam(f"col{i}", (x,y,0), (x,y,1), sec="IPE100"))
        # pl_side = ada.Plate(f"pl_side{i}", corner_points, 0.01, origin=(x,y,0), n=normals[i], xdir=(0,0,1))
        # plates.append(pl_side)
        pass

    p = ada.Part('Stru') / (*beams, *columns, *plates)
    p.show()

    p.fem = p.to_fem_obj(0.2, use_quads=False, options=GmshOptions(Mesh_Algorithm=6))
    p.fem.show()


if __name__ == "__main__":
    logger.setLevel("INFO")
    main()
