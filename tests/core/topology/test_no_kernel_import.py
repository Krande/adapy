"""Guard: ada.topology must never import a CAD kernel directly.

The whole point of routing through ada.cad is that the topology layer is
kernel-agnostic. Importing OCC.* (or adacpp) anywhere under ada/topology would
re-couple it to a specific kernel, so we assert the source is clean and that
the package imports without any kernel present in sys.modules.
"""
from __future__ import annotations

import pathlib

import ada.topology


def test_topology_source_has_no_direct_kernel_imports():
    pkg_dir = pathlib.Path(ada.topology.__file__).parent
    offenders = []
    for py in pkg_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "import OCC" in stripped or "from OCC" in stripped:
                offenders.append(f"{py.name}:{lineno}: {stripped}")
            if "import adacpp" in stripped or "from adacpp" in stripped:
                offenders.append(f"{py.name}:{lineno}: {stripped}")
    assert not offenders, "ada.topology must not import a CAD kernel directly:\n" + "\n".join(offenders)


def test_importing_topology_pulls_in_no_kernel():
    # In a clean subprocess, importing ada.topology must not drag a CAD kernel
    # into sys.modules (lazy-import discipline for slim/wasm environments).
    import subprocess
    import sys

    code = (
        "import sys; import ada.topology; "
        "loaded = [m for m in sys.modules if m == 'OCC' or m.startswith('OCC.') or m == 'adacpp' or m.startswith('adacpp.')]; "
        "print('LOADED:' + ','.join(sorted(loaded)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "LOADED:" in out.stdout
    loaded = out.stdout.split("LOADED:")[1].strip()
    assert loaded == "", f"ada.topology import pulled in a CAD kernel: {loaded}"
