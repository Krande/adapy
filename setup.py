"""Project metadata lives in pyproject.toml; this file only adds a build hook.

``pyproject.toml [project].version`` is the single source of truth (bumped by the CI release
tooling). At build time we stamp that version into ``ada/_version.py`` so an installed
wheel/conda package exposes the real ``ada.__version__`` as a plain literal — no runtime
metadata lookup. The committed ``src/ada/_version.py`` keeps a placeholder for source checkouts.
"""

import pathlib
import re

from setuptools import setup
from setuptools.command.build_py import build_py


def _pyproject_version() -> str:
    pp = pathlib.Path(__file__).parent / "pyproject.toml"
    m = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', pp.read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


class BuildPyWithVersion(build_py):
    """Overwrite the built copy of ada/_version.py with the real version (source stays a
    placeholder)."""

    def run(self):
        super().run()
        target = pathlib.Path(self.build_lib) / "ada" / "_version.py"
        if target.exists():
            target.write_text(f'__version__ = "{_pyproject_version()}"\n', encoding="utf-8")


setup(cmdclass={"build_py": BuildPyWithVersion})
