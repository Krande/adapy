"""The native IFC read and write paths run where ifcopenshell cannot be imported.

ifcopenshell is a development dependency (the oracle the parity tests compare against); at runtime
the native reader and writer must not need it. Each case runs in a fresh interpreter whose import
system refuses ``ifcopenshell``, so a stray import anywhere on the path fails the test.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap

import pytest

import ada
from ada.cadit.ifc.read.native_reader import native_adacpp_ifc_available
from ada.cadit.ifc.write.native_ifc_writer import native_ifc_writer_available

pytestmark = pytest.mark.skipif(not native_adacpp_ifc_available(), reason="adacpp without IfcNgeomStream")

_BLOCK = textwrap.dedent(
    """
    import json, sys

    class _NoIfcOpenShell:
        def find_spec(self, name, path=None, target=None):
            if name == "ifcopenshell" or name.startswith("ifcopenshell."):
                raise ImportError(f"ifcopenshell is blocked in this test ({name})")
            return None

    sys.meta_path.insert(0, _NoIfcOpenShell())
    """
)


def _run(body: str, *args: str) -> dict:
    src = pathlib.Path(ada.__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(src), env.get("PYTHONPATH", "")])
    done = subprocess.run(
        [sys.executable, "-c", _BLOCK + textwrap.dedent(body), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def _model(tmp_path) -> pathlib.Path:
    beams = [ada.Beam(f"bm{i}", (i, 0, 0), (i, 0, 1), "IPE300") for i in range(2)]
    plate = ada.Plate("pl0", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    src = tmp_path / "model.ifc"
    (ada.Assembly("a") / (ada.Part("p") / [*beams, plate])).to_ifc(src)
    return src


def test_native_read_without_ifcopenshell(tmp_path):
    src = _model(tmp_path)
    out = _run(
        """
        import ada
        a = ada.from_ifc(sys.argv[1], reader="native")
        print(json.dumps({
            "names": sorted(o.name for o in a.get_all_physical_objects()),
            "source": str(a.ifc_source_path),
            "store_built": a._ifc_store is not None,
            "loaded": sorted(m for m in sys.modules if m.split(".")[0] == "ifcopenshell"),
        }))
        """,
        str(src),
    )
    assert out["names"] == ["bm0", "bm1", "pl0"]
    assert pathlib.Path(out["source"]) == src
    assert out["store_built"] is False
    assert out["loaded"] == []


def test_default_read_falls_back_to_native_without_ifcopenshell(tmp_path):
    src = _model(tmp_path)
    out = _run(
        """
        import ada
        a = ada.from_ifc(sys.argv[1])
        print(json.dumps({"names": sorted(o.name for o in a.get_all_physical_objects())}))
        """,
        str(src),
    )
    assert out["names"] == ["bm0", "bm1", "pl0"]


@pytest.mark.skipif(not native_ifc_writer_available(), reason="adacpp without blobs_to_ifc")
def test_native_read_write_read_without_ifcopenshell(tmp_path):
    src = _model(tmp_path)
    dst = tmp_path / "rewritten.ifc"
    out = _run(
        """
        import ada
        a = ada.from_ifc(sys.argv[1], reader="native")
        a.to_ifc(sys.argv[2], writer="native", validate=False)
        b = ada.from_ifc(sys.argv[2], reader="native")
        print(json.dumps({"names": sorted(o.name for o in b.get_all_physical_objects())}))
        """,
        str(src),
        str(dst),
    )
    assert out["names"] == ["bm0", "bm1", "pl0"]
