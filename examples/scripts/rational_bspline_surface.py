# Install geomdl with: mamba install -c orbingol geomdl
# !/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    Examples for the NURBS-Python Package
    Released under MIT License
    Developed by Onur Rauf Bingol (c) 2016-2018
"""
import os
import pathlib

from geomdl import BSpline, exchange
from geomdl.visualization import VisVTK as vis

# Import and use Matplotlib's colormaps
from matplotlib import cm

FILES_DIR = [fp for fp in pathlib.Path(__file__).resolve().absolute().parents if fp.name == "examples"][
    0
].parent / "files"


def main():
    # Create a BSpline surface instance
    surf = BSpline.Surface()

    # Set degrees
    surf.degree_u = 3
    surf.degree_v = 3

    # Set control points
    surf.set_ctrlpts(
        *exchange.import_txt(FILES_DIR / "other/rational_bspline_surf_wknots/ex_surface01.cpt", two_dimensional=True)
    )

    # Set knot vectors
    surf.knotvector_u = [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0]
    surf.knotvector_v = [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 3.0, 3.0, 3.0]

    # Set evaluation delta
    surf.delta = 0.025

    # Evaluate surface points
    surf.evaluate()

    # Plot the control point grid and the evaluated surface
    vis_comp = vis.VisSurface()
    surf.vis = vis_comp
    surf.render(colormap=cm.cool)

    # Good to have something here to put a breakpoint
    pass


if __name__ == "__main__":
    main()
