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
"an asymmetric profile catches a sign flip" is not quite right. What the asymmetric member does catch,
and what no doubly symmetric member can, is a **mirrored frame** -- ``n2 = n1 x t`` where the
convention is ``t x n1``, or a profile placed left-handed. That reverses the sign of the product of
inertia, and with it the sign of the cross-axis deflection this test measures. Do not "simplify" the L
away to an I-beam: the I-beam's ``Iyz`` is zero and the handedness check goes with it.

**The sign itself is pinned here too, by a first moment instead of a second one.** Second moments are
invariant under a 180 degree rotation; a *centre of mass* is not, and CAE reports one for a sectioned
wire with no analysis job at all. `test_the_wider_flange_sits_below_the_beam_axis` gives a member an
I-section whose bottom flange is wider than its top and asserts that the centre of mass CAE computes
lies **below** the beam axis, by exactly the centroid offset of the outline *adapy itself draws* for
that section. That is the only check in this repo that settles ``n1``'s sign against adapy's geometry
rather than against another writer: the cross-writer equality in `test_cae_orientation_convention.py`
asserts that the CAE writer agrees with the INP writer, and the INP writer's own sign has never been
validated against anything physical -- so an error shared by both would pass it in silence.

`test_a_channel_model_passes_abaqus_datacheck` is the other measurement-shaped test here, and it
exists because a plausible mapping already failed it once: CAE has a ``ChannelProfile``, and the deck
CAE writes from one is rejected by Abaqus' own preprocessor. Nothing short of running the solver's
input processor says so, which is what this test does.
"""

from __future__ import annotations

import json
import math
import pathlib
import re

import pytest

import ada
from ada.materials.metals import CarbonSteel

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

    adapy's INP reader has no ``RECT`` branch, so a FLATBAR or a POLY member reads back as *nothing*,
    and "0 sections against 0 sections" would pass. So the compared model is restricted to I / BOX / L,
    and **the absence of a section is a hard failure here**, not a silent pass.

    Members are matched by **elset**, not by the section's name. Abaqus/CAE writes the section's own
    name in the ``** Section:`` comment above the block and the member's name on ``elset=``, and the
    reader reads that comment -- so a section comes back as ``sec_BG200x200x10_S355`` rather than
    inheriting its set's name. That is the right way round; a section renamed after one of its sets was
    the bug. It does mean the elset is what still carries the member, so that is what this matches on.
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
        member = section.elset if isinstance(section.elset, str) else section.elset.name
        assert member in expected, "section {!r} came back on elset {!r}, which is not a member".format(
            section.name, member
        )
        beam = expected[member]
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


# ------------------------------------------------- the sign of n1, from a first moment of area

#: Bottom flange (``w_btn``, which Abaqus calls ``b1``) deliberately wider than the top one, and every
#: other dimension distinct. adapy's own I outline puts ``w_btn`` at ``-h/2``, so the centroid of this
#: section sits *below* the beam axis -- which is the whole measurement.
UNEQUAL_I = dict(h=0.81, w_btn=0.41, w_top=0.31, t_w=0.021, t_ftop=0.025, t_fbtn=0.029)

CENTROID_DRIVER = """
from mesh import ElemType
import regionToolset

_m = mdb.models['Model-1']
_p = _m.parts['Centroid']
_set = _p.sets['unequal_i']
_n1 = _p.beamSectionOrientations[0].n1
_section_name = _p.sectionAssignments[0].sectionName
print('PROBE n1 {0!r} {1!r} {2!r}'.format(_n1[0], _n1[1], _n1[2]))
_p.seedPart(size=1.0, deviationFactor=0.1, minSizeFactor=0.1)
_p.setElementType(regions=(_p.edges,), elemTypes=(ElemType(elemCode=B31, elemLibrary=STANDARD),))
_p.generateMesh()


def _report(tag, part, region):
    props = part.getMassProperties(regions=region)
    com = props['centerOfMass']
    print('PROBE {0} volume {1!r}'.format(tag, props['volume']))
    print('PROBE {0} com {1!r} {2!r} {3!r}'.format(tag, com[0], com[1], com[2]))


_report('written', _p, regionToolset.Region(edges=_set.edges))

# The control: the same wire, the same section, n1 negated and nothing else changed. If the
# measurement above were insensitive to the sign these two would come back identical, so the
# test asserts on both.
_q = _m.Part(name='Flipped', dimensionality=THREE_D, type=DEFORMABLE_BODY)
_q.WirePolyLine(points=((__P1__, __P2__),), mergeType=IMPRINT, meshable=ON)
_flipped = _q.Set(name='all', edges=_q.edges)
_q.SectionAssignment(region=_flipped, sectionName=_section_name)
_q.assignBeamSectionOrientation(region=_flipped, method=N1_COSINES,
                               n1=(-_n1[0], -_n1[1], -_n1[2]))
