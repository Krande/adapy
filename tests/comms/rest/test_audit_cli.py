"""Unit tests for the `ada audit` CLI client (HTTP mocked)."""

import argparse
import json

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
    rc = ac.cmd_log(
        _args(
            limit=50,
            pages=1,
            source=".fem",
            target="step",
            status="error",
            key=None,
            grep="closed",
            action=None,
            since=None,
            until=None,
        )
    )
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


def test_cmd_log_server_side_action_and_since(monkeypatch, capsys):
    seen = {}

    def fake(base, token, path):
        seen["path"] = path
        return {"entries": [], "next_before_id": None}

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_log(
        _args(
            limit=50,
            pages=1,
            source=None,
            target=None,
            status=None,
            key=None,
            grep=None,
            action="view",
            since="6h",
            until="2026-01-01T00:00:00Z",
        )
    )
    assert rc == 0
    assert "action=view" in seen["path"]
    assert "since=6h" in seen["path"]
    assert "until=2026-01-01" in seen["path"]
    assert "(no matching audit rows)" in capsys.readouterr().err


# ── loads: per-load browser metrics ────────────────────────────────────────


def _loads_page(entries, next_before_id=None):
    return {"entries": entries, "next_before_id": next_before_id}


def test_cmd_loads_device_and_limit_filter_plus_client_metrics_fetch(monkeypatch, capsys):
    page = _loads_page(
        [
            {
                "id": 1,
                "ts": "2026-06-06T10:11:12.345+00:00",
                "status": "done",
                "key": "cad/a.glb",
                "device_id": "abc12345xyz",
                "error": None,
            },
            {
                "id": 2,
                "ts": "2026-06-06T10:12:00+00:00",
                "status": "done",
                "key": "cad/b.glb",
                "device_id": "other99999",
                "error": None,
            },
            {
                "id": 3,
                "ts": "2026-06-06T10:13:00+00:00",
                "status": "error",
                "key": "cad/c.glb",
                "device_id": "abc12345aaa",
                "error": "load failed",
            },
        ]
    )
    seen_paths = []

    def fake(base, token, path):
        seen_paths.append(path)
        if path.startswith("/api/admin/audit?"):
            return page
        assert "/client-metrics" in path
        audit_id = int(path.split("/api/admin/audit/")[1].split("/")[0])
        return {
            "audit_id": audit_id,
            "client_metrics": {
                "transport": "relayed",
                "total_ms": 1200,
                "ttfb_ms": 50,
                "download_ms": 400,
                "parse_ms": 100,
                "prepare_ms": 60,
                "first_render_ms": 30,
                "transfer_bytes": 2048,
                "triangles": 5000,
                "profile_frames": [{"fn": "decode", "self_ms": 5}],
            },
        }

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_loads(
        _args(
            kind="view",
            since=None,
            until=None,
            key=None,
            device="abc123",
            pages=1,
            limit=50,
            profile=False,
        )
    )
    assert rc == 0

    # Only the two device-matching rows should have triggered a client-metrics fetch.
    cm_paths = [p for p in seen_paths if "/client-metrics" in p]
    assert len(cm_paths) == 2
    assert "/api/admin/audit/1/client-metrics" in cm_paths
    assert "/api/admin/audit/3/client-metrics" in cm_paths

    out = capsys.readouterr().out
    assert "a.glb" in out and "relayed" in out
    assert "b.glb" not in out  # filtered out by --device
    assert "c.glb" in out and "load failed" in out  # error snippet shown


def test_cmd_loads_server_side_query_params_in_path(monkeypatch):
    seen = {}

    def fake(base, token, path):
        seen["path"] = path
        return _loads_page([])

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_loads(
        _args(
            kind="render",
            since="1d",
            until=None,
            key="cad/a.glb",
            device=None,
            pages=1,
            limit=50,
            profile=False,
        )
    )
    assert rc == 0
    assert seen["path"].startswith("/api/admin/audit?")
    assert "action=render" in seen["path"]
    assert "since=1d" in seen["path"]
    assert "key=cad" in seen["path"]  # urlencoded key substring


