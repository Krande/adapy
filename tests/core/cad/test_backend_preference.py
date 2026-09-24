"""With both kernels installed, adacpp is the default everywhere a kernel is auto-selected.

Every shipped environment carries adacpp alone, so what it selects is what production runs.
An environment that also carries pythonocc -- tests-xkernel, or a user who installs both --
must select the same thing, and must select it consistently: a CAD backend and a document
backend on different kernels hand each other shapes neither can read.

These run only where both kernels are present (the ``both_backends`` fixture), which is
tests-xkernel in CI.
"""

from __future__ import annotations

import pytest

from ada.cad import AdacppBackend, select_backend
from ada.cad.doc import AdacppDocBackend, select_doc_backend
from ada.occ.backend import OccBackend
from ada.occ.doc_backend import OccDocBackend


@pytest.fixture
def no_backend_env(monkeypatch):
    for var in ("ADAPY_CAD_BACKEND", "ADAPY_DOC_BACKEND"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_auto_selection_prefers_adacpp(both_backends, no_backend_env):
    assert isinstance(select_backend(), AdacppBackend)
    assert isinstance(select_doc_backend(), AdacppDocBackend)


@pytest.mark.parametrize("value", ["occ", "pythonocc-core", "pyocc"])
def test_asking_for_occ_moves_both_backends(both_backends, no_backend_env, value):
    """The opt-out: ADAPY_CAD_BACKEND=occ must take the document backend with it, not leave
    it on adacpp."""
    no_backend_env.setenv("ADAPY_CAD_BACKEND", value)
    assert isinstance(select_backend(), OccBackend)
    assert isinstance(select_doc_backend(), OccDocBackend)


def test_bare_cad_config_takes_the_adacpp_track(both_backends):
    from ada.cad.registry import CadConfig, TessellationPath

    assert CadConfig().path == TessellationPath.ADACPP_LIBTESS2
    assert CadConfig().env()["ADAPY_CAD_BACKEND"] == "adacpp"


def test_default_tessellator_takes_adacpp_without_a_declared_default(both_backends, monkeypatch):
    """An adacpp that declares no default track: OCC leads the track list whenever pythonocc
    is importable, and must not win on list position alone."""
    import dataclasses

    import ada.cad.plan as plan
    from ada.cad.registry import CadBackendName, available_tess_tracks

    tracks = [dataclasses.replace(t, is_default=False) for t in available_tess_tracks()]
    assert tracks[0].backend == CadBackendName.OCC  # the precondition this test is about
    monkeypatch.setattr(plan, "available_tess_tracks", lambda: tracks)

    assert plan.Tessellator.default().resolved.backend == CadBackendName.ADACPP
