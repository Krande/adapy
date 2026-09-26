"""The authority: run the emitted script in a real Abaqus, then load the model it built.

Skipped, never failed, when no Abaqus install is present -- there are four CAE tokens on the site
server, so this is a local and pre-merge gate, not a CI one.

The headline is `test_the_cantilever_deflection_ratio_is_the_sections_inertia_ratio`. A cantilever is
loaded along the direction adapy says is ``n1``, then along the direction adapy says is ``n2``; the
ratio of the two tip deflections must be the section's own ``Iy / Iz``. A *ratio* is used so no unit
agreement and no material calibration is needed -- E, P and L all cancel. For an IPE300 that ratio is
13.27, so a swapped axis reads as 0.075 and is unmissable rather than subtle.

**The acceptance model must keep its asymmetric member, and here is exactly what it buys.** A pure
reversal of ``n1`` is invisible to *every* deflection measurement, on any section: every second moment
of area is quadratic in position and therefore invariant under a 180 degree rotation. So the claim
"an asymmetric profile catches a sign flip" is not quite right, and the sign is pinned instead by
`test_cae_orientation_convention.py`. What the asymmetric member does catch, and what no doubly
symmetric member can, is a **mirrored frame** -- ``n2 = n1 x t`` where the convention is ``t x n1``,
or a profile placed left-handed. That reverses the sign of the product of inertia, and with it the
sign of the cross-axis deflection this test measures. Do not "simplify" the L away to an I-beam: the
I-beam's ``Iyz`` is zero and the handedness check goes with it.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

import ada

from .abaqus_runner import ERROR_MARKERS, abaqus_available, run_cae_script
from .conftest import require_writer

require_writer()

pytestmark = pytest.mark.skipif(
    not abaqus_available(), reason="no Abaqus install found (set ADA_ABAQUS_CMD to point at abqXXXX.bat)"
)

E_MODULUS = 210e9
TIP_LOAD = 1000.0
LENGTH = 6.0

#: Abaqus integrates a beam profile numerically with `integration=DURING_ANALYSIS`, so a ratio taken
#: from deflections and a ratio taken from adapy's closed-form section properties do not agree to the
#: last bit. The residual is systematic, not noise -- these are deterministic FE runs. Measured on
#: this very model, Abaqus 2025: 2.7e-4 for the IPE300's deflection ratio, 2.9e-3 for the angle's, and
#: 5.6e-3 / 8.4e-3 for the angle's two coupling ratios. So the headroom below is roughly 4x on the
#: symmetric case and 1.2x on the asymmetric one; a future Abaqus that integrates an L differently
#: could need the latter widened, and that is a real difference to look at, not noise to absorb.
RATIO_TOL_SYMMETRIC = 1e-3
RATIO_TOL_ASYMMETRIC = 1e-2

_FLOAT = r"[-+0-9.eE]+"


def _assert_ran(run) -> None:
    if run.licence_denied:
        pytest.skip("Abaqus is installed but no CAE licence was available:\n" + run.stdout)
    assert not run.failed, "the emitted script did not run cleanly:\n" + run.describe()


def emit(part: ada.Part, workdir: pathlib.Path, driver: str, name: str = "model") -> pathlib.Path:
    """Write the writer's script, then append a driver that loads, solves or interrogates it."""
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / (name + ".py")
    part.to_abaqus_cae_script(script)
    with script.open("a", encoding="utf-8") as handle:
        handle.write("\n\n# --- appended by tests/core/cadit/cae/test_cae_licensed_acceptance.py\n")
        handle.write(driver)
    return script


# --------------------------------------------------------------------------- the specimen


def test_the_specimen_script_is_accepted_by_the_kernel(specimen_script, tmp_path):
    """What makes the mutation specimen an artefact rather than an opinion.

    Every CAE call in `files/specimen_frame_cae.py` is exercised here, so the graph checker's teeth are
    demonstrated against a script Abaqus itself accepts.
    """
    run = run_cae_script(specimen_script, tmp_path)

    _assert_ran(run)
    assert "BUILD OK" in run.printed, run.describe()
    # 3 wires, and the brace landing mid-span splits the girder: 4 edges, all sectioned.
    assert run.value("edges") == "4 all sectioned", run.describe()


