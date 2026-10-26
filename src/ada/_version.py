"""adapy version, exposed as ``ada.__version__``.

pyproject.toml ``[project].version`` is the single source of truth (the CI release tooling
bumps it); installing the package records that as the distribution metadata read here. Resolved
once at import — one lookup, negligible next to importing ``ada``. ``ada-py-core`` ships the
importable code on the conda-forge split; ``ada-py`` is the wheel / metapackage.

``ADAPY_VERSION`` in the environment wins over everything, so CI can stamp the version it is
building. Without it or distribution metadata — adapy run off ``PYTHONPATH`` from a source copy,
as the docs, worker and viewer images do — the ``pyproject.toml`` beside ``src/`` is read.
``0.0.0`` only when none of those resolve (#464: paradoc reports stamped adapy as v0.0.0).
"""

import os
import pathlib
import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _version_from_pyproject() -> str | None:
    # src/ada/_version.py -> parents[2] is the checkout root holding pyproject.toml.
    pp = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = pp.read_text(encoding="utf-8")
    except OSError:
        return None
    # Only adapy's own pyproject: an unrelated one two levels above an install is not ours.
    if not re.search(r'(?m)^name\s*=\s*["\']ada-py["\']', text):
        return None
    m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else None


def _resolve_version() -> str:
    env = (os.environ.get("ADAPY_VERSION") or "").strip()
    if env:
        return env
    for _dist in ("ada-py-core", "ada-py"):
        try:
            return _pkg_version(_dist)
        except PackageNotFoundError:
            continue
    return _version_from_pyproject() or "0.0.0"


__version__ = _resolve_version()
