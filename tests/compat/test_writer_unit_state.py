"""Regression: the OCC STEP writer must pin ``xstep.cascade.unit`` itself.

The cascade unit is a process-global (default MM, also mutated by every
STEPStore read). Unpinned, a fresh process exporting a metre-based model wrote
every coordinate 1000x off (a 3 m beam became "0.003" in a METRE-declared
file); test suites masked it because earlier reader tests leaked M into the
global. Poisoning the global here makes the assertion order-independent."""

import re

import pytest

import ada
from ada import Beam, Section


def test_to_stp_writes_correct_scale_with_poisoned_cascade_unit(tmp_path):
    OCCInterface = pytest.importorskip("OCC.Core.Interface", reason="pythonocc writer under test")

    # Simulate the fresh-process default / a prior mm-read leaking into the global.
    OCCInterface.Interface_Static.SetCVal("xstep.cascade.unit", "MM")

    a = ada.Assembly("m") / (ada.Part("p") / Beam("bm", (0, 0, 0), (3, 0, 0), Section("s", from_str="IPE300")))
    src = tmp_path / "m.step"
    a.to_stp(src)

    txt = src.read_text(errors="replace")
    assert "SI_UNIT($,.METRE.)" in txt or "SI_UNIT(.METRE.)" in txt
    xs = [abs(float(p.split(",")[0])) for p in re.findall(r"CARTESIAN_POINT\('[^']*',\(([^)]+)\)", txt)]
    # The 3 m beam's coordinates must come out in metres (~3.0), not mm-misread (~0.003).
    assert max(xs) == pytest.approx(3.0, abs=0.2), f"max |x| {max(xs)} — cascade-unit scaling regressed"
