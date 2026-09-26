"""What the writer emits for one fixed model: the text, and the graph inside the text.

The bar this is written to exceed is `tests/core/cadit/e3d/test_aveva_e3d_mac.py`, the only test of
this repo's other script-emitting writer: it builds a model, calls the writer, and prints. So:

* the golden file is drift detection, and is meant to be reviewed as prose -- read the diff, do not
  regenerate it because it changed;
* the graph pass is the part that has an opinion about whether the script is *right*. Its teeth are
  demonstrated in `test_cae_graph_checks_have_teeth.py` against a kernel-validated specimen, and
  re-demonstrated here against this writer's own output, so a green pass is evidence and not habit.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

import ada
from ada.sections.categories import BaseTypes

from .cae_script_graph import CaeGraphError, ScriptGraph, check_emitted_script
from .conftest import FILES, require_writer

require_writer()

GOLDEN = FILES / "golden_frame_cae.txt"
REGEN_ENV = "ADA_REGEN_CAE_GOLDEN"

#: The banner carries the adapy version, which changes on every release and means nothing here.
_VERSION_LINE = re.compile(r"^(# Abaqus/CAE concept model, written by adapy ).*$", re.MULTILINE)


def normalise(source: str) -> str:
    return _VERSION_LINE.sub(r"\1<version>.", source).replace("\r\n", "\n")


def emit(part: ada.Part, tmp_path: pathlib.Path, name: str = "frame", **kwargs) -> tuple[pathlib.Path, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    destination = tmp_path / (name + ".py")
    written = part.to_abaqus_cae_script(destination, **kwargs)
    assert destination in written, "the writer did not report the script it wrote: {}".format(written)
    return destination, destination.read_text(encoding="utf-8")


def test_the_emitted_script_is_valid_python(frame_model, tmp_path):
    destination, source = emit(frame_model.get_part("Frame"), tmp_path)

    compile(source, str(destination), "exec")


def test_the_emitted_script_passes_the_graph_pass(frame_model, tmp_path):
    """Every section assigned, every reference defined, one instance per part, n1 unit and normal."""
    _, source = emit(frame_model.get_part("Frame"), tmp_path)

    graph = check_emitted_script(source, name="frame.py")

    assert sorted(graph.created_names("Part")) == ["Frame"]
    assert len(graph.by_method("WirePolyLine")) == 3
    assert len(graph.by_method("SectionAssignment")) == 3
    assert len(graph.by_method("assignBeamSectionOrientation")) == 3
    assert sorted(graph.created_names("Set")) == ["brace", "col1", "girder"]


def test_the_graph_pass_still_has_teeth_on_this_writers_output(frame_model, tmp_path):
    """The specimen proves the checker works; this proves it works on *this* writer's shape."""
    _, source = emit(frame_model.get_part("Frame"), tmp_path)
    anchor = "part_0.SectionAssignment(region=region_0_1, sectionName="
    matches = [line for line in source.splitlines() if anchor in line]
    assert len(matches) == 1, "anchor matched {} lines, not one".format(len(matches))
    mutated = source.replace(matches[0] + "\n", "")
    assert mutated != source

    with pytest.raises(CaeGraphError):
        check_emitted_script(mutated, name="mutated frame.py")


def test_two_collinear_members_get_their_own_cylinders(tmp_path):
    """Stacked columns share an axis, so each cylinder reaches into the other member's span.

    The writer has to give them separate cylinders, and the graph pass has to tell the two members apart
    rather than matching the first collinear segment it meets.
    `test_cae_graph_checks_have_teeth.py::test_a_valid_script_is_accepted` carries the region ordering
    that used to make the latter reject a valid script.
    """
    part = ada.Part("Stack")
    part / (
        ada.Beam("col_lower", (0, 0, 0), (0, 0, 4), "IPE300", "S355"),
        ada.Beam("col_upper", (0, 0, 4), (0, 0, 8), "IPE300", "S355"),
    )
    ada.Assembly("A") / part

    _, source = emit(part, tmp_path, name="stack")

    graph = check_emitted_script(source, name="stack.py")
    cylinders = graph.by_method("getByBoundingCylinder")
    assert len(cylinders) == 2
    assert cylinders[0].kwargs["center1"] != cylinders[1].kwargs["center1"]
    assert sorted(graph.created_names("Set")) == ["col_lower", "col_upper"]


