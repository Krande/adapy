import ada


def test_butt_weld():
    pl_points = [(0, 0), (1, 0.0), (1, 1), (0, 1)]

    pl1 = ada.Plate("pl1", pl_points, 0.01)
    pl2 = ada.Plate("pl2", pl_points, 0.01, placement=ada.Placement((1.0, 0, 0)))

    # Weld definition
    p1 = (1, 0, 0)
    p2 = (1, 1, 0)
    xdir = (-1, 0, 0)

    weld_profile = [(-0.005, 0.01), (0, 0), (0.005, 0.01)]
    groove_profile = [(-0.005, 0.01), (-0.001, 0.002), (-0.001, 0), (0.001, 0), (0.001, 0.002), (0.005, 0.01)]

    wld = ada.Weld("weld1", p1, p2, "V", [pl1, pl2], weld_profile, groove=groove_profile, xdir=xdir)

    a = ada.Assembly() / (pl1, pl2, wld)
    a.to_ifc("temp/welded_plates.ifc", validate=True, file_obj_only=True)
