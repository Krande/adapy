"""One orientation convention, shared by both Abaqus writers.

The single highest-value test in this workstream. ``n1`` is ``beam.yvec`` -- ``up x t`` -- and the INP
writer has emitted exactly that as the second data line of ``*Beam Section`` for as long as it has
existed (`write_sections.py`: ``n1 = ", ".join(str(x) for x in fem_sec.local_y)``). The drift that will
actually happen to this codebase is a *third* convention appearing when a second Abaqus writer is
added, so the two are pinned to each other here rather than each to a remembered rule.

Three layers, in increasing cost:

1. the INP writer's ``n1`` is ``beam.yvec``, over vertical / horizontal / skew / explicit-``up``
   members. Licence-free, writer-free, and it runs today: it is the number the CAE writer must match.
2. the CAE writer's ``n1`` equals that same number, member for member. Licence-free.
3. the comparison itself rejects a reversed ``n1`` -- see `test_the_comparison_rejects_a_sign_flip`.
   A reversed ``n1`` is the one orientation defect no static check and no deflection measurement can
   find on its own (every second moment of area is invariant under a 180 degree rotation), so this
   equality is the only thing standing between the two writers and a silent sign disagreement.
"""

from __future__ import annotations

import pathlib
import re

import numpy as np
import pytest

import ada

from .cae_script_graph import orientations_by_set_name
from .conftest import writer_available

#: `*Beam Section` with a cross-section keyword, its dimension line, and then its n1 line.
_BEAM_SECTION_BLOCK = re.compile(
    r"^\*Beam Section,\s*elset=(?P<elset>[^,\n]+),[^\n]*\n(?P<dims>[^\n*]*)\n(?P<n1>[^\n*]*)$",
    re.IGNORECASE | re.MULTILINE,
)

ORIENTATION_CASES = {
    "vertical": dict(p1=(0.0, 0.0, 0.0), p2=(0.0, 0.0, 4.0), up=None),
    "horizontal": dict(p1=(0.0, 0.0, 0.0), p2=(6.0, 0.0, 0.0), up=None),
    "skew": dict(p1=(0.0, 0.0, 0.0), p2=(3.0, 2.0, 4.0), up=None),
    "explicit_up": dict(p1=(0.0, 0.0, 0.0), p2=(6.0, 0.0, 0.0), up=(0.0, 1.0, 0.0)),
}


def make_beam(case: str) -> ada.Beam:
    spec = ORIENTATION_CASES[case]
    return ada.Beam("member", spec["p1"], spec["p2"], "IPE300", "S355", up=spec["up"])


def n1_from_inp(beam: ada.Beam, scratch: pathlib.Path) -> dict[str, tuple[float, float, float]]:
    """``{elset: n1}`` straight out of the INP text the Abaqus writer produced."""
    part = ada.Part("Frame") / beam
    assembly = ada.Assembly("Model") / part
    part.fem = part.to_fem_obj(10.0, "line", experimental_bm_splitting=False)
    assembly.to_fem("inp_n1", "abaqus", scratch_dir=scratch, overwrite=True)

    inps = sorted(scratch.rglob("*.inp"))
    assert inps, "the Abaqus writer produced no .inp under {}".format(scratch)
    text = "\n".join(p.read_text(encoding="utf-8") for p in inps)

    found = {}
    for match in _BEAM_SECTION_BLOCK.finditer(text):
        values = [float(v) for v in match.group("n1").split(",")]
        assert len(values) == 3, "expected three direction cosines, got {!r}".format(match.group("n1"))
        found[match.group("elset").strip()] = tuple(values)
    assert found, "no *Beam Section block with an n1 line was found in the emitted INP"
    return found


def unit(vector) -> np.ndarray:
    array = np.asarray(vector, dtype=float)
    return array / np.linalg.norm(array)


