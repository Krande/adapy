"""Unit tests for the `ada audit` CLI client (HTTP mocked)."""

import argparse

import pytest

from ada_cli import audit as ac
from ada_cli import load_dotenv_cwd


def _args(**kw):
    base = dict(url="https://v.example", token="tok", json=False)
    base.update(kw)
    return argparse.Namespace(**base)


# ── .env loading ──────────────────────────────────────────────────────────


def test_load_dotenv_cwd(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# a comment\n" "export ADAPY_BASE_URL=viewer.example.com\n" 'ADAPY_API_TOKEN="secret-token"\n' "\n" "EMPTY=\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ADAPY_BASE_URL", raising=False)
    monkeypatch.delenv("ADAPY_API_TOKEN", raising=False)

    assert load_dotenv_cwd() is True
    import os

    assert os.environ["ADAPY_BASE_URL"] == "viewer.example.com"
    assert os.environ["ADAPY_API_TOKEN"] == "secret-token"  # quotes stripped


def test_load_dotenv_does_not_override_existing(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ADAPY_API_TOKEN=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ADAPY_API_TOKEN", "from-shell")
    load_dotenv_cwd()
    import os

    assert os.environ["ADAPY_API_TOKEN"] == "from-shell"  # real env wins


# ── config ────────────────────────────────────────────────────────────────


def test_config_missing_exits(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("ADAPY_BASE_URL", raising=False)
    monkeypatch.delenv("ADAPY_API_BASE", raising=False)
    monkeypatch.delenv("ADAPY_API_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        ac._config(argparse.Namespace(url=None, token=None))


def test_config_normalizes_bare_host():
    base, token = ac._config(argparse.Namespace(url="viewer.example.com", token="t"))
    assert base == "https://viewer.example.com"
    assert token == "t"


# ── filtering logic (mock the HTTP layer) ─────────────────────────────────


def test_cmd_log_filters(monkeypatch, capsys):
    page = {
        "entries": [
            {"id": 1, "key": "fem/a.fem", "target_format": "step", "status": "error", "error": "closed loop"},
            {"id": 2, "key": "fem/b.fem", "target_format": "step", "status": "done", "error": ""},
            {"id": 3, "key": "cad/c.ifc", "target_format": "step", "status": "error", "error": "other"},
            {"id": 4, "key": "fem/d.fem", "target_format": "glb", "status": "error", "error": "closed loop"},
        ],
        "next_before_id": None,
    }
    monkeypatch.setattr(ac, "_get_json", lambda b, t, p: page)
    rc = ac.cmd_log(_args(limit=50, pages=1, source=".fem", target="step", status="error", key=None, grep="closed"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fem/a.fem" in out  # matches all filters
    assert "b.fem" not in out  # status done
    assert "c.ifc" not in out  # not .fem
    assert "d.fem" not in out  # target glb


def test_cmd_run_failed_and_format(monkeypatch, capsys):
    payload = {
        "run": {"id": "r1", "status": "finished", "total": 3, "failed": 1, "started_at": "2026-06-06"},
        "jobs": [
            {"status": "done", "target_format": "step", "key": "ok.fem"},
            {"status": "error", "target_format": "step", "key": "bad.fem", "error": "boom"},
            {"status": "error", "target_format": "glb", "key": "x.fem", "error": "nope"},
        ],
    }
    monkeypatch.setattr(ac, "_get_json", lambda b, t, p: payload)
    rc = ac.cmd_run(_args(run_id="r1", failed=True, format="step"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "bad.fem" in out and "boom" in out
    assert "ok.fem" not in out  # not failed
    assert "x.fem" not in out  # wrong format


def test_cmd_perf_run_mode_hits_cell_endpoint(monkeypatch, capsys):
    seen = {}

    def fake(base, token, path):
        seen["path"] = path
        return {
            "audit_run_id": "r1",
            "trigger": "all",
            "since_days": 30,
            "cells": [
                {
                    "source_ext": "fem",
                    "target_format": "step",
                    "sample_count": 2,
                    "failure_rate": 1.0,
                    "duration_ms_p95": 222000,
                    "peak_rss_kb_p95": 3000000,
                    "peak_rss_max_kb": 3200000,
                    "streaming": {"is_candidate": True},
                },
            ],
        }

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_perf(_args(run="r1", worker_tag=None, trigger=None, source_ext="fem", target=None, since=30, limit=25))
    assert rc == 0
    assert "/api/admin/audit/perf?" in seen["path"]
    assert "audit_run_id=r1" in seen["path"]
    out = capsys.readouterr().out
    assert "fem->step" in out and "yes" in out  # streaming candidate flag


# ── parser wiring ─────────────────────────────────────────────────────────


def test_add_parser_routes_subcommands():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    ac.add_parser(sub)
    for name, fn in [
        ("runs", ac.cmd_runs),
        ("log", ac.cmd_log),
        ("perf", ac.cmd_perf),
        ("profile", ac.cmd_profile),
    ]:
        ns = parser.parse_args(["audit", name] + (["1"] if name == "profile" else []))
        assert ns.func is fn


# ── wasm-sweep: --adacpp-image ────────────────────────────────────────────


def test_wasm_sweep_honours_the_adacpp_image_default(monkeypatch, tmp_path):
    """`--adacpp-image` IS live, and its default decides which engine a sweep validates.

    Worth pinning because it is reasonable to assume otherwise: nearly every `ada audit` subcommand
    (runs / run / log / perf / profile) reports on work the WORKERS did, so the engine is whatever
    the pool happens to run and no client-side image pin could matter. `wasm-sweep` is the one that
    inverts that — it re-runs a prior run's cells LOCALLY under node+pyodide to validate the
    in-browser engine, and the wheel it loads comes from this image. That is why the default has to
    track the viewer's base (tests/core/test_deploy_pins.py pins the equality): it sat at 0.9.0
    while the viewer moved on, so a default sweep validated a wheel six releases behind what shipped
    — silently, since a stale-but-valid wheel sweeps perfectly happily.
    """
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    ac.add_parser(sub)
    ns = parser.parse_args(["audit", "wasm-sweep", "run-1"])
    assert ns.func is ac.cmd_wasm_sweep
    assert ns.adacpp_image == ac.ADACPP_DEFAULT_IMAGE

    # Neither --adacpp-wheel nor $ADACPP_WHEEL set => the image is what gets extracted.
    monkeypatch.delenv("ADACPP_WHEEL", raising=False)
    wheel = tmp_path / "ada_cpp-0.16.1-cp313-cp313-pyodide_2025_0_wasm32.whl"
    wheel.touch()
    seen = {}

    def _fake_extract(image, dest):
        seen["image"] = image
        return wheel

    monkeypatch.setattr(ac, "_extract_adacpp_from_image", _fake_extract)
    ns.out = str(tmp_path)
    assert ac._resolve_adacpp_wheel(ns) == str(wheel)
    assert seen["image"] == ac.ADACPP_DEFAULT_IMAGE, "the --adacpp-image default never reached the extraction"


def test_wasm_sweep_explicit_wheel_beats_the_image(monkeypatch, tmp_path):
    """An explicit --adacpp-wheel short-circuits the image, so a local build can be swept."""
    wheel = tmp_path / "ada_cpp-0.16.1-cp313-cp313-pyodide_2025_0_wasm32.whl"
    wheel.touch()

    def _boom(image, dest):  # pragma: no cover - must not run
        raise AssertionError("must not extract from the image when a wheel is given")

    monkeypatch.setattr(ac, "_extract_adacpp_from_image", _boom)
    monkeypatch.delenv("ADACPP_WHEEL", raising=False)
    ns = argparse.Namespace(adacpp_wheel=str(wheel), adacpp_image="unused", out=str(tmp_path))
    assert ac._resolve_adacpp_wheel(ns) == str(wheel)
