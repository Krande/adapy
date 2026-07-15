"""Composable conversion plans (ada.cad.plan).

The contract under test is mostly about REFUSAL. A plan's job is to be honoured exactly as
written or not at all: the failures this abstraction exists to prevent are the ones where a
serializer accepts a kernel it cannot drive and meshes with a different one, which is
indistinguishable from success at every layer above. So "raises" is the assertion, and
"silently produced a GLB" is the bug.

Kept free of adacpp where possible — composition, lowering and vocabulary errors are all
answerable without a kernel.
"""

from __future__ import annotations

import os

import pytest

from ada.cad import ConversionPlan, PlanError, Serializer, Tessellator
from ada.cad.registry import CadBackendName, StepReader, available_tess_tracks


def _adacpp_tracks():
    return [t for t in available_tess_tracks() if t.backend is CadBackendName.ADACPP]


def _neutral_track():
    return next((t for t in _adacpp_tracks() if t.neutral), None)


def _taxonomy_track():
    return next((t for t in _adacpp_tracks() if not t.neutral), None)


def test_serializer_or_tessellator_composes_a_plan():
    plan = Serializer.cpp(threads=3) | Tessellator("adacpp:cdt", deflection=1.5)
    assert isinstance(plan, ConversionPlan)
    assert plan.serializer.name == "cpp" and plan.serializer.threads == 3
    assert plan.tessellator.track == "adacpp:cdt" and plan.tessellator.deflection == 1.5


def test_with_returns_a_variant_and_leaves_the_base_alone():
    base = Serializer.cpp() | Tessellator("adacpp:cdt")
    variant = base.with_(track="adacpp:libtess2")
    assert variant.tessellator.track == "adacpp:libtess2"
    assert base.tessellator.track == "adacpp:cdt", "plans are frozen; with_ must not mutate the base"


def test_plan_lowers_to_cad_config():
    """CadConfig is the transport (worker env); the plan is the front door. The lowering is the
    contract with everything predating the plan."""
    plan = Serializer.python(step_reader=StepReader.AUTO) | Tessellator("adacpp:cdt", deflection=2.0, angular_deg=10.0)
    cfg = plan.to_cad_config()
    assert cfg.path == "adacpp:cdt"
    assert cfg.deflection == 2.0 and cfg.angular_deg == 10.0
    assert cfg.step_reader is StepReader.AUTO


def test_unknown_track_is_refused_and_names_the_alternatives():
    plan = Serializer.python() | Tessellator("adacpp:does-not-exist")
    with pytest.raises(PlanError, match="unknown tessellation track"):
        plan.validate()


def test_unknown_serializer_is_refused():
    plan = ConversionPlan(serializer=Serializer(name="nope"), tessellator=Tessellator.default())
    with pytest.raises(PlanError, match="unknown serializer"):
        plan.validate()


@pytest.mark.skipif(_taxonomy_track() is None, reason="this adacpp declares no taxonomy track")
def test_cpp_refuses_a_taxonomy_track():
    """The native reader builds no taxonomy geometry, and adacpp meshes such a track as though
    untracked rather than erroring — so the plan must refuse rather than return a GLB attributed
    to a kernel that never ran."""
    tax = _taxonomy_track()
    plan = Serializer.cpp() | Tessellator(tax.name)
    with pytest.raises(PlanError, match="taxonomy"):
        plan.validate()


@pytest.mark.skipif(_neutral_track() is None, reason="no neutral adacpp track available")
def test_python_accepts_any_available_track():
    """The python path drives every declared track — the neutral restriction is the native
    reader's, not the vocabulary's."""
    for t in _adacpp_tracks():
        (Serializer.python() | Tessellator(t.name)).validate()


@pytest.mark.skipif(_neutral_track() is None, reason="no neutral adacpp track available")
def test_fuses_only_for_cpp_step_to_glb(tmp_path):
    step = tmp_path / "x.stp"
    step.write_text("")
    neutral = _neutral_track().name

    assert (Serializer.cpp() | Tessellator(neutral)).fuses(step) is True
    assert (Serializer.python() | Tessellator(neutral)).fuses(step) is False
    # non-STEP source: no whole-file native entry point exists for it
    other = tmp_path / "x.ifc"
    other.write_text("")
    assert (Serializer.cpp() | Tessellator(neutral)).fuses(other) is False


@pytest.mark.skipif(_neutral_track() is None, reason="no neutral adacpp track available")
def test_tess_env_does_not_leak_between_plans(tmp_path):
    """adaptive/face_regions travel only by env and are inherited by the Python path's pool, so a
    plan that pins them must not colour the next conversion in the same process."""
    from ada.cad.plan import _tess_env

    before = os.environ.get("ADA_STREAM_TESS_ADAPTIVE")
    with _tess_env(Tessellator(_neutral_track().name, adaptive=True)):
        assert os.environ["ADA_STREAM_TESS_ADAPTIVE"] == "1"
    assert os.environ.get("ADA_STREAM_TESS_ADAPTIVE") == before, "env must be restored on exit"


def test_default_tessellator_is_the_declared_default():
    """adapy must not name the default track — adacpp declares which one it is."""
    tracks = available_tess_tracks()
    if not tracks:
        pytest.skip("no tessellation track available")
    expected = next((t for t in tracks if t.is_default), tracks[0])
    assert Tessellator.default().track == expected.name