def assert_n1_matches(actual, expected, what: str) -> None:
    """Equality of *direction*, including sign -- the whole point of the comparison.

    Magnitude is deliberately not compared: the INP writer emits ``fem_sec.local_y``, which is
    ``cross(local_z, xvec)`` with ``local_z`` the *reference* up rather than the orthogonalised one, so
    for a skew member its length is ``sin(angle)`` -- measured 0.6695 for the skew case here. Abaqus
    projects and normalises ``n1`` itself, so the deck is right either way, but it does mean the two
    writers' literals differ in length while describing the same direction. See
    `test_the_inp_n1_is_a_direction_of_arbitrary_length`.
    """
    actual = unit(actual)
    expected = unit(expected)
    if np.allclose(actual, -expected, atol=1e-9) and not np.allclose(actual, expected, atol=1e-9):
        raise AssertionError(
            "{}: n1 is reversed -- {} against {}. A 180 degree section rotation is invisible to "
            "every deflection measurement, so this equality is the only thing that catches it.".format(
                what, actual.tolist(), expected.tolist()
            )
        )
    assert actual == pytest.approx(expected, abs=1e-9), "{}: n1 {} != {}".format(
        what, actual.tolist(), expected.tolist()
    )


@pytest.mark.parametrize("case", sorted(ORIENTATION_CASES))
def test_the_inp_writer_emits_beam_yvec_as_n1(case, tmp_path):
    """Layer 1. This is the number the CAE writer has to match, measured from the existing writer."""
    beam = make_beam(case)

    emitted = n1_from_inp(beam, tmp_path)

    assert len(emitted) == 1, "one member, one *Beam Section: got {}".format(sorted(emitted))
    ((elset, n1),) = emitted.items()
    assert_n1_matches(n1, beam.yvec, "INP elset {}".format(elset))


def test_the_inp_n1_is_a_direction_of_arbitrary_length(tmp_path):
    """A measured wart, pinned so the CAE writer is not "corrected" to copy it.

    ``fem_sec.local_y`` is ``cross(local_z, xvec)`` where ``local_z`` is the reference up, not an
    orthogonalised one. For a skew member the two are 42 degrees apart, so the emitted ``n1`` comes out
    0.6695 long. Abaqus normalises it, so the INP is correct -- but the CAE writer emits a unit vector
    (`cae_script_graph.check_orientation_vectors` requires it), and this test records that the
    difference between the two is length only, never direction.
    """
    beam = make_beam("skew")

    (n1,) = n1_from_inp(beam, tmp_path).values()

    length = float(np.linalg.norm(n1))
    assert length != pytest.approx(1.0, abs=1e-6), "if the INP writer now normalises n1, drop this test"
    assert unit(n1) == pytest.approx(unit(beam.yvec), abs=1e-9)


@pytest.mark.parametrize("case", sorted(ORIENTATION_CASES))
def test_the_four_cases_really_are_four_different_orientations(case):
    """A fixture that accidentally produced the same n1 four times would hide a swapped axis."""
    vectors = [tuple(np.round(make_beam(c).yvec, 12)) for c in sorted(ORIENTATION_CASES)]

    assert len(set(vectors)) == len(vectors), "the orientation cases collapse to {}".format(set(vectors))


def test_the_comparison_rejects_a_sign_flip():
    """The mutation that matters: `n1 = -beam.yvec`. Nothing else in this suite can see it."""
    beam = make_beam("skew")

    with pytest.raises(AssertionError, match="reversed"):
        assert_n1_matches(-np.asarray(beam.yvec, dtype=float), beam.yvec, "mutated writer")


def test_the_comparison_rejects_a_swapped_axis():
    beam = make_beam("horizontal")

    with pytest.raises(AssertionError):
        assert_n1_matches(beam.up, beam.yvec, "mutated writer")


@pytest.mark.skipif(not writer_available(), reason="Part.to_abaqus_cae_script is not implemented yet")
@pytest.mark.parametrize("case", sorted(ORIENTATION_CASES))
def test_the_cae_writer_emits_the_same_n1_as_the_inp_writer(case, tmp_path):
    """Layer 2. Both writers derive n1 from `beam.yvec`; this is what keeps them from drifting apart."""
    beam = make_beam(case)
    expected = n1_from_inp(make_beam(case), tmp_path / "inp")

    part = ada.Part("Frame") / beam
    ada.Assembly("Model") / part
    script = tmp_path / "frame.py"
    part.to_abaqus_cae_script(script)

    cae_n1 = orientations_by_set_name(script.read_text(encoding="utf-8"))
    assert len(cae_n1) == 1, "one member, one orientation: got {}".format(sorted(cae_n1))
    ((region, n1),) = cae_n1.items()
    ((elset, inp_n1),) = expected.items()
    assert_n1_matches(n1, inp_n1, "CAE region {} against INP elset {}".format(region, elset))
    assert_n1_matches(n1, beam.yvec, "CAE region {} against beam.yvec".format(region))
