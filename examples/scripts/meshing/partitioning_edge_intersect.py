import os
import pathlib
import subprocess

from dotenv import load_dotenv

import ada
from ada.config import Config, logger
from ada.fem import Elem
from ada.fem.shapes.definitions import LineShapes
from ada.visit.renderer_manager import FEARenderParams, RenderParams

load_dotenv()


def edges_intersect(use_xact=False):
    # Place the following code wherever in the code to break
    # br_names = Config().meshing_open_viewer_breakpoint_names
    # if br_names is not None and "partition_isect_pl_after_fragment" in br_names:
    #    gmsh_session.open_gui()

    Config().update_config_globally(
        "meshing_open_viewer_breakpoint_names",
        [
            # "partition_isect_bm_loop",
            # "partition_isect_bm_pre",
            # "partition_bm_split_cut_1"
        ],
    )
    bm_name = ada.Counter(1, "bm")
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)
    points = pl.poly.points3d
    objects = [pl]

    # Beams along 3 of 4 along circumference
    for p1, p2 in zip(points[:-1], points[1:]):
        objects.append(ada.Beam(next(bm_name), p1, p2, "IPE100"))

    # Beam along middle in x-dir
    bmx = ada.Beam(next(bm_name), (0, 0.5, 0.0), (1, 0.5, 0), "IPE100")
    objects.append(bmx)

    # Beam along diagonal
    bm_diag = ada.Beam(next(bm_name), (0, 0, 0.0), (1, 1, 0), "IPE100")
    objects.append(bm_diag)

    a = ada.Assembly() / (ada.Part("MyPart") / objects)
    p = a.get_part("MyPart")
    extra_props = {}
    gltf_extra = os.getenv("GLTF_ASSET_EXTRAS_KEY_VAL", None)
    if gltf_extra:
        key, val = gltf_extra.split(";")
        extra_props[key] = val
    a.show(
        params_override=RenderParams(
            gltf_export_to_file="temp/design_model.glb",
            gltf_asset_extras_dict=extra_props if len(extra_props) > 0 else None,
        )
    )

    p.fem = p.to_fem_obj(0.1, use_quads=False, interactive=False)
    p.fem.show(
        params_override=RenderParams(
            gltf_export_to_file="temp/fea_model.glb",
            gltf_asset_extras_dict=extra_props if len(extra_props) > 0 else None,
            fea_params=FEARenderParams(solid_beams=True),
        )
    )

    if use_xact:
        xact = os.getenv("XACT_EXE")
        if xact:
            fem_file = pathlib.Path("temp/scratch/MyIntersectingedge/ufo_bulk.fem").resolve().absolute().as_posix()
            subprocess.run([xact, fem_file])

    n = p.fem.nodes.get_by_volume(p=(0.5, 0.5, 0))[0]
    num_line_elem = len(list(filter(lambda x: isinstance(x, Elem) and x.type == LineShapes.LINE, n.refs)))
    assert num_line_elem == 4


if __name__ == "__main__":
    logger.setLevel("INFO")
    edges_intersect(use_xact=False)
