"""Build an external procedural-engine wheel from a git repo.

The engine-build worker clones ``repo_url@ref`` (optionally with an SSH deploy
key), builds a **pure-python** wheel with pip, and hands back the wheel filename
+ bytes for upload under the hidden ``_engines/`` prefix. Pure-python
(``py3-none-any``) wheels are what the browser micropip-installs; a package with
compiled extensions won't be pyodide-compatible (the engine contract is an
OCC-free pure-python ``compile(doc) -> bytes``).

Kept dependency-light (git + pip via subprocess) so it needs nothing beyond the
worker image's toolchain.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

__all__ = ["clone_repo", "build_wheel_from_source", "build_engine_wheel"]


def _pip_base_cmd() -> list[str]:
    """The pip invocation to use — ``python -m pip`` when pip is importable in
    this interpreter (the normal worker image), else the ``pip`` executable on
    PATH. Raises when neither is available."""
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip"]
    pip_exe = shutil.which("pip")
    if pip_exe:
        return [pip_exe]
    raise RuntimeError("no pip available to build the engine wheel")


def clone_repo(repo_url: str, ref: str, dest: str | pathlib.Path, *, ssh_key_path: str | None = None) -> None:
    """Shallow-clone ``repo_url`` at ``ref`` into ``dest``. When ``ssh_key_path``
    is given it is used as the SSH identity (a read-only deploy key), pinned so
    git uses only that key."""
    env = dict(os.environ)
    if ssh_key_path:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        )
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, repo_url, str(dest)],
        check=True,
        capture_output=True,
        env=env,
    )


def build_wheel_from_source(src_dir: str | pathlib.Path) -> tuple[str, bytes]:
    """Build a wheel from the source tree at ``src_dir`` with ``pip wheel
    --no-deps`` and return ``(filename, bytes)``. Raises ``RuntimeError`` if the
    build produces no wheel; the underlying ``CalledProcessError`` (with pip's
    output) propagates on a build failure."""
    src = pathlib.Path(src_dir)
    with tempfile.TemporaryDirectory(prefix="engine_wheel_") as out:
        proc = subprocess.run(
            [*_pip_base_cmd(), "wheel", "--no-deps", "--no-build-isolation", "-w", out, str(src)],
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"pip wheel failed:\n{proc.stdout}\n{proc.stderr}")
        wheels = sorted(pathlib.Path(out).glob("*.whl"))
        if not wheels:
            raise RuntimeError("pip wheel produced no .whl file")
        wheel = wheels[0]
        return wheel.name, wheel.read_bytes()


def build_engine_wheel(repo_url: str, ref: str, *, ssh_key_path: str | None = None) -> tuple[str, bytes]:
    """Clone ``repo_url@ref`` and build its wheel — the full build step for a
    ``kind: "wheel"`` engine. Returns ``(wheel_filename, wheel_bytes)``."""
    with tempfile.TemporaryDirectory(prefix="engine_src_") as tmp:
        src = pathlib.Path(tmp) / "repo"
        clone_repo(repo_url, ref, src, ssh_key_path=ssh_key_path)
        return build_wheel_from_source(src)