def test_the_runner_reports_a_failed_build_as_failed(frame_run, tmp_path):
    """The runner's own teeth, and the reason it does not trust the launcher's exit status.

    `abq2025.bat` returns 0 whatever the CAE process it started did, so a failed build has to announce
    itself. This runs a copy of the writer's own script with one section name corrupted -- the defect a
    missing profile mapping would produce -- and asserts that the writer's banner and its sidecar both
    say so.
    """
    _, good = frame_run
    _assert_ran(good)
    source = good.script.read_text(encoding="utf-8")
    anchor = "sectionName='sec_IPE300_S355'"
    assert source.count(anchor) == 1, "anchor matched {} times, not one".format(source.count(anchor))
    broken = tmp_path / "broken.py"
    broken.write_text(source.replace(anchor, "sectionName='sec_NEVER_CREATED'"), encoding="utf-8")

    run = run_cae_script(broken, tmp_path)

    if run.licence_denied:
        pytest.skip("no CAE licence available")
    assert run.failed, "a script that could not build its model was reported as a clean run: " + run.describe()
    assert "BUILD FAILED banner" in run.failure_signals, run.describe()
    assert "build-result sidecar" in run.failure_signals, run.describe()
    result = run.build_results[0]
    assert result["ok"] is False
    assert result["errors"], "the sidecar records no reason for the failure"


def test_a_failure_that_leaves_through_sys_exit_is_still_detected(specimen_script, tmp_path):
    """The measurement that makes the exit status unusable, pinned as a test.

    CAE treats a ``SystemExit`` escaping a ``noGUI=`` script as a clean finish: the process status is 0
    and *no* ``Abaqus Error`` line is printed anywhere. The specimen exits that way on purpose, so
    corrupting one of its section names produces exactly the run that a status-based or stdout-based
    harness would call a pass. Only the sidecar shows it -- which is why the writer uses ``os._exit``
    and why this runner reads the sidecar.
    """
    source = specimen_script.read_text(encoding="utf-8")
    anchor = 'sectionName="sec_IPE300"'
    assert source.count(anchor) == 1, "anchor matched {} times, not one".format(source.count(anchor))
    broken = tmp_path / "specimen_broken.py"
    broken.write_text(source.replace(anchor, 'sectionName="sec_NEVER_CREATED"'), encoding="utf-8")

    run = run_cae_script(broken, tmp_path)

    if run.licence_denied:
        pytest.skip("no CAE licence available")
    assert run.returncode == 0, "if sys.exit now reaches the process status, simplify the runner"
    assert not any(marker in run.stdout for marker in ERROR_MARKERS), run.describe()
    assert run.failure_signals == ["build-result sidecar"], run.describe()
    sidecar = tmp_path / "specimen_frame_cae.cae_build_result.json"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["error"], "the sidecar records no reason"


# ------------------------------------------------------------------- the writer's own output

FRAME_DRIVER = """
from mesh import ElemType

_m = mdb.models['Model-1']
_p = _m.parts['Frame']
print('PROBE edges {0}'.format(len(_p.edges)))
print('PROBE assignments {0}'.format(len(_p.sectionAssignments)))
for _i in range(len(_p.sectionAssignments)):
    _sa = _p.sectionAssignments[_i]
    print('PROBE assignment {0} {1}'.format(_sa.region[0], _sa.sectionName))
print('PROBE orientations {0}'.format(len(_p.beamSectionOrientations)))
for _i in range(len(_p.beamSectionOrientations)):
    _bso = _p.beamSectionOrientations[_i]
    print('PROBE orientation {0} {1}'.format(_bso.region, _bso.n1))
for _k in sorted(_m.profiles.keys()):
    print('PROBE profile {0} {1}'.format(_k, _m.profiles[_k].__class__.__name__))
print('PROBE profile_h {0}'.format(_m.profiles['IPE300'].h))
for _e in _p.edges:
    print('PROBE edge {0} {1}'.format(_e.index, _e.pointOn))

_p.seedPart(size=100.0, deviationFactor=0.1, minSizeFactor=0.1)
_p.setElementType(regions=(_p.edges,), elemTypes=(ElemType(elemCode=B31, elemLibrary=STANDARD),))
_p.generateMesh()
_m.StaticStep(name='Step-1', previous='Initial')
mdb.Job(name='frame_export', model='Model-1').writeInput(consistencyChecking=OFF)
print('PROBE inp frame_export.inp')
"""


