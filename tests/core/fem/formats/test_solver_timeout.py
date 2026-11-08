"""A solver run that is given a timeout, or interrupted, leaves no process behind.

``subprocess.run`` kills only the process it started, and a solver is a tree of them: Code_Aster's ``run_aster`` is a
batch file (a script elsewhere) that starts Python, which starts the solver. ``to_fem(..., execute=True)`` had no
timeout, and a Ctrl-C while it waited left Code_Aster running. Here the solver is a stand-in: a Python process that
starts a child which sleeps, each writing its process id, so the test can see that both are gone.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

from ada.fem.formats import utils

#: The stand-in solver: writes its pid, starts a child that writes its own and sleeps, then waits for it.
TREE = """
import pathlib, subprocess, sys, time
out = pathlib.Path(sys.argv[1])
(out / "parent.pid").write_text(str(__import__("os").getpid()))
child = subprocess.Popen([sys.executable, "-c",
    "import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(120)",
    str(out / "child.pid")])
child.wait()
"""


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # a zombie (killed, not yet reaped by its parent) counts as gone
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        pass
    # no /proc (macOS): ask ps for the process state
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_for(path: pathlib.Path, seconds: float = 30.0) -> int:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError(f"{path.name} was not written")


def _gone(pids, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def tree(tmp_path):
    script = tmp_path / "tree.py"
    script.write_text(TREE)
    yield [sys.executable, str(script), str(tmp_path)], tmp_path
    for name in ("parent.pid", "child.pid"):  # never leave the stand-in running, whatever the test did
        f = tmp_path / name
        if f.exists() and f.read_text().strip() and _alive(int(f.read_text())):
            pid = int(f.read_text())
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, 9)


def test_a_timeout_kills_the_whole_process_tree(tree):
    cmd, out = tree
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        utils.run_process(cmd, timeout=3, text=True)
    assert time.monotonic() - start < 60
    pids = [_wait_for(out / "parent.pid"), _wait_for(out / "child.pid")]
    assert _gone(pids), "a process of the tree outlived the timeout"


def test_an_interrupt_kills_the_whole_process_tree_and_is_raised_again(tree, monkeypatch):
    cmd, out = tree
    communicate = subprocess.Popen.communicate
    calls = []

    def interrupted(self, *args, **kwargs):
        calls.append(self)
        if len(calls) > 1:  # the kill's own subprocess calls, and the reaping
            return communicate(self, *args, **kwargs)
        _wait_for(out / "child.pid")
        raise KeyboardInterrupt  # what Ctrl-C raises in the waiting Python

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        utils.run_process(cmd, text=True)
    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
    pids = [_wait_for(out / "parent.pid"), _wait_for(out / "child.pid")]
    assert _gone(pids), "a process of the tree outlived the interrupt"


def test_without_a_timeout_the_run_is_waited_for_as_before(tmp_path):
    out = utils.run_process([sys.executable, "-c", "print('done')"], text=True)
    assert out.returncode == 0 and out.stdout.strip() == "done"


@pytest.mark.skipif(sys.platform == "darwin", reason="running a solver locally is not implemented on macOS (run_macOS)")
def test_to_fem_passes_the_timeout_to_code_aster_and_kills_it(tmp_path, monkeypatch):
    """``Assembly.to_fem(..., "code_aster", execute=True, timeout=...)`` with a stand-in for ``run_aster`` that never
    finishes, started the way ``CodeAsterExecute.run`` starts it (``_run_local``): the timeout is raised, and neither
    the stand-in nor the process it started is left running. (The command is replaced rather than the executable: a
    batch file in a temporary folder may not be allowed to run.)"""
    import ada
    from ada.fem import Bc, FemSet, LoadPoint, StepImplicitStatic
    from ada.fem.formats.code_aster.execute import CodeAsterExecute

    script = tmp_path / "tree.py"
    script.write_text(TREE)
    pids_dir = tmp_path / "pids"
    pids_dir.mkdir()

    def run(self, exit_on_complete=True):
        return self._run_local([sys.executable, str(script), str(pids_dir)], exit_on_complete=exit_on_complete)

    monkeypatch.setattr(CodeAsterExecute, "run", run)

    bm = ada.Beam("bm", (0, 0, 0), (1, 0, 0), "IPE100")
    p = ada.Part("P") / bm
    p.fem = bm.to_fem_obj(0.5, "line")
    root = [n for n in p.fem.nodes if abs(n.x) < 1e-9]
    tip = [n for n in p.fem.nodes if abs(n.x - 1) < 1e-9]
    p.fem.add_bc(Bc("fix", p.fem.add_set(FemSet("root", root, FemSet.TYPES.NSET)), [1, 2, 3, 4, 5, 6]))
    a = ada.Assembly("A") / p
    step = a.fem.add_step(StepImplicitStatic("static"))
    step.add_load(LoadPoint("tip", -1000.0, p.fem.add_set(FemSet("tip", tip, FemSet.TYPES.NSET)), 3))

    try:
        with pytest.raises(subprocess.TimeoutExpired):
            a.to_fem("ca_timeout", "code_aster", scratch_dir=tmp_path, overwrite=True, execute=True, timeout=3)
        pids = [_wait_for(pids_dir / "parent.pid"), _wait_for(pids_dir / "child.pid")]
        assert _gone(pids), "the stand-in solver outlived the timeout"
        assert "P time_limit 900" in (tmp_path / "ca_timeout" / "ca_timeout.export").read_text()
    finally:
        for name in ("parent.pid", "child.pid"):
            f = pids_dir / name
            if f.exists() and f.read_text().strip() and _alive(int(f.read_text())):
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/PID", f.read_text().strip()], capture_output=True)
                else:
                    os.kill(int(f.read_text()), 9)
