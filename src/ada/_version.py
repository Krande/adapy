"""adapy version, exposed as ``ada.__version__``.

pyproject.toml ``[project].version`` is the single source of truth (the CI release tooling
bumps it); installing the package records that as the distribution metadata read here. Resolved
once at import — one lookup, negligible next to importing ``ada``. ``ada-py-core`` ships the
importable code on the conda-forge split; ``ada-py`` is the wheel / metapackage. ``0.0.0`` for an
uninstalled source checkout.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version


def _resolve_version() -> str:
    for _dist in ("ada-py-core", "ada-py"):
        try:
            return _pkg_version(_dist)
        except PackageNotFoundError:
            continue
    return "0.0.0"


__version__ = _resolve_version()