@pytest.fixture(scope="session")
def frame_run(tmp_path_factory):
    """Build the frame in CAE once, interrogate it in-kernel, and export an INP from it."""
    box = ada.Section("BG200x200x10", "BG", h=0.2, w_top=0.2, w_btn=0.2, t_w=0.01, t_ftop=0.01, t_fbtn=0.01)
    angle = ada.Section("HP200x10", "HP", h=0.2, w_btn=0.12, t_w=0.010, t_fbtn=0.012)
    part = ada.Part("Frame")
    part / (
        ada.Beam("col1", (0, 0, 0), (0, 0, 4), "IPE300", "S355"),
        ada.Beam("girder", (0, 0, 4), (6, 0, 4), box, "S355"),
        ada.Beam("brace", (3, 0, 4), (3, 2, 0), angle, "S355"),
    )
    assembly = ada.Assembly("CaeFrame") / part

    workdir = tmp_path_factory.mktemp("cae_frame")
    script = emit(part, workdir, FRAME_DRIVER, name="frame")
    run = run_cae_script(script, workdir)
    return assembly, run


def test_the_writers_script_builds_the_model_it_describes(frame_run):
    """The build guards are the script's own; this asserts on what the kernel holds afterwards."""
    _, run = frame_run
    _assert_ran(run)

    # The brace lands mid-span of the girder, so the girder is two sub-edges: 4 in total.
    assert run.value("PROBE edges") == "4"
    assert run.value("PROBE assignments") == "3"
    assert run.value("PROBE orientations") == "3"
    assignments = dict(line.split(" ", 1) for line in run.values("PROBE assignment"))
    assert assignments == {
        "brace": "sec_HP200x10_S355",
        "col1": "sec_IPE300_S355",
        "girder": "sec_BG200x200x10_S355",
    }
    profiles = dict(line.split(" ", 1) for line in run.values("PROBE profile"))
    assert profiles == {"BG200x200x10": "BoxProfile", "HP200x10": "LProfile", "IPE300": "IProfile"}
    assert float(run.value("PROBE profile_h")) == pytest.approx(0.3)
    assert len(run.values("PROBE edge")) == 4


def test_the_orientations_read_back_from_the_kernel_are_the_beams_yvecs(frame_run):
    """`p.beamSectionOrientations[i].n1` -- note the attribute name, it is not `beamOrientations`."""
    assembly, run = frame_run
    _assert_ran(run)

    read_back = {}
    for line in run.values("PROBE orientation"):
        match = re.match(r"\('(?P<set>[^']+)'.*?\((?P<n1>{f},\s*{f},\s*{f})\)".format(f=_FLOAT), line)
        assert match, "could not read an orientation back from {!r}".format(line)
        read_back[match.group("set")] = tuple(float(v) for v in match.group("n1").split(","))

    assert sorted(read_back) == ["brace", "col1", "girder"]
    for name, n1 in sorted(read_back.items()):
        beam = assembly.get_by_name(name)
        assert n1 == pytest.approx(
            tuple(float(v) for v in beam.yvec), abs=1e-9
        ), "member {!r}: CAE holds n1 {} but adapy's yvec is {}".format(name, n1, beam.yvec)


def test_an_inp_exported_from_cae_reads_back_into_adapy(frame_run):
    """The round trip, and the limit that makes it usable.

    adapy's INP reader has no ``RECT`` branch and ``cards.re_beam`` matches ``*Beam Section`` only, so
    ``*Beam General Section`` is invisible to it: a FLATBAR, a CHANNEL or a POLY member reads back as
    *nothing*, and "0 sections against 0 sections" would pass. So the compared model is restricted to
    I / BOX / L, and **the absence of a section is a hard failure here**, not a silent pass.
    """
    assembly, run = frame_run
    _assert_ran(run)
    inp = run.workdir / run.value("PROBE inp")
    assert inp.exists(), "CAE did not write the INP it was asked for:\n" + run.describe()

    read_back = ada.from_fem(inp)
    parts = [p for p in read_back.get_all_parts_in_assembly(True) if len(p.fem.sections) > 0]
    assert len(parts) == 1, "expected one part with sections, got {}".format([p.name for p in parts])
    fem = parts[0].fem

    expected = {beam.name: beam for beam in assembly.get_all_physical_objects(by_type=ada.Beam)}
    assert len(fem.sections) == len(expected), (
        "{} of {} members survived the round trip -- absence is the failure this test exists to "
        "catch, not a pass".format(len(fem.sections), len(expected))
    )
    for section in fem.sections:
        beam = expected[section.name]
        assert section.section.type == beam.section.type, "member {!r} changed section type".format(beam.name)
        assert tuple(float(v) for v in section.local_y) == pytest.approx(
            tuple(float(v) for v in beam.yvec), abs=1e-6
        ), "member {!r}: n1 did not survive the CAE round trip".format(beam.name)

    # Coordinates first: the unit and placement class of error.
    corners = {tuple(round(float(v), 9) for v in node.p) for node in fem.nodes}
    for beam in expected.values():
        start, end = beam.axis_global()
        for point in (start, end):
            assert (
                tuple(round(float(v), 9) for v in point) in corners
            ), "member {!r} endpoint {} is not among the nodes CAE exported".format(beam.name, point)


