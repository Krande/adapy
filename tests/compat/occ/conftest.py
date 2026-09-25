"""The tests under ``tests/core/occ`` exercise the pythonocc (OCC) backend's internals directly
(BRepMesh quality, wire building, runaway-face recovery, seam detection, ...). They are inherently
OCC-specific, so skip the whole directory where pythonocc isn't installed — e.g. the adacpp-only
test env — instead of erroring at collection on ``import OCC``.
"""

import importlib.util

if importlib.util.find_spec("OCC") is None:
    collect_ignore_glob = ["*.py"]
