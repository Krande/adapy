"""The backend fixtures' skip/fail contract: a missing kernel skips a test in an ordinary run,
but under ADAPY_REQUIRE_BOTH_KERNELS (the cross-kernel leg, where deselection is off) it must
fail, so that leg cannot report green on a run of skips."""

import pytest


def test_missing_backend_skips_in_an_ordinary_run(monkeypatch):
    from tests import conftest

    monkeypatch.setattr(conftest, "backend_available", lambda backend: False)
    monkeypatch.delenv("ADAPY_REQUIRE_BOTH_KERNELS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        conftest._require_or_skip_backend("occ")


def test_missing_backend_fails_under_require_both_kernels(monkeypatch):
    from tests import conftest

    monkeypatch.setattr(conftest, "backend_available", lambda backend: False)
    monkeypatch.setenv("ADAPY_REQUIRE_BOTH_KERNELS", "1")
    with pytest.raises(pytest.fail.Exception, match="occ backend is not installed"):
        conftest._require_or_skip_backend("occ")


def test_installed_backend_passes(monkeypatch):
    from tests import conftest

    monkeypatch.setattr(conftest, "backend_available", lambda backend: True)
    monkeypatch.delenv("ADAPY_REQUIRE_BOTH_KERNELS", raising=False)
    conftest._require_or_skip_backend("occ")  # must not raise
