"""History data reads back as steps: the procedure, its loads, BCs and output requests.

Before this the reader read no step at all -- a deck's analysis was model data only, the history
section reported keyword by keyword as unread, and an earlier step's *Boundary was taken for a
model one (history data was cut at the LAST *Step, not the first).
"""

from __future__ import annotations

import textwrap

import numpy as np
import pytest

import ada
from ada.fem.loads.fe_loads import LoadTypes
from ada.fem.steps import (
    StepEigen,
    StepEigenComplex,
    StepExplicit,
    StepImplicitDynamic,
    StepImplicitStatic,
    StepSteadyState,
)

_MODEL = """\
*Node
1, 0., 0., 0.
2, 1., 0., 0.
3, 1., 1., 0.
4, 0., 1., 0.
5, 0., 0., 1.
*Element, type=C3D4, elset=solid
1, 1, 2, 3, 5
2, 1, 3, 4, 5
*Nset, nset=base
1, 2, 3, 4
*Nset, nset=tip
5,
*Surface, type=ELEMENT, name=skin
solid, S1
*Solid Section, elset=solid, material=steel
,
*Material, name=steel
*Elastic
2.1e11, 0.3
*Density
7850.,
*Amplitude, name=ramp
0., 0., 0.5, 0.25, 1., 1.
"""


def _read(tmp_path, history: str, model: str = _MODEL) -> ada.Assembly:
    path = tmp_path / "deck.inp"
    path.write_text(model + textwrap.dedent(history))  # model text is flush left already
    return ada.from_fem(path, "abaqus")


def _only_step(a: ada.Assembly):
    (step,) = a.fem.steps
    return step


def test_a_static_step_reads_with_its_increments_stabilization_and_restart(tmp_path):
    step = _only_step(
        _read(
            tmp_path,
            """\
            *Step, name=pull, nlgeom=YES, inc=250, unsymm=YES
            *Static, stabilize=0.0002, allsdtol=0.05
            0.1, 1., 1e-08, 0.5
            *Restart, write, frequency=3
            *End Step
            """,
        )
    )
    assert isinstance(step, StepImplicitStatic)
    assert (step.name, step.nl_geom, step.total_incr) == ("pull", True, 250)
    assert (step.init_incr, step.total_time, step.min_incr, step.max_incr) == (0.1, 1.0, 1e-08, 0.5)
    abq = step.options.ABAQUS
    assert abq.unsymm is True and abq.restart_int == 3
    assert (abq.stabilize.factor, abq.stabilize.allsdtol) == (0.0002, 0.05)


def test_each_procedure_reads_as_its_step_type(tmp_path):
    a = _read(
        tmp_path,
        """\
        *Step, name=dyn
        *Dynamic, application=QUASI-STATIC, initial=NO
        0.01, 2., 1e-06, 0.1
        *End Step
        *Step, name=exp
        *Dynamic, Explicit
        , 0.5
        *End Step
        *Step, name=eig
        *Frequency, eigensolver=Lanczos
        12,
        *End Step
        *Step, name=ceig
        *Complex Frequency, friction damping=YES
        6,
        *End Step
        """,
    )
    dyn, exp, eig, ceig = a.fem.steps
    assert isinstance(dyn, StepImplicitDynamic) and dyn.total_time == 2.0
    assert dyn.options.ABAQUS.init_accel_calc is False
    assert isinstance(exp, StepExplicit) and exp.total_time == 0.5
    assert type(eig) is StepEigen and eig.num_eigen_modes == 12
    assert isinstance(ceig, StepEigenComplex) and ceig.num_eigen_modes == 6 and ceig.friction_damping is True
    # every step owns its options: the class default is one shared instance
    assert len({id(s.options) for s in a.fem.steps}) == 4


def test_a_steady_state_step_reads_its_range_damping_and_unit_load(tmp_path):
    step = _only_step(
        _read(
            tmp_path,
            """\
            *Step, name=ssd
            *Steady State Dynamics, frequency scale=LINEAR, interval=RANGE
             0.5, 20., 100
            *Global Damping, alpha=0.1, beta=0.02
            ** Name: unit   Type: Concentrated force
            *Cload, op=NEW
            tip, 3, 1.
            *End Step
            """,
        )
    )
    assert isinstance(step, StepSteadyState)
    assert (step.fmin, step.fmax, step.alpha, step.beta) == (0.5, 20.0, 0.1, 0.02)
    assert step.unit_load.name == "unit" and step.unit_load.fem_set.name == "tip"


