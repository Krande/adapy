"""The Calculix deck's implicit dynamic step, amplitudes and zero point loads, by their text.

* ``StepImplicitDynamic`` is a ``*DYNAMIC`` step (the writer raised "unsupported Step Type"), with the HHT ``ALPHA``
  and ``DIRECT`` from ``step.options.CALCULIX``.
* An ``Amplitude`` is ``*AMPLITUDE`` model data and a load with one names it (the writer dropped it and applied the
  load in full).
* A zero point load is three explicit zero ``*CLOAD`` lines: what a coupling adapter writes its forces into.
"""

from __future__ import annotations

import re

import pytest

import ada
from ada.fem import (
    Amplitude,
    FemSection,
    FemSet,
    LoadPoint,
    LoadPressure,
    StepImplicitDynamic,
    StepImplicitStatic,
    Surface,
)
from ada.fem.elements import Elem
from ada.fem.formats import conversion_report
from ada.materials.metals.base_models import Metal


def _plate():
    a = ada.Assembly("a")
    p = ada.Part("p")
    a.add_part(p)
    fem = p.fem
    mat = p.add_material(ada.Material("m", Metal(E=1e10, rho=700.0, sig_y=1e8, sig_u=1e8, v=0.3, zeta=0.0, alpha=0.0)))
    pts = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)]
    for i, c in enumerate(pts, start=1):
        fem.nodes.add(ada.Node(c, i, parent=fem))
    node = fem.nodes.from_id
    els = []
    for k, (i, j, m) in enumerate([(1, 2, 3), (1, 3, 4), (2, 5, 6), (2, 6, 3)], start=1):
        e = Elem(k, [node(i), node(j), node(m)], "TRIANGLE", parent=fem)
        fem.add_elem(e)
        els.append(e)
    es = fem.add_set(FemSet("skin", els, "elset", parent=fem))
    fem.add_section(FemSection("sec", "shell", es, mat, thickness=1e-3, parent=fem, theory="membrane"))
    surf = fem.add_surface(Surface("skin_in", Surface.TYPES.ELEMENT, es, el_face_index=-1, parent=fem))
    iface = fem.add_set(FemSet("Ninterface", [node(i) for i in range(1, 7)], "nset", parent=fem))
    fix = fem.add_set(FemSet("fix", [node(1), node(4)], "nset", parent=fem))
    a.fem.add_bc(ada.fem.Bc("fix", fix, [1, 2, 3]))
    return a, p, surf, iface


def _deck(a, tmp_path, name="d"):
    with conversion_report.collect() as report:
        a.to_fem(name, "calculix", scratch_dir=tmp_path, overwrite=True)
    return (tmp_path / name / f"{name}.inp").read_text(), report


def test_an_implicit_dynamic_step_is_a_dynamic_step_with_alpha_and_direct(tmp_path):
    a, p, surf, iface = _plate()
    step = a.fem.add_step(StepImplicitDynamic("dyn", nl_geom=True, total_time=2.0, init_incr=0.01, max_incr=0.01))
    step.options.CALCULIX.alpha = -0.2
    step.options.CALCULIX.direct = True
    step.add_load(LoadPressure("pin", 1000.0, surf))
    inp, _ = _deck(a, tmp_path)
    head = re.search(r"\*Step, nlgeom=YES, inc=\d+\n\*Dynamic([^\n]*)\n([^\n]*)", inp)
    assert head, inp
    assert head.group(1) == ", ALPHA=-2.0000000000000E-01, DIRECT"
    assert [float(v) for v in head.group(2).split(",")[:2]] == [0.01, 2.0]
    assert "*Cload, OP=NEW\n*Dload, OP=NEW" in inp, "a dynamic step starts from no load, as a static one does"


def test_a_dynamic_step_without_options_writes_ccx_defaults_and_options_are_per_step(tmp_path):
    a, p, surf, iface = _plate()
    s1 = a.fem.add_step(StepImplicitStatic("pre", nl_geom=True, total_time=1.0, init_incr=0.5, max_incr=0.5))
    s1.add_load(LoadPressure("pin", 1000.0, surf))
    s2 = a.fem.add_step(StepImplicitDynamic("dyn", nl_geom=True, total_time=1.0, init_incr=0.1, max_incr=0.1))
    s2.options.CALCULIX.direct = True
    assert s1.options.CALCULIX.direct is False, "a step's solver options are its own"
    inp, _ = _deck(a, tmp_path)
    dyn = inp.split("** STEP: dyn")[1]
    assert "*Dynamic, DIRECT\n" in dyn
    assert "skin, P, 1000.0" in dyn, "the static step's pressure carries into the dynamic step"


def test_an_alpha_outside_the_hht_range_is_refused(tmp_path):
    a, p, surf, iface = _plate()
    step = a.fem.add_step(StepImplicitDynamic("dyn", total_time=1.0, init_incr=0.1, max_incr=0.1))
    step.options.CALCULIX.alpha = -0.5
    with pytest.raises(ValueError, match="alpha"):
        _deck(a, tmp_path)


def test_an_amplitude_is_model_data_and_the_loads_name_it(tmp_path):
    a, p, surf, iface = _plate()
    ramp = Amplitude("ramp", [0.0, 0.5, 10.0], [0.0, 1.0, 1.0])
    step = a.fem.add_step(StepImplicitDynamic("dyn", nl_geom=True, total_time=1.0, init_incr=0.1, max_incr=0.1))
    step.add_load(LoadPressure("pin", 1000.0, surf, amplitude=ramp))
    step.add_load(LoadPoint("tip", 5.0, iface, 3, amplitude=ramp))
    inp, report = _deck(a, tmp_path)
    model, steps = inp.split("** STEP: dyn")
    block = re.search(r"\*Amplitude, name=ramp\n([^\n*]*)", model)
    assert block, model
    values = [float(v) for v in block.group(1).split(",")]
    assert values == [0.0, 0.0, 0.5, 1.0, 10.0, 1.0]
    assert "*Dload, AMPLITUDE=ramp\nskin, P, 1000.0" in steps
    assert "*Cload, AMPLITUDE=ramp\nNinterface, 3, 5.0000000000000E+00" in steps
    assert not [f for f in report.findings if "amplitude" in f.message.lower()], "the amplitude is no longer dropped"


def test_a_zero_point_load_is_three_explicit_zero_cload_lines(tmp_path):
    a, p, surf, iface = _plate()
    step = a.fem.add_step(StepImplicitDynamic("dyn", nl_geom=True, total_time=1.0, init_incr=0.1, max_incr=0.1))
    step.add_load(LoadPoint("coupling", 0.0, iface, 1))
    inp, _ = _deck(a, tmp_path)
    block = re.search(r"Type: Concentrated force \(zero\)\n\*Cload\n((?:[^\n*]+\n){3})", inp)
    assert block, inp
    assert block.group(1).split("\n")[:3] == [f"Ninterface, {d}, 0.0000000000000E+00" for d in (1, 2, 3)]