_q.seedPart(size=1.0, deviationFactor=0.1, minSizeFactor=0.1)
_q.setElementType(regions=(_q.edges,), elemTypes=(ElemType(elemCode=B31, elemLibrary=STANDARD),))
_q.generateMesh()
_report('flipped', _q, regionToolset.Region(edges=_flipped.edges))
"""


def outline_centroid_along_up(sec: ada.Section) -> float:
    """Where adapy's *own drawn outline* puts this section's centroid, along the profile height.

    Deliberately not ``sec.properties`` and deliberately not the INP writer: this is the polygon
    :func:`ada.sections.profiles.build_section_profile` produces, which is what adapy renders and
    exports everywhere else. Local 2D ``y`` is the profile height, which Abaqus reaches as
    ``n2 = t x n1`` and adapy calls ``beam.up``.
    """
    from ada.sections.profiles import build_section_profile

    points = [(float(p[0]), float(p[1])) for p in build_section_profile(sec, True).outer_curve.points2d]
    twice_area = 0.0
    moment = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        cross = x0 * y1 - x1 * y0
        twice_area += cross
        moment += (y0 + y1) * cross
    return moment / (3.0 * twice_area)


@pytest.fixture(scope="session")
def centroid_run(tmp_path_factory):
    """One member with an unequal-flange I, measured in the kernel -- no analysis job needed."""
    sec = ada.Section("IUNEQUAL", "IG", **UNEQUAL_I)
    part = ada.Part("Centroid")
    part / ada.Beam("unequal_i", (0, 0, 0), (LENGTH, 0, 0), sec, "S355")
    assembly = ada.Assembly("Centroid") / part
    beam = part.beams[0]
    start, end = beam.axis_global()

    workdir = tmp_path_factory.mktemp("cae_centroid")
    driver = CENTROID_DRIVER.replace("__P1__", repr(tuple(round(float(v), 9) for v in start))).replace(
        "__P2__", repr(tuple(round(float(v), 9) for v in end))
    )
    script = emit(part, workdir, driver, name="centroid")
    return assembly, run_cae_script(script, workdir)


def measured_mass_properties(run, tag) -> tuple[float, tuple[float, ...]]:
    volume = float(run.value("PROBE {0} volume".format(tag)))
    com = tuple(float(v) for v in run.value("PROBE {0} com".format(tag)).split(" "))
    return volume, com


def test_the_wider_flange_sits_below_the_beam_axis(centroid_run):
    """The sign of ``n1``, settled against adapy's geometry by a measurement in the CAE kernel.

    Every second moment of area is quadratic in position, so ``n1`` and ``-n1`` give identical bending
    stiffness about both axes and no deflection measurement can tell them apart. First moments are
    linear and are therefore *not* invariant: put the wider flange of an I-section on one side of the
    axis and the centre of mass moves to that side. ``part.getMassProperties()`` reports it, in the
    same run that built the model and with no solver involved.

    So: ``w_btn`` 0.41 against ``w_top`` 0.31, and adapy's own outline puts ``w_btn`` at ``-h/2``.
    Projected on ``beam.up`` -- the direction Abaqus derives as ``n2 = t x n1`` -- the centre of mass
    must be negative, and equal to the centroid of that outline. Measured on Abaqus 2025:
    ``-0.0441890415587341`` against adapy's outline centroid ``-0.044189041558734175``. Agreement to
    the last digit either number carries, which makes this an identity rather than a tolerance.

    Flip ``n1``'s sign anywhere between ``Beam.yvec`` and the emitted script and this is what notices.
    `test_cae_orientation_convention.py` compares the CAE writer against the INP writer, and the INP
    writer's sign has never been checked against anything physical -- so if both were wrong the same
    way, that comparison would agree and only this test would fail.
    """
    assembly, run = centroid_run
    _assert_ran(run)
    beam = assembly.get_by_name("unequal_i")
    sec = beam.section
    assert sec.w_btn > sec.w_top, "precondition: the bottom flange is the wider one"
    up = tuple(float(v) for v in beam.up)

    volume, com = measured_mass_properties(run, "written")

    # First, that CAE integrated the cross-section adapy meant -- otherwise the offset below would be
    # the right number for the wrong profile. 1e-6 and no tighter: CAE integrates a profile
    # numerically and the residual measured here is a systematic 3e-8, while a swapped or dropped
    # dimension moves the area by percent.
    assert volume / LENGTH == pytest.approx(sec.properties.Ax, rel=1e-6), (
        "CAE's section area is {:.9g} but adapy's is {:.9g}: the profile itself did not survive, so "
        "its centroid says nothing".format(volume / LENGTH, sec.properties.Ax)
    )

    offset = along(com, up)
    expected = outline_centroid_along_up(sec)

    assert offset < 0.0, (
        "the centre of mass is {:.6g} along up, but the wider flange (w_btn={:.3g} > w_top={:.3g}) is "
        "drawn at -h/2, so it must lie below the axis. A positive value means n1 is reversed.".format(
            offset, sec.w_btn, sec.w_top
        )
    )
    assert offset == pytest.approx(expected, rel=1e-9), (
        "CAE puts the centre of mass {:.9g} along up; the centroid of the outline adapy draws for this "
        "section is {:.9g}".format(offset, expected)
    )


def test_reversing_n1_moves_the_centre_of_mass_to_the_other_side(centroid_run):
    """The teeth of the test above, asserted rather than assumed.

    A measurement insensitive to what it claims to measure is worse than none, and "a centre of mass
    detects a sign flip" is the same kind of claim that was already wrong once here in the other
    direction -- the cantilever ratio, which cannot detect one. So the driver builds a second member
    differing from the first *only* in the sign of ``n1``, and this asserts its centre of mass is the
    mirror image: same distance, other side.
    """
    assembly, run = centroid_run
    _assert_ran(run)
    up = tuple(float(v) for v in assembly.get_by_name("unequal_i").up)

    written_volume, written_com = measured_mass_properties(run, "written")
    flipped_volume, flipped_com = measured_mass_properties(run, "flipped")

    assert flipped_volume == pytest.approx(written_volume, rel=1e-12), "the control must differ only in n1"
    assert along(flipped_com, up) == pytest.approx(-along(written_com, up), rel=1e-9), (
        "n1 and -n1 gave centres of mass {:.9g} and {:.9g} along up. Were these equal, the measurement "
        "would be blind to the sign and the test above would prove nothing.".format(
            along(written_com, up), along(flipped_com, up)
        )
    )


# ------------------------------------------- a channel, through the solver's own input processor

CHANNEL_DRIVER = """
from mesh import ElemType

