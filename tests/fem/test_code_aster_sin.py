"""Code_Aster results as a Sesam SIN (``options.CODE_ASTER.write_sin``), for tools that read Sestra's.

The converter (:mod:`ada.fem.formats.code_aster.results.to_sin`) was measured against Sestra V11.3 on a 6300-element
topside (see its docstring); the sign conventions it carries are those. Here: the pieces it is built of, and a
stiffened plate pulled along its stiffener, whose stresses and forces are known in closed form.
"""

from __future__ import annotations

import numpy as np
import pytest

import ada
from ada.fem import Bc, FemSet, LoadCase, LoadPoint, StepImplicitStatic
from ada.fem.formats.code_aster.common import med_cell_values
from ada.fem.formats.code_aster.results.to_sin import beam_mid_forces, shell_frame
from ada.fem.formats.sesam.read.deck_records import read_deck_records
from ada.materials.metals import CarbonSteel

LX, LY, T, F = 2.0, 1.0, 0.010, 100e3


def _stiffened_plate() -> ada.Assembly:
    """A 2 x 1 m, 10 mm plate in the xy plane with a FB100x10 stiffener along x at y = 0.5, clamped at x = 0.

    One load case, ``pull``: F along +x, spread over the nodes of the free edge.
    """
    mat = ada.Material("S355", CarbonSteel("S355"))
    pl = ada.Plate("pl", [(0, 0), (LX, 0), (LX, LY), (0, LY)], T, mat=mat)
    bm = ada.Beam("st", (0, LY / 2, 0), (LX, LY / 2, 0), "FB100x10", mat, up=(0, 0, 1))
    a = ada.Assembly("A") / (ada.Part("P") / [pl, bm])
    a.fem = a.to_fem_obj(0.25)
    root = [n for n in a.fem.nodes if abs(n.x) < 1e-6]
    tip = [n for n in a.fem.nodes if abs(n.x - LX) < 1e-6]
    a.fem.add_bc(Bc("fix", a.fem.add_set(FemSet("root", root, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    tip_set = a.fem.add_set(FemSet("tip", tip, FemSet.TYPES.NSET))
    step = a.fem.add_step(StepImplicitStatic("static"))
    load = LoadPoint("pull", F / len(tip), tip_set, 1)
    step.add_load(load)
    step.add_loadcase(LoadCase("pull", None, loads=[load]))
    return a


def test_med_cell_values_reads_component_by_component():
    # 2 cells, 3 points each, 2 components, stored MED_NO_INTERLACE: component-major, then cell, then point.
    co = np.array([11, 12, 13, 21, 22, 23, 111, 112, 113, 121, 122, 123], dtype=float)
    v = med_cell_values(co, 2, 3)
    assert v.shape == (2, 3, 2)
    assert v[0, :, 0].tolist() == [11, 12, 13] and v[1, :, 1].tolist() == [121, 122, 123]


def test_beam_mid_forces_is_exact_for_a_uniform_load():
    # Sesam's beam forces: dMXZ/dx = NXY and dMXY/dx = -NXZ (measured against Sestra; to_sin.MID_MOMENT_SIGNS).
    length = 1.7
    c_z, c_y = 3.0, -5.0  # the moments' curvature, as a uniform distributed load gives

    def forces(x):
        mxz, mxy = 2.0 + 0.4 * x + 0.5 * c_z * x**2, -1.0 + 0.3 * x + 0.5 * c_y * x**2
        nxy, nxz = 0.4 + c_z * x, -(0.3 + c_y * x)
        return np.array([7.0, nxy, nxz, 0.2, mxy, mxz])

    mid = beam_mid_forces(forces(0.0), forces(length), length)
    assert mid == pytest.approx(forces(length / 2))


def test_gelref_reads_a_per_node_reference_from_the_tail():
    from ada.fem.formats.code_aster.results.to_sin import _GEONO, _TRANSNO, _gelref

    plain = [7, 1, 0, 0, 0, 0, 0, 0, 3, 0, 0, 5]
    assert (_gelref(plain, _GEONO, 2), _gelref(plain, _TRANSNO, 2)) == (3, 5)
    # GEONO and TRANSNO per node: the tail holds the two GEONOs, then the two TRANSNOs.
    per_node = [7, 1, 0, 0, 0, 0, 0, 0, -1, 0, 0, -1, 3, 3, 5, 5]
    assert (_gelref(per_node, _GEONO, 2), _gelref(per_node, _TRANSNO, 2)) == (3, 5)


def test_shell_frame_projects_the_reference_vector():
    quad = np.array([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], dtype=float)
    frame = shell_frame(quad, np.array([1.0, 1.0, 0.3]))
    assert frame[2] == pytest.approx([0, 0, 1])
    assert frame[0] == pytest.approx(np.array([1, 1, 0]) / np.sqrt(2))
    assert np.cross(frame[0], frame[1]) == pytest.approx(frame[2])


def test_deck_records_are_what_a_sin_holds(tmp_path):
    a = _stiffened_plate()
    a.to_fem("d", "sesam", scratch_dir=tmp_path, overwrite=True, model_data_only=True)
    deck = read_deck_records(next((tmp_path / "d").glob("*T1.FEM")))
    assert len(deck.get("GNODE")) == len(a.fem.nodes) == len(deck.get("GCOORD"))
    assert len(deck.get("GELMNT1")) == len(a.fem.elements)
    # Padded to whole lines of four, as Sestra stores them; NFIELD-first records without their NFIELD.
    assert all(len(r) % 4 == 0 for r in deck.get("GELMNT1"))
    assert deck.get("GSETMEMB") and all(r[-1] != 0 for r in deck.get("GSETMEMB"))  # members to the end, no padding
    assert "FB100x10" in {name for _, name in deck.text["TDSECT"]}
    assert "DATE" not in deck.numeric and "IEND" not in deck.numeric


def test_a_stiffened_plate_in_tension_reads_back_from_the_sin(tmp_path, require_solver):
    require_solver("code_aster")
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    a = _stiffened_plate()
    a.fem.options.CODE_ASTER.write_sin = True
    res = a.to_fem("sp", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True)
    sin = tmp_path / "sp" / "spR1.SIN"
    assert sin.exists(), "no SIN beside the Code_Aster results"

    with open_sin(sin) as s:
        assert [t for _, t in s.iter_text_records("TDRESREF")] == ["pull"]
        rblodcmb = [list(r) for r in s.iter_records("RBLODCMB")]
        disp = {int(r[1]): np.asarray(r[4:7]) for r in s.iter_records("RVNODDIS")}
        stresses = [np.asarray(r) for r in s.iter_records("RVSTRESS")]
        rdpoints = [np.asarray(r) for r in s.iter_records("RDPOINTS")]
        frames = {int(r[1]): r[-9:].reshape(3, 3) for r in rdpoints}
        # Point id, x, y, z per result point, between the 8 header words and the 9 of the frame.
        points = {int(r[1]): r[8:-9].reshape(-1, 4)[:, 1:] for r in rdpoints}
        forces = [np.asarray(r[4:]).reshape(3, 6) for r in s.iter_records("RVFORCES")]
    assert len(rblodcmb) == 1 and rblodcmb[0][3:5] == [1.0, 1.0]  # result case 1 is load case 1 at factor 1

    # The displacements are the .rmed's.
    depl = next(np.asarray(r.values, float) for r in res.results if r.name.endswith("DEPL"))
    ux = np.array([disp[int(n)][0] for n in depl[:, 0]])
    assert ux == pytest.approx(depl[:, 1], rel=1e-5, abs=1e-12)  # node ids alike, internal = external here

    # The shells' local x is the projected reference vector, so each element's membrane stress is taken to global axes
    # with the frame RDPOINTS carries; the result points give its area (the bottom surface's corners, in the xy plane).
    sxx, areas = [], []
    for r in stresses:
        eid = int(r[1])
        vals = r[4:].reshape(-1, 3).mean(axis=0)
        loc = np.array([[vals[0], vals[2], 0], [vals[2], vals[1], 0], [0, 0, 0]])
        sxx.append((frames[eid].T @ loc @ frames[eid])[0, 0])
        pts = points[eid]
        corner = [0, 1, 4, 3] if len(pts) == 10 else [0, 1, 3]  # n1 n2 (c) n4 n3 / n1 n2 (c) n3, then the other side
        xy = np.array([pts[k][:2] for k in corner])
        areas.append(0.5 * abs(np.dot(xy[:, 0], np.roll(xy[:, 1], -1)) - np.dot(xy[:, 1], np.roll(xy[:, 0], -1))))
    sxx, areas = np.array(sxx), np.array(areas)
    assert areas.sum() == pytest.approx(LX * LY, rel=1e-6)
    # Plate and stiffener carry the pull between them at every section, so averaged along the length too: the
    # plate's area-weighted mean stress times its section, plus the stiffener's mean axial force (tension positive,
    # as Sestra's NXX), is F.
    nxx = np.array([f[:, 0].mean() for f in forces])
    plate_force = (sxx * areas).sum() / LX * T
    assert plate_force + nxx.mean() == pytest.approx(F, rel=0.02)
    assert nxx.min() > 0 and plate_force > 0
    assert np.median(sxx) == pytest.approx(F / (LY * T + 0.100 * 0.010), rel=0.05)
