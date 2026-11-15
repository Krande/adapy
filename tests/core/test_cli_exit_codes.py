"""The exit-code contract in docs/cli.md: 1 for a failed or refused operation, 2 for a wrong command line."""

from __future__ import annotations

import argparse
import os

import pytest

from ada_cli import audit
from ada_cli.main import main


@pytest.fixture(autouse=True)
def connection(monkeypatch):
    monkeypatch.setenv("ADAPY_API_BASE", "http://viewer.invalid")
    monkeypatch.setenv("ADAPY_API_TOKEN", "t")
    monkeypatch.setenv("ADAPY_API_SCOPE", "s")


def test_files_delete_with_nothing_named_is_a_usage_error():
    assert main(["files", "delete"]) == 2


def test_files_upload_of_a_missing_file_is_a_usage_error(tmp_path):
    assert main(["files", "upload", str(tmp_path / "absent.ifc")]) == 2


def test_build_with_a_missing_config_is_a_usage_error(tmp_path):
    assert main(["build", "run", "--config", str(tmp_path / "missing.toml")]) == 2


def test_wasm_sweep_without_node_is_a_failed_operation(monkeypatch):
    monkeypatch.setattr(audit.shutil, "which", lambda name: None)
    assert main(["audit", "wasm-sweep", "run-1"]) == 1


def test_wasm_sweep_without_pyodide_is_a_failed_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(audit.shutil, "which", lambda name: "/bin/node")
    monkeypatch.setattr(audit, "_resolve_driver", lambda args: str(tmp_path / "driver.mjs"))
    assert main(["audit", "wasm-sweep", "run-1"]) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["serve", "api", "--max-jobs", "0"],
        ["serve", "api", "--cpu-budget", "lots"],
        ["desktop", "--max-jobs", "999"],
        ["desktop", "--cpu-budget", "150%"],
    ],
)
def test_a_job_limit_out_of_range_is_a_usage_error(argv, monkeypatch, capsys):
    monkeypatch.delenv("ADA_LOCAL_JOB_PROCESSES", raising=False)
    monkeypatch.delenv("ADA_LOCAL_JOB_CPU_BUDGET", raising=False)
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"ada {argv[0]}: error: --")
    # Refused before anything was handed on to the server.
    assert "ADA_LOCAL_JOB_PROCESSES" not in os.environ
    assert "ADA_LOCAL_JOB_CPU_BUDGET" not in os.environ


def test_job_limits_reach_the_server_through_its_environment(monkeypatch):
    from ada_cli.main import _use_local_job_opts

    monkeypatch.delenv("ADA_LOCAL_JOB_PROCESSES", raising=False)
    monkeypatch.delenv("ADA_LOCAL_JOB_CPU_BUDGET", raising=False)
    _use_local_job_opts(argparse.Namespace(max_jobs=3, cpu_budget="50%"))
    assert os.environ["ADA_LOCAL_JOB_PROCESSES"] == "3"
    assert os.environ["ADA_LOCAL_JOB_CPU_BUDGET"] == "50%"


def test_the_docs_list_every_code():
    from pathlib import Path

    docs = Path(__file__).resolve().parents[2] / "docs" / "cli.md"
    if not docs.is_file():
        pytest.skip("docs/ is not shipped with the installed package's tests")
    text = docs.read_text(encoding="utf-8")
    assert all(f"| `{code}`" in text for code in (0, 1, 2, 3))
