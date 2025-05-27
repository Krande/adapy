import os
import subprocess

import ada


def main():
    w = 5
    pl = ada.Plate("pl1", [(0, 0), (w, 0), (w, w), (0, w)], 0.01)
    beams = ada.Beam.array_from_list_of_coords(pl.poly.points3d, "IPE300", make_closed=True)
    p = ada.Part("myPart") / (pl, *beams)
    pipe = ada.Pipe("pipe1", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1.5, 0), (3, 1.5, 0)], "OD200x5")

    copied_p = p.copy_to("my_copied_part", (0, 0, 1))
    copied_p.placement = copied_p.placement.rotate((0, 0, 1), 45)
    p_top = ada.Part("TopPart") / (p, copied_p, pipe)
    p_top.show(embed_glb=True)

    a = ada.Assembly() / p_top
    a.to_ifc("temp/my_model.ifc")
    a.to_genie_xml("temp/my_model.xml")


if __name__ == "__main__":
    main()