def test_loads_read_with_their_components_amplitude_and_follower_flag(tmp_path):
    step = _only_step(
        _read(
            tmp_path,
            """\
            *Step, name=s
            *Static
            1., 1.
            ** Name: push_F   Type: Concentrated force
            *Cload, amplitude=ramp, follower
            tip, 1, 10.
            tip, 3, -2.5
            ** Name: push_M   Type: Moment
            *Cload, amplitude=ramp, follower
            tip, 5, 4.
            *Cload
            3, 2, 7.
            ** Name: g   Type: Gravity
            *Dload
            , GRAV, 9.81, 0., 0., -1.
            ** Name: acc   Type: Acceleration
            *Dload
            , GRAV, 2., 1., 0., 0.
            *Dsload
            skin, P, 1000.
            *End Step
            """,
        )
    )
    loads = {ld.name: ld for ld in step.loads}
    push = loads["push"]
    assert push.fem_set.name == "tip" and push.amplitude.name == "ramp" and push.follower_force is True
    assert np.allclose(push.forces, [10.0, 0.0, -2.5, 0.0, 4.0, 0.0])
    (label,) = [ld for name, ld in loads.items() if name not in ("push", "g", "acc") and ld.type == LoadTypes.FORCE]
    assert [n.id for n in label.fem_set.members] == [3] and np.allclose(label.forces, [0, 7, 0, 0, 0, 0])
    assert loads["g"].type == LoadTypes.GRAVITY and loads["g"].magnitude == 9.81
    assert loads["acc"].type == LoadTypes.ACC and loads["acc"].magnitude == 2.0
    (pressure,) = [ld for ld in step.loads if ld.type == LoadTypes.PRESSURE]
    assert pressure.magnitude == 1000.0 and pressure.surface.name == "skin"


def test_a_transformed_point_load_reads_its_coordinate_system(tmp_path):
    """The writer's (and CAE's) convention: the transform sits on a ``_T-<set>`` node set that
    holds the load's set; that set is scaffolding and is consumed into the load's ``csys``."""
    model = _MODEL + (
        "*Nset, nset=side\n5,\n*Nset, nset=_T-side, internal\nside,\n"
        "** Transform: skew\n*Transform, nset=_T-side\n0., 1., 0., -1., 0., 0.\n"
    )
    a = _read(
        tmp_path,
        """\
            *Step, name=s
            *Static
            1., 1.
            ** Name: side   Type: Concentrated force
            *Cload
            side, 1, 5.
            *End Step
            """,
        model,
    )
    (load,) = _only_step(a).loads
    assert load.csys.name == "skew"
    assert np.allclose(load.csys.coords[:2], [[0, 1, 0], [-1, 0, 0]])
    assert "_t-side" not in {s.name.lower() for p in a.get_all_parts_in_assembly(True) for s in p.fem.sets}


def test_output_requests_read_as_field_and_history_outputs(tmp_path):
    step = _only_step(
        _read(
            tmp_path,
            """\
            *Step, name=s
            *Static
            1., 1.
            ** FIELD OUTPUT: fields
            *Output, field, number interval=2
            *Node Output
            U, RF
            *Element Output
            S, E
            ** HISTORY OUTPUT: tip_u
            *Output, history, frequency=1
            *Node Output, nset=tip
            U1, U2
            ** HISTORY OUTPUT: energy
            *Output, history, frequency=1
            *Energy Output
            ALLIE, ALLKE
            *End Step
            """,
        )
    )
    (fo,) = step.field_outputs
    assert (fo.name, fo.nodal, fo.element, fo.int_value) == ("fields", ["U", "RF"], ["S", "E"], 2)
    hist = {h.name: h for h in step.hist_outputs}
    assert hist["tip_u"].type == "node" and hist["tip_u"].fem_set.name == "tip"
    assert hist["tip_u"].variables == ["U1", "U2"]
    assert hist["energy"].type == "energy" and hist["energy"].variables == ["ALLIE", "ALLKE"]


def test_a_boundary_in_the_first_of_two_steps_is_step_data_not_model_data(tmp_path):
    a = _read(
        tmp_path,
        """\
        *Boundary
        base, ENCASTRE
        *Step, name=first
        *Static
        1., 1.
        *Boundary
        tip, 3, 3, 0.01
        *End Step
        *Step, name=second
        *Static
        1., 1.
        *End Step
        """,
    )
    model_bcs = [bc for p in a.get_all_parts_in_assembly(include_self=True) for bc in p.fem.bcs]
    assert [bc.fem_set.name for bc in model_bcs] == ["base"]
    first, second = a.fem.steps
    assert [bc.fem_set.name for bc in first.bcs.values()] == ["tip"] and len(second.bcs) == 0


def test_amplitudes_read_as_exact_pairs(tmp_path):
    a = _read(tmp_path, "")
    amp = a.fem.amplitudes["ramp"]
    assert list(amp.x) == [0.0, 0.5, 1.0] and list(amp.y) == [0.0, 0.25, 1.0]


@pytest.mark.parametrize("text", ["", "*Step, name=a\n*Static\n1., 1.\n*End Step\n" * 2])
def test_parse_step_takes_exactly_one_step(tmp_path, text):
    from ada.fem.formats.abaqus.read.read_steps import parse_step

    assert parse_step(text, _read(tmp_path, "")) is None


def test_a_load_or_step_the_reader_cannot_hold_is_reported_not_dropped_silently(tmp_path):
    from ada.fem.formats import conversion_report

    with conversion_report.collect() as report:
        a = _read(
            tmp_path,
            """\
            *Step, name=s
            *Static
            1., 1.
            *Dload
            solid, P1, -10.
            *End Step
            *Step, name=nothing
            *End Step
            """,
        )
    omitted = {(f.keyword, f.subject.split(":")[0]) for f in report.findings if f.kind == "omitted"}
    assert ("*DLOAD", "s") in omitted and ("*STEP", "nothing") in omitted
    assert [s.name for s in a.fem.steps] == ["s"] and len(a.fem.steps[0].loads) == 0
