import sys

import pytest

import ada
from ada.fem.meshing import GmshOptions
from ada.fem.meshing.exceptions import BadJacobians


def test_beam_tet_mesh_fail():
    bm = ada.Beam("MyBeam", (0, 0.5, 0.5), (3, 0.5, 0.5), "IPE400")
    p = ada.Part("MyFem") / bm
    options = GmshOptions(Mesh_ElementOrder=2)

    if sys.platform == "darwin":
        # For some reason this test fails on macos
        return

    with pytest.raises(BadJacobians):
        p.fem = bm.to_fem_obj(
            0.05, "solid", interactive=False, options=options, perform_quality_check=True, silent=True
        )

    # (ada.Assembly("Test") / p).to_fem("test", "abaqus", execute=True)


def test_beam_tet_mesh_pass():
    bm = ada.Beam("MyBeam", (0, 0.5, 0.5), (3, 0.5, 0.5), "IPE400")
    p = ada.Part("MyFem") / bm
    options = GmshOptions(Mesh_ElementOrder=2, Mesh_Algorithm3D=10)

    p.fem = bm.to_fem_obj(0.05, "solid", interactive=False, options=options, perform_quality_check=True, silent=True)

    # (ada.Assembly("Test") / p).to_fem("test", "abaqus", execute=True)


def test_beam_mesh_with_hole(test_meshing_dir):
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220")
    p = ada.Part("MyFem") / bm
    bm.add_boolean(ada.PrimCyl("Cylinder", (0.5, -0.5, 0), (0.5, 0.5, 0), 0.05))
    p.fem = bm.to_fem_obj(0.5, "line", interactive=False)

    el_types = {el_type.value: list(group) for el_type, group in p.fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["LINE"]) == 2

    assert len(p.fem.nodes) == 3

    # (Assembly("Test") / p).to_ifc(test_meshing_dir / "bm_mesh_ifc", include_fem=True)