def test_cmd_loads_json_drops_profile_frames_unless_profile_flag(monkeypatch, capsys):
    page = _loads_page(
        [{"id": 1, "ts": "2026-06-06T10:11:12+00:00", "status": "done", "key": "cad/a.glb", "device_id": "d1"}]
    )

    def fake(base, token, path):
        if path.startswith("/api/admin/audit?"):
            return page
        return {"audit_id": 1, "client_metrics": {"total_ms": 10, "profile_frames": [{"fn": "x", "self_ms": 1}]}}

    monkeypatch.setattr(ac, "_get_json", fake)

    rc = ac.cmd_loads(
        _args(kind="view", since=None, until=None, key=None, device=None, pages=1, limit=50, profile=False, json=True)
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert "profile_frames" not in rows[0]["client_metrics"]
    assert rows[0]["client_metrics"]["total_ms"] == 10

    rc = ac.cmd_loads(
        _args(kind="view", since=None, until=None, key=None, device=None, pages=1, limit=50, profile=True, json=True)
    )
    assert rc == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["client_metrics"]["profile_frames"] == [{"fn": "x", "self_ms": 1}]


# ── loads-summary ───────────────────────────────────────────────────────────


def test_cmd_loads_summary_view_table(monkeypatch, capsys):
    def fake(base, token, path):
        assert path.startswith("/api/admin/audit/frontend-loads?")
        assert "frontend-loads/hotspots" not in path
        return {
            "since_days": 1,
            "cells": [
                {
                    "key": "cad/a.glb",
                    "sample_count": 10,
                    "fail_count": 1,
                    "total_ms_p50": 900,
                    "total_ms_p95": 2000,
                    "network_ms": 300.0,
                    "cpu_ms": 150.0,
                    "gpu_ms": 20.0,
                    "dominant_bound": "network",
                }
            ],
        }

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_loads_summary(_args(since_days=1, kind="view"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "cad/a.glb" in out
    assert "network" in out


def test_cmd_loads_summary_render_endpoint(monkeypatch, capsys):
    seen = {}

    def fake(base, token, path):
        seen["path"] = path
        return {
            "since_days": 7,
            "cells": [
                {
                    "key": "cad/a.glb",
                    "window_count": 5,
                    "fps_p50": 58.0,
                    "fps_min": 40.0,
                    "frame_ms_p50": 17.0,
                    "gpu_ms_p50": 5.0,
                    "triangles_p50": 12000,
                    "dominant_bound": "cpu",
                }
            ],
        }

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_loads_summary(_args(since_days=7, kind="render"))
    assert rc == 0
    assert seen["path"].startswith("/api/admin/audit/render?")
    out = capsys.readouterr().out
    assert "cad/a.glb" in out and "cpu" in out


# ── loads-hotspots ───────────────────────────────────────────────────────────


def test_cmd_loads_hotspots_endpoint_params(monkeypatch, capsys):
    seen = {}

    def fake(base, token, path):
        seen["path"] = path
        return {
            "functions": [
                {"fn": "decodeMesh", "samples": 4, "self_ms_sum": 120.0, "self_ms_avg": 30.0, "is_wasm": True},
            ],
            "loads_in_window": 4,
            "key": "cad/a.glb",
            "kind": "view",
            "since_days": 3,
        }

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_loads_hotspots(_args(since_days=3, key="cad/a.glb", kind="view", limit=100))
    assert rc == 0
    assert seen["path"].startswith("/api/admin/audit/frontend-loads/hotspots?")
    assert "key=cad" in seen["path"]
    assert "since=3" in seen["path"]
    assert "kind=view" in seen["path"]
    out = capsys.readouterr().out
    assert "decodeMesh" in out and "loads_in_window=4" in out


def test_cmd_loads_hotspots_empty_hints_to_stderr(monkeypatch, capsys):
    def fake(base, token, path):
        return {"functions": [], "loads_in_window": 0, "key": None, "kind": "view", "since_days": 1}

    monkeypatch.setattr(ac, "_get_json", fake)
    rc = ac.cmd_loads_hotspots(_args(since_days=1, key=None, kind="view", limit=100))
    assert rc == 0
    assert "no profiled frames" in capsys.readouterr().err


# ── parser wiring ─────────────────────────────────────────────────────────


def test_add_parser_routes_subcommands():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    ac.add_parser(sub)
    for name, fn in [
        ("runs", ac.cmd_runs),
        ("log", ac.cmd_log),
        ("loads", ac.cmd_loads),
        ("loads-summary", ac.cmd_loads_summary),
        ("loads-hotspots", ac.cmd_loads_hotspots),
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