_m = mdb.models['Model-1']
_p = _m.parts['Channels']
for _k in sorted(_m.profiles.keys()):
    print('PROBE profile {0} {1}'.format(_k, _m.profiles[_k].__class__.__name__))
# Weighing the member is the check a generalized section cannot pass: measured, a part whose only
# section is a GeneralizedProfile reports mass=None, because the kernel has no shape to integrate.
_mp = _p.getMassProperties()
print('PROBE mass {0}'.format(_mp['mass']))
print('PROBE com {0}'.format(_mp['centerOfMass']))
_p.seedPart(size=1.0, deviationFactor=0.1, minSizeFactor=0.1)
_p.setElementType(regions=(_p.edges,), elemTypes=(ElemType(elemCode=B31, elemLibrary=STANDARD),))
_p.generateMesh()

_a = _m.rootAssembly
_inst = _a.instances[sorted(_a.instances.keys())[0]]
_a.Set(name='root', vertices=_inst.vertices.findAt(((0.0, 0.0, 0.0),)))
_m.EncastreBC(name='fix', createStepName='Initial', region=_a.sets['root'])
_m.StaticStep(name='Step-1', previous='Initial')

_job = mdb.Job(name='channel_dc', model='Model-1')
_job.submit(consistencyChecking=OFF, datacheckJob=True)
_job.waitForCompletion()
print('PROBE datacheck status {0}'.format(_job.status))
"""


@pytest.fixture(scope="session")
def channel_run(tmp_path_factory):
    """A channel member, built in CAE and then handed to Abaqus' input file processor."""
    part = ada.Part("Channels")
    part / ada.Beam("chan", (0, 0, 0), (4, 0, 0), "UNP200x10", "S355")
    assembly = ada.Assembly("Chan") / part

    workdir = tmp_path_factory.mktemp("cae_channel")
    script = emit(part, workdir, CHANNEL_DRIVER, name="channel")
    return assembly, run_cae_script(script, workdir)


def test_a_channel_model_passes_abaqus_datacheck(channel_run):
    """A channel must reach the solver, and only the solver can say whether it does.

    This is the test the ``ChannelProfile`` mapping failed. CAE has a profile class named after the
    shape; it builds; the model opens in the GUI; and the INP that CAE's own exporter writes from it is
    refused by Abaqus' input file processor::

        ***ERROR: in keyword *BEAMSECTION, file "chan_job.inp", line 29: Illegal value "CHANNEL"
                  for parameter "section".
        ***ERROR: ELEMENT 1 INSTANCE CHANPART-1 IS MISSING A BEAM SECTION DEFINITION

    No test that reads the emitted script, and none that interrogates the CAE model afterwards, can see
    that -- every one of them was green on the mapping that produced it. So a channel goes all the way
    to ``datacheck`` here, and the assertion is on Abaqus' verdict and its own ``.dat``.

    The profile assertion belongs with it: a deck that datachecks clean because the channel had
    silently become something else would satisfy the verdict alone. And the mass is asserted next to
    it, because it separates the two candidates that both datacheck clean. An ``ArbitraryProfile``
    traces the three walls and can be weighed; a ``GeneralizedProfile`` carrying the same five
    properties analyses just as happily and reports ``mass=None``, since there is no shape to
    integrate. Weight is what a model gets handed to someone for.
    """
    _, run = channel_run
    _assert_ran(run)

    profiles = dict(line.split(" ", 1) for line in run.values("PROBE profile"))
    assert profiles == {"UNP200": "ArbitraryProfile"}, (
        "a channel is a traced polyline in CAE, as it is in the INP: CAE's ChannelProfile builds fine "
        "and then cannot be solved, and a GeneralizedProfile cannot be weighed"
    )

    # rho * Ax * L, on adapy's own numbers for a UNP200x10 over a 4 m member. The midline area
    # Abaqus integrates -- 2(w - t_w/2) t_f + (h - t_f) t_w -- is algebraically the same as
    # calc_channel's 2 w t_f + (h - 2 t_f) t_w, so this is an equality and not a tolerance.
    section = ada.Section("UNP200", from_str="UNP200x10")
    expected_mass = 7850.0 * section.properties.Ax * 4.0
    mass = run.value("PROBE mass")
    assert mass not in (None, "None"), (
        "CAE could not weigh the member, which is what a generalized section does: " + run.describe()
    )
    assert float(mass) == pytest.approx(expected_mass, rel=1e-6), (
        "CAE weighs the channel at {0} kg against rho * Ax * L = {1} kg from adapy's own area, so the "
        "profile Abaqus integrated is not the cross-section adapy meant".format(mass, expected_mass)
    )

    # Not `job.status`: measured, it reads `None` in a noGUI session even for a datacheck that
    # finished cleanly, so it is printed for diagnosis and asserted on nowhere. Abaqus' own verdict
    # is the last line of the job log, and its reasons are in the .dat.
    log = run.workdir / "channel_dc.log"
    dat = run.workdir / "channel_dc.dat"
    assert log.is_file() and dat.is_file(), "Abaqus wrote no job files, so nothing was checked:\n" + run.describe()
    text = dat.read_text(encoding="utf-8", errors="replace")
    verdict = log.read_text(encoding="utf-8", errors="replace")
    errors = [line.strip() for line in text.splitlines() if "***ERROR" in line]

    assert errors == [], "abaqus datacheck rejected the deck CAE exported for a channel:\n" + "\n".join(errors)
    assert "ANALYSIS DATACHECK COMPLETE" in text, "the datacheck did not run to completion:\n" + text[-2000:]
    assert "COMPLETED" in verdict, "Abaqus did not report the job as COMPLETED:\n" + verdict


