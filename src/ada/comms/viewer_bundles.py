"""Installed viewer bundles: a whole viewer build, plugins included, that a package installs beside adapy.

adapy's own viewer carries no plugins: they join the frontend at build time. A package that wants the
viewer with its plugins (a company's result plugins, a UI shell) builds the viewer with them and
installs the result as a bundle, so a local viewer can use it without building anything:

    <prefix>/share/ada/viewers/<name>/
        bundle.json     {"name": ..., "title": ..., "adapy_version": ..., "plugins": [...], "default_ui": ...}
        dist/           the hosted build (index.html + assets/), served by the REST app
        index.zip       optional: the single-file build that ``show()`` and notebooks open

It is plain files, so a conda package with no Python in it can install one. ``ADA_VIEWER_BUNDLE``
(or ``--viewer`` on ``ada view`` / ``ada serve api``) picks one by name; unset, the built-in viewer
is used. ``ADA_VIEWER_BUNDLE_PATH`` adds directories to look in (``os.pathsep``-separated), ahead of
the environment's own.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from dataclasses import dataclass, field

ENV_BUNDLE = "ADA_VIEWER_BUNDLE"
ENV_BUNDLE_PATH = "ADA_VIEWER_BUNDLE_PATH"
MANIFEST = "bundle.json"
#: Names that ask for the built-in viewer, as an unset variable does.
BUILTIN_NAMES = frozenset({"", "builtin", "core", "default"})


@dataclass(frozen=True)
class ViewerBundle:
    name: str
    root: pathlib.Path
    title: str = ""
    adapy_version: str = ""
    plugins: tuple[str, ...] = field(default_factory=tuple)
    default_ui: str | None = None

    @property
    def dist(self) -> pathlib.Path | None:
        """The hosted build, if the bundle has one."""
        d = self.root / "dist"
        return d if (d / "index.html").is_file() else None

    @property
    def embed_zip(self) -> pathlib.Path | None:
        """The single-file build, if the bundle has one."""
        z = self.root / "index.zip"
        return z if z.is_file() else None


def bundle_dirs() -> list[pathlib.Path]:
    """Where bundles are looked for, first match wins: ``ADA_VIEWER_BUNDLE_PATH``, then the
    environment's ``share/ada/viewers`` (``Library/share`` too, where conda puts it on Windows)."""
    dirs = [pathlib.Path(p) for p in os.environ.get(ENV_BUNDLE_PATH, "").split(os.pathsep) if p.strip()]
    prefix = pathlib.Path(sys.prefix)
    dirs += [prefix / "share" / "ada" / "viewers", prefix / "Library" / "share" / "ada" / "viewers"]
    return dirs


def _read(root: pathlib.Path) -> ViewerBundle | None:
    try:
        meta = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    return ViewerBundle(
        name=str(meta.get("name") or root.name),
        root=root,
        title=str(meta.get("title") or ""),
        adapy_version=str(meta.get("adapy_version") or ""),
        plugins=tuple(str(p) for p in meta.get("plugins") or ()),
        default_ui=meta.get("default_ui") or None,
    )


def list_viewer_bundles() -> dict[str, ViewerBundle]:
    """Every installed bundle by name. A directory without a readable ``bundle.json`` is not one."""
    found: dict[str, ViewerBundle] = {}
    for d in bundle_dirs():
        if not d.is_dir():
            continue
        for root in sorted(p for p in d.iterdir() if p.is_dir()):
            bundle = _read(root)
            if bundle is not None:
                found.setdefault(bundle.name, bundle)
    return found


def resolve_viewer_bundle(name: str | None = None) -> ViewerBundle | None:
    """The bundle named (else ``ADA_VIEWER_BUNDLE``), or None for the built-in viewer.

    An unknown name is an error rather than a quiet fall back to the built-in viewer: whoever asked
    for a bundle expects its plugins, and a viewer without them looks like a broken plugin."""
    if name is None:
        name = os.environ.get(ENV_BUNDLE, "")
    name = name.strip()
    if name.lower() in BUILTIN_NAMES:
        return None
    bundles = list_viewer_bundles()
    if name not in bundles:
        have = ", ".join(sorted(bundles)) or "none installed"
        raise ValueError(
            f'no viewer bundle "{name}" ({ENV_BUNDLE}); installed: {have}. Looked in: '
            + ", ".join(str(d) for d in bundle_dirs())
        )
    return bundles[name]