# --------------------------------------------------------------- the cantilever acceptance check

CANTILEVER_DRIVER = """
from mesh import ElemType

_m = mdb.models['Model-1']
_p = _m.parts['Cantilever']
_p.seedPart(size=__SEED__, deviationFactor=0.1, minSizeFactor=0.1)
# B33 is Euler-Bernoulli: no transverse shear flexibility, so the deflection ratio is the
# inertia ratio and nothing else.
_p.setElementType(regions=(_p.edges,), elemTypes=(ElemType(elemCode=B33, elemLibrary=STANDARD),))
_p.generateMesh()
print('PROBE mesh {0} elements {1} nodes'.format(len(_p.elements), len(_p.nodes)))

_a = _m.rootAssembly
_inst = _a.instances[sorted(_a.instances.keys())[0]]
_m.StaticStep(name='along_n1', previous='Initial')
_m.StaticStep(name='along_n2', previous='along_n1')

_MEMBERS = __MEMBERS__
for _name, _root, _tip, _n1, _n2 in _MEMBERS:
    _a.Set(name='root_' + _name, vertices=_inst.vertices.findAt((_root,)))
    _a.Set(name='tip_' + _name, vertices=_inst.vertices.findAt((_tip,)))
    _m.EncastreBC(name='fix_' + _name, createStepName='Initial', region=_a.sets['root_' + _name])
    _m.ConcentratedForce(name='load1_' + _name, createStepName='along_n1',
                         region=_a.sets['tip_' + _name],
                         cf1=__LOAD__ * _n1[0], cf2=__LOAD__ * _n1[1], cf3=__LOAD__ * _n1[2])
    _m.ConcentratedForce(name='load2_' + _name, createStepName='along_n2',
                         region=_a.sets['tip_' + _name],
                         cf1=__LOAD__ * _n2[0], cf2=__LOAD__ * _n2[1], cf3=__LOAD__ * _n2[2])
    _m.loads['load1_' + _name].deactivate('along_n2')

_job = mdb.Job(name='cantilever', model='Model-1')
_job.submit(consistencyChecking=OFF)
_job.waitForCompletion()
_odb = session.openOdb(name='cantilever.odb')
for _step in ('along_n1', 'along_n2'):
    _u = _odb.steps[_step].frames[-1].fieldOutputs['U']
    for _name, _root, _tip, _n1, _n2 in _MEMBERS:
        _values = _u.getSubset(region=_odb.rootAssembly.nodeSets[('tip_' + _name).upper()]).values
        for _v in _values:
            print('PROBE U {0} {1} {2!r} {3!r} {4!r}'.format(
                _step, _name, _v.data[0], _v.data[1], _v.data[2]))
_odb.close()
"""


@pytest.fixture(scope="session")
def cantilever_run(tmp_path_factory):
    """Two cantilevers: a doubly symmetric IPE300 and an unequal-leg angle with a real ``Iyz``."""
    angle = ada.Section("HPu", "HP", h=0.2, w_btn=0.12, t_w=0.010, t_fbtn=0.012)
    part = ada.Part("Cantilever")
    part / (
        ada.Beam("cant_i", (0, 0, 0), (LENGTH, 0, 0), "IPE300", "S355"),
        ada.Beam("cant_l", (0, 4, 0), (LENGTH, 4, 0), angle, "S355"),
    )
    assembly = ada.Assembly("Cant") / part

    members = []
    for beam in sorted(part.beams, key=lambda b: b.name):
        start, end = beam.axis_global()
        n1 = tuple(float(v) for v in beam.yvec)
        # Abaqus defines n2 = t x n1; adapy's up is that same vector, and both writers rely on it.
        n2 = tuple(float(v) for v in beam.up)
        members.append(
            (
                beam.name,
                tuple(round(float(v), 9) for v in start),
                tuple(round(float(v), 9) for v in end),
                n1,
                n2,
            )
        )

    workdir = tmp_path_factory.mktemp("cae_cantilever")
    driver = (
        CANTILEVER_DRIVER.replace("__SEED__", "0.5")
        .replace("__LOAD__", repr(TIP_LOAD))
        .replace("__MEMBERS__", repr(tuple(members)))
    )
    script = emit(part, workdir, driver, name="cantilever")
    run = run_cae_script(script, workdir)
    return assembly, run


