"""The exit-code contract in docs/cli.md: 1 for a failed or refused operation, 2 for a wrong command line."""

from __future__ import annotations

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


def test_the_docs_list_every_code():
    from pathlib import Path

    docs = Path(__file__).resolve().parents[2] / "docs" / "cli.md"
    if not docs.is_file():
        pytest.skip("docs/ is not shipped with the installed package's tests")
    text = docs.read_text(encoding="utf-8")
    assert all(f"| `{code}`" in text for code in (0, 1, 2, 3))
