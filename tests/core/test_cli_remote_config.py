"""``resolve_remote_config``: the viewer URL every ``ada`` remote subcommand (files, build upload) uses."""

import pytest

from ada_cli import resolve_remote_config

_NAMES = ("ADAPY_API_BASE", "ADAPY_BASE_URL", "ADAPY_VIEWER_URL", "ADAPY_API_TOKEN", "ADAPY_VIEWER_TOKEN")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _NAMES + ("ADAPY_API_SCOPE", "ADAPY_VIEWER_SCOPE"):
        monkeypatch.delenv(name, raising=False)


def test_a_bare_host_gets_https(monkeypatch):
    monkeypatch.setenv("ADAPY_BASE_URL", "viewer.example.com/")
    assert resolve_remote_config()[0] == "https://viewer.example.com"


def test_an_explicit_scheme_is_kept():
    assert resolve_remote_config(url="http://localhost:8000")[0] == "http://localhost:8000"


def test_no_url_stays_empty():
    assert resolve_remote_config()[0] == ""
