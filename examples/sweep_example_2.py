import ada
from ada.param_models.sweep_example import (
    get_three_sweeps_mesh_data,
    sweep1_pts,
    sweep2_pts,
    sweep3_pts,
)


def main():
    wt = 8e-3
    fillet = [(0, 0), (-wt, 0), (0, wt)]

    profile_y = ada.Direction(0, 0, 1)
    sweep1_profile_normal = [0.0, 1.0, 0.0]
    sweep2_profile_normal = [-1.0, 0.0, 0.0]
    sweep3_profile_normal = [1.0, 0.0, 0.0]
    sweep3_profile_xdir = [0, 1, 0]
    derived_reference = True
    sweep1 = ada.PrimSweep(
        "sweep1",
        sweep1_pts,
        fillet,
        profile_normal=sweep1_profile_normal,
        profile_ydir=profile_y,
        color="red",
        derived_reference=derived_reference
    )
    sweep2 = ada.PrimSweep(
        "sweep2",
        sweep2_pts,
        fillet,
        profile_normal=sweep2_profile_normal,
        profile_ydir=profile_y,
        color="blue",
        derived_reference=derived_reference,
    )
    sweep3 = ada.PrimSweep(
        "sweep3",
        sweep3_pts,
        fillet,
        profile_normal=sweep3_profile_normal,
        profile_xdir=sweep3_profile_xdir,
        color="green",
        derived_reference=derived_reference,
    )
    sweeps = [
        sweep1,
        sweep2,
        sweep3,
    ]
    mesh_data = get_three_sweeps_mesh_data()
    mesh1_raw = mesh_data[0]
    mesh1 = sweeps[0].solid_trimesh()
    mesh1_raw_vertices = mesh1_raw["vertices"]
    mesh1_vertices = mesh1.vertices.tolist()
    assert len(mesh1_raw_vertices) == len(mesh1_vertices)

    a = ada.Assembly("part") / sweeps
    export_ifc_file = "temp/swept_shape_example_2.ifc"
    a.to_ifc(export_ifc_file, validate=True)
    a.to_stp("temp/swept_shape_example_2.stp")
    # a = ada.from_ifc(export_ifc_file)
    a.show(stream_from_ifc_store=True)
    a.show(stream_from_ifc_store=False, append_to_scene=True)


if __name__ == "__main__":
    main()