# --------------------------------------- an offset on a traced channel, read back from the kernel

OFFSET_DRIVER = """
_m = mdb.models['Model-1']
for _k in sorted(_m.sections.keys()):
    _s = _m.sections[_k]
    print('PROBE section {0} {1} {2}'.format(_k, _m.profiles[_s.profile].__class__.__name__, _s.integration))
    for _attr in ('beamSectionOffset', 'centroid', 'shearCenter'):
        try:
            print('PROBE attr {0} {1} {2}'.format(_k, _attr, tuple(getattr(_s, _attr))))
        except Exception:
            print('PROBE attr {0} {1} ABSENT'.format(_k, _attr))
"""


@pytest.fixture(scope="session")
def offset_run(tmp_path_factory):
    """adapy's own GeniE corpus, every constant offset in it, built for real."""
    path = (
        pathlib.Path(__file__).resolve().parents[4] / "files/fem_files/sesam/varying_offset/beams_constant_offset.xml"
    )
    assembly = ada.from_genie_xml(path)

    workdir = tmp_path_factory.mktemp("cae_offsets")
    script = emit(assembly, workdir, OFFSET_DRIVER, name="offsets")
    return assembly, run_cae_script(script, workdir)


def test_a_channels_offset_survives_as_the_keyword_its_section_kind_accepts(offset_run):
    """The consequence of a channel becoming an ``ArbitraryProfile``, checked in the kernel.

    A channel used to be a generalized section here, and a generalized section takes ``centroid``
    because it *refuses* ``beamSectionOffset`` -- ``TypeError: keyword error on beamSectionOffset``,
    from the constructor and from ``setValues`` alike. An ``ArbitraryProfile`` section takes
    ``beamSectionOffset``, so moving the channel moved which name its offset has to be written under,
    and nothing but the kernel can confirm the value arrived.

    The two kinds are not symmetric, and this is where that was established rather than assumed. On a
    ``DURING_ANALYSIS`` section ``centroid`` is the *same stored member* as ``beamSectionOffset``:
    probed one section per spelling with one exported INP each, setting either produced
    ``*Beam Section Offset`` and read back out of ``beamSectionOffset``. What is silently ignored on
    that kind is ``shearCenter`` -- accepted, and written nowhere -- so this asserts it stayed zero.

    The emitted script's guard 8 reads the same attribute back and fails the build on a mismatch, so a
    clean run is itself part of the evidence; these assertions say which value it found.
    """
    assembly, run = offset_run
    _assert_ran(run)

    kinds = dict((line.split(" ")[0], line.split(" ")[1]) for line in run.values("PROBE section"))
    assert kinds["sec_UNP180_S355_off_0_0p09"] == "ArbitraryProfile"

    attrs = {}
    for line in run.values("PROBE attr"):
        name, attr, value = line.split(" ", 2)
        attrs[(name, attr)] = value
    channel = "sec_UNP180_S355_off_0_0p09"
    assert (
        attrs[(channel, "beamSectionOffset")] == "(0.0, 0.09)"
    ), "CAE holds the channel's offset as {0}; the writer asked for (0.0, 0.09)".format(
        attrs[(channel, "beamSectionOffset")]
    )
    assert attrs[(channel, "centroid")] == "(0.0, 0.09)", (
        "on a DURING_ANALYSIS section 'centroid' is the same member under another name; were this "
        "zero, the two would be separate and the choice of name would carry the offset"
    )
    assert (
        attrs[(channel, "shearCenter")] == "(0.0, 0.0)"
    ), "'shearCenter' is the argument this section kind accepts and ignores, so nothing may land in it"

    # And the same value on a shaped section that was never a channel, so the assertion above is
    # about the keyword and not about this one profile.
    assert attrs[("sec_HEA300_S355_off_0_0p145", "beamSectionOffset")] == "(0.0, 0.145)"

    built = json.loads((run.workdir / "offsets.cae_build_result.json").read_text(encoding="utf-8"))
    assert built["ok"] is True, built


# ------------------------------------------------------- the analysis: supports, loads, a solve

#: The frame the cross-solver comparison against Sestra is derived from. Rebuilt here rather than
#: imported from ``verification/`` so the acceptance suite keeps no dependency on that package --
#: and kept numerically identical to it, because the numbers asserted below are the ones that
#: comparison turns on.
PORTAL_HEIGHT = 6.0
PORTAL_SPAN = 8.0
PORTAL_SEED = 1.0
PORTAL_LOAD = 10.0e3
PORTAL_SECTION = "OD200x10"

