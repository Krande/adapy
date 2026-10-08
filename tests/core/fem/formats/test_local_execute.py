"""``fea_execute_dir``: where the launch scripts go, and where the solver runs.

The contract of the original ``LocalExecute`` (2021): ``run.bat``/``stop.bat`` are written to
``<fea_execute_dir>/<analysis name>``, and the solver runs in the deck's folder -- the batch file
itself does ``cd /d <analysis_dir>`` and every solver command names its deck relative to it
(``ccx -i name``, ``run_aster name.export``, ``abaqus job=name``, ``sestra /dsf nameT100``).

Measured before the fix (CalculiX 2.23, Code_Aster 18.1.8, Abaqus 2025, Sestra V11.3-00) with
``ADA_FEA_EXECUTE_DIR`` set, three failures, each hiding the next:

1. ``TypeError: unsupported operand type(s) for /: 'str' and 'str'`` -- the setting was a ``str``;
2. ``FileNotFoundError: ...stop.bat`` -- copied although only Abaqus passes a stop command;
3. the solver started with ``cwd=<execute_dir>`` and could not open its deck
   (``*ERROR in readinput: cannot open file``, ``Command line option "input" must have a value``).

No solver is run here: ``subprocess.run`` is replaced by a recorder.
"""

from __future__ import annotations

import copy
import pathlib
import subprocess
import sys

import pytest

from ada.config import Config
from ada.fem.formats.abaqus.execute import AbaqusExecute
from ada.fem.formats.calculix.execute import CalculixExecute
from ada.fem.formats.utils import LocalExecute

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the launch scripts are written on Windows only")
not_macos = pytest.mark.skipif(
    sys.platform == "darwin", reason="adapy does not run solvers on macOS (run_macOS raises NotImplementedError)"
)


@pytest.fixture
def execute_dir(monkeypatch, tmp_path):
    """``ADA_FEA_EXECUTE_DIR`` set for one test; the process-wide ``Config`` is restored afterwards."""
    cfg = Config()
    saved = copy.deepcopy(cfg.config)
    exec_dir = tmp_path / "exec"
    monkeypatch.setenv("ADA_FEA_EXECUTE_DIR", str(exec_dir))
    cfg.reload_config()
    yield exec_dir
    monkeypatch.delenv("ADA_FEA_EXECUTE_DIR", raising=False)
    cfg.config = saved
    cfg._trigger_update_config()


@pytest.fixture
def deck(tmp_path) -> pathlib.Path:
    analysis_dir = tmp_path / "scratch" / "job1"
    analysis_dir.mkdir(parents=True)
    inp = analysis_dir / "job1.inp"
    inp.write_text("** empty deck\n")
    return inp


@pytest.fixture
def recorded_runs(monkeypatch) -> list[dict]:
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(dict(cmd=cmd, **kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_execute_dir_from_env_is_a_path(execute_dir, deck):
    assert Config().fea_execute_dir == execute_dir
    assert LocalExecute(deck).execute_dir == execute_dir / "job1"


@not_macos
def test_solver_runs_in_the_deck_folder(execute_dir, deck, recorded_runs, monkeypatch):
    monkeypatch.setattr(CalculixExecute, "get_exe", lambda self, fea_software: pathlib.Path("ccx"))

    CalculixExecute(deck).run()

    assert len(recorded_runs) == 1
    assert pathlib.Path(recorded_runs[0]["cwd"]) == deck.parent
    if sys.platform == "win32":
        assert (execute_dir / "job1" / "run.bat").is_file()
        # CalculiX has no stop command: no stop.bat, and nothing copied to the root of the setting
        assert sorted(p.name for p in (execute_dir / "job1").iterdir()) == ["run.bat"]
        assert sorted(p.name for p in execute_dir.iterdir()) == ["job1"]


@windows_only
def test_solver_runs_in_the_deck_folder_when_a_stop_bat_already_exists(execute_dir, deck, recorded_runs, monkeypatch):
    """A leftover ``stop.bat`` in the execute dir must not be what makes the run pass."""
    (execute_dir / "job1").mkdir(parents=True)
    (execute_dir / "job1" / "stop.bat").write_text("")
    (execute_dir / "stop.bat").write_text("")
    monkeypatch.setattr(CalculixExecute, "get_exe", lambda self, fea_software: pathlib.Path("ccx"))

    CalculixExecute(deck).run()

    assert pathlib.Path(recorded_runs[0]["cwd"]) == deck.parent


@windows_only
def test_stop_bat_only_when_there_is_a_stop_command(execute_dir, deck, monkeypatch):
    monkeypatch.setattr(AbaqusExecute, "get_exe", lambda self, fea_software: pathlib.Path("abaqus"))

    AbaqusExecute(deck, auto_execute=False).run()

    stop_bat = execute_dir / "job1" / "stop.bat"
    assert stop_bat.is_file()
    assert "abaqus terminate job=job1" in stop_bat.read_text()
    assert f"cd /d {deck.parent}" in stop_bat.read_text()
    assert (execute_dir / "job1" / "run.bat").is_file()