def test_the_writer_is_deterministic(frame_model, tmp_path):
    """Golden files are worthless if a set or dict iteration order leaks into the output."""
    part = frame_model.get_part("Frame")

    # Same file name, two directories, so nothing about the destination can differ.
    _, first = emit(part, tmp_path / "a")
    _, second = emit(part, tmp_path / "b")

    assert first == second


def test_the_emitted_script_matches_the_golden_text(frame_model, tmp_path):
    """Drift detection. Review the diff; regenerate only once you have agreed with it."""
    _, source = emit(frame_model.get_part("Frame"), tmp_path)
    actual = normalise(source)

    if os.environ.get(REGEN_ENV):
        GOLDEN.write_text(actual, encoding="utf-8", newline="\n")
        pytest.fail("golden regenerated from this run; unset {} and review the diff".format(REGEN_ENV))
    assert GOLDEN.exists(), "no golden yet -- run once with {}=1 and review what it writes".format(REGEN_ENV)

    assert actual == GOLDEN.read_text(encoding="utf-8")


def test_the_golden_model_still_carries_an_asymmetric_section(frame_model):
    """The brace is the only member whose product of inertia is non-zero. Keep it that way."""
    brace = frame_model.get_by_name("brace")

    assert brace.section.type == BaseTypes.ANGULAR
    assert brace.section.properties.Iyz != 0.0


def test_a_dot_in_a_name_is_sanitised_and_recorded(tmp_path):
    """Probed: CAE rejects `has.dot` outright with `invalid name`, while spaces and dashes are fine."""
    part = ada.Part("Fra.me")
    part / ada.Beam("bm.1", (0, 0, 0), (2, 0, 0), "IPE300", "S355")
    ada.Assembly("A") / part

    destination, source = emit(part, tmp_path, name="dotted")

    graph = check_emitted_script(source, name="dotted.py")
    assert all("." not in name for name in graph.created_names("Part"))
    assert all("." not in name for name in graph.created_names("Set"))
    sidecar = destination.with_suffix("").with_suffix(".name_map.json")
    candidates = sorted(tmp_path.glob("*name_map.json"))
    assert candidates, "names were changed but no name_map sidecar was written (looked for {})".format(sidecar)


@pytest.mark.parametrize(
    "beam",
    [
        pytest.param(
            ada.BeamTapered("t1", (0, 0, 0), (2, 0, 0), "IPE300", "IPE200", mat="S355"),
            id="tapered",
        ),
        pytest.param(
            ada.Beam("e1", (0, 0, 0), (2, 0, 0), "IPE300", "S355", e1=(0, 0, 0.66)),
            id="eccentric",
        ),
    ],
)
def test_phase_one_refuses_what_it_cannot_carry(beam, tmp_path):
    """A tapered member emitted as a prism, or an offset silently dropped, opens and solves and is wrong."""
    part = ada.Part("Refuse")
    part / beam
    ada.Assembly("A") / part

    with pytest.raises(Exception):
        part.to_abaqus_cae_script(tmp_path / "refused.py")


def test_unit_scale_multiplies_every_coordinate(frame_model, tmp_path):
    """A jacket built 1000x too small is the classic silent unit failure."""
    part = frame_model.get_part("Frame")

    _, metres = emit(part, tmp_path, name="m")
    _, millimetres = emit(part, tmp_path, name="mm", unit_scale=1000.0)

    graph_m = ScriptGraph(metres)
    graph_mm = ScriptGraph(millimetres)
    wires_m = [c.kwargs["points"] for c in graph_m.by_method("WirePolyLine")]
    wires_mm = [c.kwargs["points"] for c in graph_mm.by_method("WirePolyLine")]
    assert len(wires_m) == len(wires_mm) == 3
    for before, after in zip(wires_m, wires_mm):
        for (p1, p2), (q1, q2) in zip(before, after):
            assert q1 == pytest.approx(tuple(v * 1000.0 for v in p1))
            assert q2 == pytest.approx(tuple(v * 1000.0 for v in p2))
    # and the profiles are scaled with them, or the model would be a hairline frame
    heights = {c.kwargs.get("h") for c in graph_mm.profile_calls() if "h" in c.kwargs}
    assert heights == {300.0}