#: How far the solved sway may sit from the Euler-Bernoulli closed form. 1% -- the same budget the
#: cross-solver comparison uses, and for the same reason: the real gap is 0.31%, of which 0.28% is
#: Abaqus' thin-walled PIPE second moment of area (see
#: `test_the_abaqus_pipe_sections_second_moment_is_the_thin_walled_one`) and the rest the axial
#: flexibility the closed form neglects -- while every translation defect this could have is an
#: order of magnitude bigger: a pinned base instead of a fixed one is 4.3x, a halved load is 2x.
PORTAL_SWAY_REL_TOL = 1e-02


def portal_sway_euler_bernoulli(*, p_total, height, span, e_mod, inertia) -> float:
    """Slope-deflection sway of a fixed-base portal frame, one section throughout.

    ``delta = P h^3 / (24 E I) * (4 + 6 beta) / (1 + 6 beta)`` with ``beta = (I/L) / (I/h) = h/L``
    for a uniform frame. Worth checking at its limits: ``beta -> inf`` (rigid girder) gives
    ``P h^3 / (24 E I)``, two fixed-fixed columns in parallel; ``beta -> 0`` gives
    ``P h^3 / (6 E I)``, two cantilevers each carrying ``P/2``. Both are right.

    An independent third opinion rather than a second solver: two writers can agree and both be
    wrong, and a section modulus out by the same factor in both agrees beautifully.
    """
    beta = height / span
    return (p_total * height**3) / (24.0 * e_mod * inertia) * (4.0 + 6.0 * beta) / (1.0 + 6.0 * beta)


def nset_at(fem, name, point):
    nodes = fem.nodes.get_by_volume(p=point, tol=1e-06)
    assert len(nodes) == 1, "expected one node at {}, found {}".format(point, len(nodes))
    from ada.fem import FemSet

    return fem.add_set(FemSet(name, list(nodes), FemSet.TYPES.NSET, parent=fem))


