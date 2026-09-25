"""A clash check is about the ASSEMBLY, not about the file it came from.

`identify_joints` takes a `Part`. Nothing in it knows what was read, and the REST worker routes
every source through one loader (`converters/ada_load._load_with_ada`) with no per-format gate.
These pin that this stays true, because it is the kind of property that holds until someone adds
a convenient special case for the format they happened to be working on.

WHAT "SUPPORTED" MEANS HERE. A check needs MEMBERS -- beams and plates. A format supports it when
its reader yields those. A format whose reader yields shapes (STEP, SAT: solids with no member
semantics) yields none, and the honest answer is the warning saying so, not a crash and not a
silent zero. Both are pinned below, because the difference between them is what a reader of the
result has to be able to tell.
"""

from __future__ import annotations

import pytest

import ada
from ada.clash.identify import run_clash_check
from ada.clash.options import ClashOptions

_OPTS = ClashOptions(include_plate_joints=False)


def _frame() -> ada.Assembly:
    """Two girders and a column meeting at one node -- one joint, whatever carries it."""
    return ada.Assembly("a") / (
        ada.Part("p")
        / [
            ada.Beam("g0", (0, 0, 0), (5, 0, 0), "IPE200"),
            ada.Beam("g1", (5, 0, 0), (5, 5, 0), "IPE200"),
            ada.Beam("c0", (5, 0, 0), (5, 0, 3), "IPE200"),
        ]
    )


def test_ifc_round_trips_to_the_same_joint(tmp_path):
    src = tmp_path / "m.ifc"
    _frame().to_ifc(src, validate=False)
    result = run_clash_check(ada.from_ifc(src), source_key=src.name, options=_OPTS)
    assert len(result.joints) == 1
    assert sorted(m.name for m in result.joints[0].members) == ["c0", "g0", "g1"]


def test_genie_xml_round_trips_to_the_same_joint(tmp_path):
    """The same model through a completely different reader has to answer the same."""
    src = tmp_path / "m.xml"
    _frame().to_genie_xml(src)
    result = run_clash_check(ada.from_genie_xml(src), source_key=src.name, options=_OPTS)
    assert len(result.joints) == 1
    assert sorted(m.name for m in result.joints[0].members) == ["c0", "g0", "g1"]


def test_a_mesh_deliberately_turned_into_members_is_checkable():
    """An FEA model is not checked as a mesh -- it is checked once someone converts it.

    `iter_objects_from_fem` is that conversion, and it is 1:1 per element: a physical beam
    arrives as a CHAIN of collinear segments sharing nodes. Those interior nodes are not joints,
    and nothing special is done to suppress them -- consecutive collinear segments are PARALLEL,
    which the beam pass already rejects. This pins that, because the alternative (a joint at every
    mesh node) would be a confident, plausible, useless answer.
    """
    model = ada.from_fem("files/fem_files/sesam/beamMassT1.FEM")

    converted = ada.Part("from_fem")
    for part in model.get_all_subparts(include_self=True):
        if part.fem is not None and len(part.fem.elements):
            for obj in part.iter_objects_from_fem(detached=False):
                converted.add_object(obj)

    beams = list(converted.get_all_physical_objects(by_type=ada.Beam))
    assert len(beams) > 1, "the fixture is a beam model; if this is 0 the reader changed"

    result = run_clash_check(ada.Assembly("a") / converted, source_key="beamMassT1.FEM", options=_OPTS)
    assert result.joints, "a framed beam model has corners"

    # No joint may consist of members that are all mutually collinear -- that is a mesh node.
    for joint in result.joints:
        assert len(joint.members) >= 2
        assert joint.type_key.split("|")[-1] != "unknown"


def test_a_shapes_only_source_says_so_rather_than_reporting_zero(tmp_path):
    """STEP has solids, not members. "Nothing to check" and "nothing found" differ."""
    src = tmp_path / "m.stp"
    _frame().to_stp(src)

    model = ada.from_step(src)
    assert list(model.get_all_physical_objects()), "the STEP reader produced no objects at all"

    result = run_clash_check(model, source_key=src.name, options=_OPTS)
    assert result.joints == ()
    assert any(
        "no beams or plates" in w for w in result.warnings
    ), "a source a check cannot answer must SAY so; a bare zero reads as 'this model has no joints'"


@pytest.mark.parametrize("ext", [".ifc", ".xml"])
def test_the_worker_loader_handles_each_format_without_a_per_format_gate(ext, tmp_path):
    """The REST path's loader, exercised directly -- one door for every format."""
    from ada.comms.rest.converters.ada_load import _load_with_ada

    src = tmp_path / f"m{ext}"
    if ext == ".ifc":
        _frame().to_ifc(src, validate=False)
    else:
        _frame().to_genie_xml(src)

    result = run_clash_check(_load_with_ada(src, ext), source_key=src.name, options=_OPTS)
    assert len(result.joints) == 1
