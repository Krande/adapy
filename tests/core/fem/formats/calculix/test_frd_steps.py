"""The .frd reader: a field belongs to the step ``1PSTEP`` names, and a deck of U1 beams has no elements in the file.

``files/two_steps_u1.frd`` is what ccx 2.23 wrote for a two-element U1 cantilever, a tip load along z in step 1 and one
along y added in step 2 (``CALCULIX_CODE_ASTER.md``). Its ``1PSTEP`` lines are ``<block> <increment> <step>``; the
reader took the increment, so both steps' fields came out as step 1 and ``get_last_step_results`` could not tell them
apart. ccx writes no element of a user element type, so the file's element block is empty, which the reader refused.
"""

from __future__ import annotations

import pathlib

import numpy as np

from ada.fem.formats.calculix.results.read_frd_file import read_from_frd_file_proto

FRD = pathlib.Path(__file__).parent / "files" / "two_steps_u1.frd"


def test_each_field_has_its_own_step():
    res = read_from_frd_file_proto(FRD)
    disp = {f.step: np.asarray(f.values) for f in res.results if f.name == "DISP"}
    assert sorted(disp) == [1, 2]
    tip1, tip2 = disp[1][2], disp[2][2]
    assert np.allclose(tip1[1:4], [0, 0, -1.58730e-04])
    assert np.allclose(tip2[1:4], [0, -1.58730e-04, -1.58730e-04]), "step 2 carries step 1's load"


def test_a_beam_model_reads_as_its_nodes():
    res = read_from_frd_file_proto(FRD)
    assert res.mesh.elements == []
    assert list(res.mesh.nodes.identifiers) == [1, 2, 3]