def portal_frame() -> ada.Assembly:
    from ada.fem import Bc, Load, StepImplicitStatic

    mat = ada.Material("S355", CarbonSteel("S355"))
    part = ada.Part("PortalFrame")
    part / (
        ada.Beam("COL_L", (0, 0, 0), (0, 0, PORTAL_HEIGHT), PORTAL_SECTION, mat),
        ada.Beam("COL_R", (PORTAL_SPAN, 0, 0), (PORTAL_SPAN, 0, PORTAL_HEIGHT), PORTAL_SECTION, mat),
        ada.Beam("GIRDER", (0, 0, PORTAL_HEIGHT), (PORTAL_SPAN, 0, PORTAL_HEIGHT), PORTAL_SECTION, mat),
    )
    part.fem = part.to_fem_obj(PORTAL_SEED, "line")

    part.fem.add_bc(Bc("FIX_L", nset_at(part.fem, "BASE_L", (0.0, 0.0, 0.0)), [1, 2, 3, 4, 5, 6]))
    part.fem.add_bc(Bc("FIX_R", nset_at(part.fem, "BASE_R", (PORTAL_SPAN, 0.0, 0.0)), [1, 2, 3, 4, 5, 6]))
    top_l = nset_at(part.fem, "TOP_L", (0.0, 0.0, PORTAL_HEIGHT))
    top_r = nset_at(part.fem, "TOP_R", (PORTAL_SPAN, 0.0, PORTAL_HEIGHT))

    assembly = ada.Assembly("PortalSite") / part
    step = assembly.fem.add_step(StepImplicitStatic("static", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    # One Load per node rather than one over a two-node set, matching the comparison harness --
    # which does it that way because adapy's Sesam writer silently loads only members[0].
    step.add_load(Load("PX_L", Load.TYPES.FORCE, PORTAL_LOAD / 2, fem_set=top_l, dof=[1, 0, 0, 0, 0, 0]))
    step.add_load(Load("PX_R", Load.TYPES.FORCE, PORTAL_LOAD / 2, fem_set=top_r, dof=[1, 0, 0, 0, 0, 0]))
    return assembly


@pytest.fixture(scope="session")
def portal_run(tmp_path_factory):
    """The portal frame: meshed, solved, and its displacements written out, in one CAE run.

    ``B33`` because the closed form this run is checked against is Euler-Bernoulli, and ``B33`` is
    Abaqus' cubic Euler-Bernoulli beam: the pairing is deliberate, so the residual is the section
    and the axial term and not a beam theory. The cross-solver comparison against Sestra uses
    ``B32`` instead, because Sestra's ``BEAS`` is shear-flexible -- see
    ``verification/genie_vs_abaqus/abaqus_runner.py`` for the measured table of what each of
    ``B31``, ``B32`` and ``B33`` answers on this exact frame.
    """
    assembly = portal_frame()
    workdir = tmp_path_factory.mktemp("cae_portal")
    script = workdir / "portal.py"
    assembly.to_abaqus_cae_script(script, mesh_size=PORTAL_SEED, element_type="B33", submit=True)
    return assembly, run_cae_script(script, workdir)


def sidecars(run, stem: str) -> tuple[dict, dict]:
    build = json.loads((run.workdir / "{}.cae_build_result.json".format(stem)).read_text(encoding="utf-8"))
    moved = json.loads((run.workdir / "{}.cae_displacements.json".format(stem)).read_text(encoding="utf-8"))
    return build, moved


def nodal(displacements: dict, step: str = None) -> dict:
    """``{rounded position: six components}`` for the one instance, at the last frame of ``step``."""
    (instance,) = displacements["instances"]
    coords = {row[0]: tuple(round(c, 9) for c in row[1:4]) for row in instance["nodes"]}
    steps = {entry["name"]: entry for entry in instance["steps"]}
    entry = steps[step] if step is not None else instance["steps"][-1]
    return {coords[row[0]]: row[1:] for row in entry["displacements"]}


def test_the_portal_frames_solved_sway_is_the_closed_form_sway(portal_run):
    """The headline: one adapy concept model, carried through the writer, solved, and *right*.

    The closed form is computed from adapy's own section properties, so what this measures is the
    whole chain -- geometry, section, material, joint continuity, both fixed bases, both loads --
    against arithmetic that never went near Abaqus. A support translated as pinned instead of fixed
    is 4.3x this number and a halved load is 2x, so it is not a subtle check.
    """
    assembly, run = portal_run
    _assert_ran(run)
    build, displacements = sidecars(run, "portal")
    assert build["ok"] is True, build

    beam = assembly.get_by_name("COL_L")
    expected = portal_sway_euler_bernoulli(
        p_total=PORTAL_LOAD,
        height=PORTAL_HEIGHT,
        span=PORTAL_SPAN,
        e_mod=float(beam.material.model.E),
        inertia=float(beam.section.properties.Iy),
    )
    values = nodal(displacements)
    left = values[(0.0, 0.0, PORTAL_HEIGHT)]
    right = values[(PORTAL_SPAN, 0.0, PORTAL_HEIGHT)]

    assert left[0] == pytest.approx(
        expected, rel=PORTAL_SWAY_REL_TOL
    ), "top-corner sway {:.6e} m against the Euler-Bernoulli closed form {:.6e} m".format(left[0], expected)
    assert right[0] == pytest.approx(left[0], rel=1e-09), "the load is symmetric, so both corners sway alike"
    assert values[(0.0, 0.0, 0.0)][0] == pytest.approx(0.0, abs=1e-12), "a fixed base does not move"
    assert values[(0.0, 0.0, 0.0)][4] == pytest.approx(0.0, abs=1e-12), "nor rotate -- ENCASTRE, not PINNED"
    # The girder's quarter points are where its antisymmetric S-curve peaks, which makes them the
    # most sensitive thing in the model to joint-rotation continuity.
    assert values[(PORTAL_SPAN / 4, 0.0, PORTAL_HEIGHT)][2] == pytest.approx(
        -values[(3 * PORTAL_SPAN / 4, 0.0, PORTAL_HEIGHT)][2], rel=1e-06
    )


def test_the_solver_reports_the_load_adapy_described(portal_run):
    """The kernel-side guard: what arrived, in newtons, against adapy's own sum of its Loads.

    The comparison harness ranked "the full 10 kN must arrive" third among the things that break a
    cross-solver check, and said to read it out of the ``.dat`` by hand. This is the same check,
    made by the emitted script, failing the build rather than reporting a wrong answer.
    """
    _, run = portal_run
    _assert_ran(run)
    build, _ = sidecars(run, "portal")

    equilibrium = build["equilibrium"]
    assert equilibrium["applied_force_from_adapy"] == [PORTAL_LOAD, 0.0, 0.0]
    assert equilibrium["concentrated_force_sum"] == pytest.approx([PORTAL_LOAD, 0.0, 0.0], abs=1e-06)
    assert equilibrium["reaction_force_sum"] == pytest.approx([-PORTAL_LOAD, 0.0, 0.0], abs=1e-06)
    assert build["analysis"]["boundary_conditions"] == ["FIX_L", "FIX_R"]
    assert build["analysis"]["loads"] == ["PX_L_F", "PX_R_F"]
    assert sorted(build["analysis"]["regions"]) == ["BASE_L", "BASE_R", "TOP_L", "TOP_R"]


def test_the_seed_puts_a_node_at_every_multiple_of_the_element_size(portal_run):
    """Why a cross-solver comparison may ask for a displacement at a *position* at all.

    ``seedPart(size=1.0)`` on a 6 m column has to give 6 elements with nodes at 0, 1 ... 6, not 7
    elements at 0.857 m. Two solvers that meshed independently can only be compared at points both
    of them seeded, and this is the half of that which is Abaqus'.
    """
    _, run = portal_run
    _assert_ran(run)
    build, displacements = sidecars(run, "portal")

    mesh = build["mesh"]["PortalFrame"]
    assert mesh["elements_per_member"] == {"COL_L": 6, "COL_R": 6, "GIRDER": 8}
    assert mesh["element_type"] == "B33"
    assert mesh["nodes"] == 21, "20 elements over 3 members joined at 2 corners"

    positions = set(nodal(displacements))
    for probe in (
        (0.0, 0.0, PORTAL_HEIGHT / 2),
        (PORTAL_SPAN, 0.0, PORTAL_HEIGHT / 2),
        (PORTAL_SPAN / 4, 0.0, PORTAL_HEIGHT),
        (PORTAL_SPAN / 2, 0.0, PORTAL_HEIGHT),
        (3 * PORTAL_SPAN / 4, 0.0, PORTAL_HEIGHT),
    ):
        assert probe in positions, "the mesh has no node at {}, so no comparison can sample it".format(probe)


def test_the_displacement_sidecar_joins_the_two_fields_abaqus_splits_them_across(portal_run):
    """``U`` is (U1, U2, U3) and ``UR`` is (UR1, UR2, UR3) -- measured, and the trap this removes.

    A reader expecting six components in one field gets three, and then reports the rotations as
    absent or as zero. The loaded corner's own rotation is 2.9e-03 rad here, so "zero" would be a
    silent, plausible, wrong answer.
    """
    _, run = portal_run
    _assert_ran(run)
    _, displacements = sidecars(run, "portal")

    assert displacements["components"] == ["U1", "U2", "U3", "UR1", "UR2", "UR3"]
    assert displacements["solver_version"].startswith("Abaqus/Standard")
    assert displacements["element_type"] == "B33"
    values = nodal(displacements)
    assert len(next(iter(values.values()))) == 6
    top = values[(0.0, 0.0, PORTAL_HEIGHT)]
    assert abs(top[4]) > 1e-04, "the loaded corner rotates; a sidecar carrying only U would say it did not"
    assert top[1] == pytest.approx(0.0, abs=1e-12), "the frame is planar, so nothing moves out of plane"


def test_a_load_that_arrives_halved_fails_the_build(portal_run, tmp_path):
    """The equilibrium guard's teeth, in the kernel, on the writer's own script.

    One of the two ``ConcentratedForce`` calls is zeroed rather than deleted, and that is the point:
    a *deleted* load is caught earlier and more cheaply by the guard that reads the model's own
    ``loads`` repository back, so deleting one would have exercised the wrong guard. Halving it
    leaves an object of the right name in the right place carrying the wrong number -- which is
    what a translation defect actually looks like -- and only the solver's own reaction total can
    see it. Without this the equilibrium guard is a claim.

    It is halved rather than zeroed for a measured reason, which is also the kernel corroborating
    one of the writer's own refusals: CAE will not create a load of nothing at all --
    ``AbaqusException: Load must be created with a non-zero magnitude unless utilizing a user
    subroutine.`` -- which is exactly what ``ada.cadit.cae.analysis`` refuses a zero ``Load`` for
    on the adapy side, and earlier.
    """
    _, good = portal_run
    _assert_ran(good)
    source = good.script.read_text(encoding="utf-8")
    anchor = "region=assembly.sets['TOP_R'], cf1=5000.0"
    assert source.count(anchor) == 1, "anchor matched {} times, not one".format(source.count(anchor))
    broken = tmp_path / "halved.py"
    broken.write_text(source.replace(anchor, "region=assembly.sets['TOP_R'], cf1=2500.0"), encoding="utf-8")

    run = run_cae_script(broken, tmp_path)

    if run.licence_denied:
        pytest.skip("no CAE licence available")
    assert run.failed, "a deck carrying half its load was reported as a clean run: " + run.describe()
    assert "build-result sidecar" in run.failure_signals, run.describe()
    result = run.build_results[0]
    assert result["ok"] is False
    assert any("not the load adapy described" in error for error in result["errors"]), result["errors"]
    arrived = result["equilibrium"]["concentrated_force_sum"]
    assert arrived == pytest.approx([0.75 * PORTAL_LOAD, 0.0, 0.0], abs=1e-06), arrived


# ------------------------------------------------- the PIPE section, and a prescribed support

CANTILEVER_LENGTH = 4.0
END_MOMENT = 1000.0
SETTLEMENT = -0.001
FOLLOWER_FORCE = 1000.0


@pytest.fixture(scope="session")
def analysis_detail_run(tmp_path_factory):
    """Three independent cantilevers in one part, so three questions cost one CAE token.

    * ``moment_arm`` carries a **pure end moment**, which is the only loading that measures a
      section's second moment of area cleanly: a tip *force* on an offset or open section turns
      into a torque through the shear centre, and shear flexibility enters a force-loaded tip.
      With ``B33`` there is no shear either way, so ``theta = M L / (E I)`` is exact.
    * ``settling`` carries a **prescribed** support displacement, which adapy's Sesam writer
      cannot express at all.
    * ``followed`` carries a **follower** force, so ``follower=ON`` is known to be accepted by the
      kernel rather than believed to be.
    """
    from ada.fem import Bc, Load, StepImplicitStatic

    mat = ada.Material("S355", CarbonSteel("S355"))
    part = ada.Part("Cantilevers")
    part / (
        ada.Beam("moment_arm", (0, 0, 0), (CANTILEVER_LENGTH, 0, 0), PORTAL_SECTION, mat),
        ada.Beam("settling", (0, 3, 0), (CANTILEVER_LENGTH, 3, 0), PORTAL_SECTION, mat),
        ada.Beam("followed", (0, 6, 0), (CANTILEVER_LENGTH, 6, 0), PORTAL_SECTION, mat),
    )
    part.fem = part.to_fem_obj(0.5, "line")

    for index, y in enumerate((0.0, 3.0, 6.0)):
        root = nset_at(part.fem, "ROOT_{}".format(index), (0.0, y, 0.0))
        part.fem.add_bc(Bc("root_{}".format(index), root, [1, 2, 3, 4, 5, 6]))
    moment_tip = nset_at(part.fem, "MOMENT_TIP", (CANTILEVER_LENGTH, 0.0, 0.0))
    settling_tip = nset_at(part.fem, "SETTLING_TIP", (CANTILEVER_LENGTH, 3.0, 0.0))
    followed_tip = nset_at(part.fem, "FOLLOWED_TIP", (CANTILEVER_LENGTH, 6.0, 0.0))
    part.fem.add_bc(Bc("settle", settling_tip, [3], magnitudes=[SETTLEMENT]))

    assembly = ada.Assembly("Detail") / part
    step = assembly.fem.add_step(StepImplicitStatic("lc1", nl_geom=False, total_time=1, init_incr=1, max_incr=1))
    step.add_load(Load("M", Load.TYPES.FORCE, -END_MOMENT, fem_set=moment_tip, dof=[0, 0, 0, 0, 1, 0]))
    step.add_load(
        Load(
            "P",
            Load.TYPES.FORCE,
            FOLLOWER_FORCE,
            fem_set=followed_tip,
            dof=[0, 0, 1, 0, 0, 0],
            follower_force=True,
        )
    )

    workdir = tmp_path_factory.mktemp("cae_detail")
    script = workdir / "detail.py"
    assembly.to_abaqus_cae_script(script, mesh_size=0.5, element_type="B33", submit=True)
    return assembly, run_cae_script(script, workdir)


def test_the_abaqus_pipe_sections_second_moment_is_the_thin_walled_one(analysis_detail_run):
    """Why the cross-solver comparison lands where it does, measured rather than argued.

    Abaqus' ``section=PIPE`` integrates the wall as a **line** of the given thickness, so its
    second moment of area is the thin-walled ``pi rm^3 t`` and not the exact annulus
    ``pi/4 (ro^4 - ri^4)`` that adapy's ``Section.properties.Iy`` is. For an ``OD200x10`` that is
    0.276% low, which is the whole of the systematic gap between Abaqus and both the closed form
    and Sestra on the portal frame -- see ``verification/genie_vs_abaqus/abaqus_runner.py``.

    The two *areas* are identical, because ``2 pi rm t`` and ``pi (ro^2 - ri^2)`` are the same
    number algebraically, so only bending is affected. That is why weighing the part would have
    found nothing, and why a rotation under a pure moment finds it exactly.

    If a future Abaqus integrates a pipe differently, this is the test that says so -- and the
    number the comparison's residual is explained by would then need re-deriving, rather than a
    tolerance widening.
    """
    assembly, run = analysis_detail_run
    _assert_ran(run)
    _, displacements = sidecars(run, "detail")

    beam = assembly.get_by_name("moment_arm")
    e_mod = float(beam.material.model.E)
    adapy_inertia = float(beam.section.properties.Iy)
    radius, thickness = float(beam.section.r), float(beam.section.wt)
    thin_walled = math.pi * (radius - thickness / 2.0) ** 3 * thickness

    rotation = abs(nodal(displacements)[(CANTILEVER_LENGTH, 0.0, 0.0)][4])
    measured = END_MOMENT * CANTILEVER_LENGTH / (e_mod * rotation)

    assert measured == pytest.approx(
        thin_walled, rel=1e-03
    ), "Abaqus' effective I is {:.6e}; thin-walled pi rm^3 t is {:.6e} and adapy's annulus {:.6e}".format(
        measured, thin_walled, adapy_inertia
    )
    assert measured / adapy_inertia == pytest.approx(
        0.99724, rel=1e-04
    ), "the systematic bias the comparison's residual is explained by has moved: Abaqus' I is now {:.6f} of adapy's".format(
        measured / adapy_inertia
    )
    assert float(beam.section.properties.Ax) == pytest.approx(
        2.0 * math.pi * (radius - thickness / 2.0) * thickness, rel=1e-09
    ), "the areas are algebraically identical, which is why only bending differs"


def test_a_prescribed_support_displacement_reaches_the_solver(analysis_detail_run):
    """The Sesam gap, closed. ``write_bcs`` there never writes a ``Bc`` magnitude at all.

    Its own comment says so -- ``PRESCRIBED = 2`` is defined, and "ada's Bc magnitudes are not
    carried into BNDISPL yet" -- so a settlement case silently becomes a fixed support on that
    route. Here the node ends up exactly where the model said it would.
    """
    _, run = analysis_detail_run
    _assert_ran(run)
    _, displacements = sidecars(run, "detail")

    tip = nodal(displacements)[(CANTILEVER_LENGTH, 3.0, 0.0)]

    # 1e-06 and not tighter, because an ODB stores field data in **single** precision: this
    # -0.001 comes back as -0.0010000000474974513, which is float32's nearest neighbour to it and
    # not a solver residual. Measured, and worth knowing before reading any number out of a
    # sidecar to nine digits.
    assert tip[2] == pytest.approx(SETTLEMENT, rel=1e-06), "the prescribed displacement arrived"
    assert tip[2] != 0.0, "a Bc magnitude dropped in translation reads as a fixed support, which is zero here"
    assert tip[0] == pytest.approx(0.0, abs=1e-09), "and only in the DOF the record named"


def test_a_follower_force_is_accepted_by_the_kernel(analysis_detail_run):
    """``follower=ON`` is the argument adapy's INP writer already emits, so this route emits it too.

    Its effect is nil in a geometrically linear step, which is exactly why it is checked here: what
    is being established is that the kernel takes the keyword, so a ``LoadPoint`` -- whose
    ``follower_force`` defaults to True -- is not quietly written as a fixed-direction load.
    """
    _, run = analysis_detail_run
    _assert_ran(run)
    build, displacements = sidecars(run, "detail")

    assert "follower=ON" in run.script.read_text(encoding="utf-8")
    assert build["equilibrium"]["applied_force_from_adapy"] == [0.0, 0.0, FOLLOWER_FORCE]
    assert build["equilibrium"]["concentrated_force_sum"] == pytest.approx([0.0, 0.0, FOLLOWER_FORCE], abs=1e-04)

    tip = nodal(displacements)[(CANTILEVER_LENGTH, 6.0, 0.0)]
    assert tip[2] > 0.0, "a +Z tip force deflects the tip in +Z"