def deflections(run) -> dict[tuple[str, str], tuple[float, float, float]]:
    out = {}
    for line in run.values("PROBE U"):
        step, name, ux, uy, uz = line.split(" ")
        out[(step, name)] = (float(ux), float(uy), float(uz))
    return out


def along(vector, direction) -> float:
    return sum(v * d for v, d in zip(vector, direction))


@pytest.mark.parametrize(
    "member,tolerance",
    [("cant_i", RATIO_TOL_SYMMETRIC), ("cant_l", RATIO_TOL_ASYMMETRIC)],
)
def test_the_cantilever_deflection_ratio_is_the_sections_inertia_ratio(cantilever_run, member, tolerance):
    """Load along n1, then along n2: the tip deflections are in the ratio Iy / Iz.

    A ratio, so E, the load and the length all cancel and neither units nor material need to agree
    between adapy and Abaqus. The only thing left in it is which way round the profile is turned.
    """
    assembly, run = cantilever_run
    _assert_ran(run)
    beam = assembly.get_by_name(member)
    n1 = tuple(float(v) for v in beam.yvec)
    n2 = tuple(float(v) for v in beam.up)
    measured = deflections(run)

    d1 = along(measured[("along_n1", member)], n1)
    d2 = along(measured[("along_n2", member)], n2)

    expected = beam.section.properties.Iy / beam.section.properties.Iz
    assert d1 / d2 == pytest.approx(expected, rel=tolerance), (
        "member {!r}: deflection ratio {:.6g} but Iy/Iz is {:.6g}. A ratio near its reciprocal means "
        "n1 and n2 are swapped.".format(member, d1 / d2, expected)
    )


def test_the_symmetric_member_shows_no_cross_axis_deflection(cantilever_run):
    """The control: an IPE300 has Iyz == 0, so loading along n1 may not deflect along n2."""
    assembly, run = cantilever_run
    _assert_ran(run)
    beam = assembly.get_by_name("cant_i")
    assert beam.section.properties.Iyz == 0.0, "precondition: an IPE300's product of inertia is zero"
    n1 = tuple(float(v) for v in beam.yvec)
    n2 = tuple(float(v) for v in beam.up)
    displacement = deflections(run)[("along_n1", "cant_i")]

    coupling = along(displacement, n2) / along(displacement, n1)

    assert abs(coupling) < 1e-6, "a doubly symmetric section should not deflect off-axis"


def test_the_asymmetric_member_deflects_off_axis_by_its_own_product_of_inertia(cantilever_run):
    """What the asymmetric member is *for*: it catches a mirrored frame, which no I-beam can.

    For a tip load along local 1, unsymmetric bending gives ``d2 / d1 = -I12 / I11`` -- in adapy's
    names, ``-Iyz / Iy``. Reverse the handedness of the local frame (``n2 = n1 x t`` instead of
    ``t x n1``, or a profile placed as its own mirror image) and ``Iyz`` changes sign, so this ratio
    changes sign while every deflection *magnitude* stays exactly as it was.

    This is also the test that fails if somebody replaces the angle with an I-beam: ``Iyz`` would be
    zero and the expected coupling would collapse to nothing.
    """
    assembly, run = cantilever_run
    _assert_ran(run)
    beam = assembly.get_by_name("cant_l")
    properties = beam.section.properties
    assert properties.Iyz != 0.0, "the acceptance model has lost its asymmetric member"
    n1 = tuple(float(v) for v in beam.yvec)
    n2 = tuple(float(v) for v in beam.up)
    measured = deflections(run)

    coupling_1 = along(measured[("along_n1", "cant_l")], n2) / along(measured[("along_n1", "cant_l")], n1)
    coupling_2 = along(measured[("along_n2", "cant_l")], n1) / along(measured[("along_n2", "cant_l")], n2)

    assert coupling_1 == pytest.approx(-properties.Iyz / properties.Iy, rel=RATIO_TOL_ASYMMETRIC), (
        "off-axis deflection under a load along n1 is {:.6g}; -Iyz/Iy is {:.6g}. A sign difference is "
        "a mirrored local frame.".format(coupling_1, -properties.Iyz / properties.Iy)
    )
    assert coupling_2 == pytest.approx(-properties.Iyz / properties.Iz, rel=RATIO_TOL_ASYMMETRIC)
