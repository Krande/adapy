"""A connection library under development: its ``ada_connections.toml``, and loading it.

A connection library is a set of Python modules whose ``@register_connection`` decorators fill
the spec registry. ``ada dev connections --dir DIR`` points the studio at one; the library
declares itself in ``DIR/ada_connections.toml``::

    [library]
    name = "my-connections"                 # shown in the studio (default: the folder name)
    package = "my_project.connections"      # imported -- with every submodule -- to register specs
    # loader = "loader.py:load_all"         # ...or a callable that does the importing itself
    watch = ["."]                           # what a change reloads (default: the toml's folder)
    python_path = ["../.."]                 # prepended to sys.path, for a library not installed
    # handler_kwargs = "dev.py:preview_kwargs"  # a callable returning extra builder kwargs

Every path is relative to the toml. A ``loader`` / ``handler_kwargs`` reference is
``module:function`` or ``file.py:function``.

:class:`Library` loads it and reloads it in place: the modules it owns (those under a watched
folder, or under ``package``) are dropped from ``sys.modules`` and their specs from the
registry, then imported again, so heavy dependencies the library imports stay loaded.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import pathlib
import pkgutil
import sys
import tomllib
import traceback
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable

from ada.api.connections.spec import (
    all_registered,
    get_registered,
    spec_to_form_schema,
    unregister_modules,
)

CONFIG_FILENAME = "ada_connections.toml"

logger = logging.getLogger(__name__)


class LibraryConfigError(ValueError):
    """``ada_connections.toml`` is missing or does not describe a library."""


@dataclass(frozen=True)
class LibraryConfig:
    path: pathlib.Path
    name: str
    packages: tuple[str, ...] = ()
    loader: str | None = None
    watch: tuple[pathlib.Path, ...] = ()
    python_path: tuple[pathlib.Path, ...] = ()
    handler_kwargs: str | None = None

    @property
    def root(self) -> pathlib.Path:
        return self.path.parent


def find_config(path: str | pathlib.Path) -> pathlib.Path:
    """The library's toml: ``path`` itself, or ``path/ada_connections.toml``."""
    p = pathlib.Path(path).expanduser().resolve()
    if p.is_dir():
        p = p / CONFIG_FILENAME
    if not p.is_file():
        raise LibraryConfigError(
            f"{p}: no such file. A connection library declares itself in {CONFIG_FILENAME}, e.g.\n\n"
            '[library]\npackage = "my_project.connections"\n'
        )
    return p


def load_config(path: str | pathlib.Path) -> LibraryConfig:
    toml_path = find_config(path)
    try:
        doc = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise LibraryConfigError(f"{toml_path}: {e}") from e
    table = doc.get("library")
    if not isinstance(table, dict):
        raise LibraryConfigError(f"{toml_path}: no [library] table")
    root = toml_path.parent

    def _strs(key: str) -> tuple[str, ...]:
        value = table.get(key)
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return tuple(value)
        raise LibraryConfigError(f"{toml_path}: library.{key} must be a string or a list of strings")

    def _opt_str(key: str) -> str | None:
        value = table.get(key)
        if value is not None and not isinstance(value, str):
            raise LibraryConfigError(f"{toml_path}: library.{key} must be a string")
        return value

    packages = _strs("package")
    loader = _opt_str("loader")
    if not packages and loader is None:
        raise LibraryConfigError(f"{toml_path}: [library] needs a `package` or a `loader`")
    watch = tuple((root / w).resolve() for w in (_strs("watch") or (".",)))
    python_path = tuple((root / p).resolve() for p in _strs("python_path"))
    return LibraryConfig(
        path=toml_path,
        name=_opt_str("name") or root.name,
        packages=packages,
        loader=loader,
        watch=watch,
        python_path=python_path,
        handler_kwargs=_opt_str("handler_kwargs"),
    )


@dataclass
class LoadResult:
    ok: bool
    error: str | None = None
    traceback: str | None = None
    modules: list[str] = field(default_factory=list)


class Library:
    """A loaded connection library, reloadable in place."""

    def __init__(self, config: LibraryConfig):
        self.config = config
        self.generation = 0
        self._owned: set[str] = set()
        self._foreign: set[str] = set()
        self._watch_prefixes = tuple(
            os.path.normcase(str(w)) + ("" if w.is_file() else os.sep) for w in self.config.watch
        )
        self._kwargs_factory: Callable[[], dict[str, Any]] | None = None

    # -- loading ---------------------------------------------------------------------------

    def load(self) -> LoadResult:
        """Import the library. A failure is reported, not raised: the studio shows it and the
        next change tries again."""
        for p in reversed(self.config.python_path):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        importlib.invalidate_caches()
        before = set(sys.modules)
        try:
            for package in self.config.packages:
                _import_recursive(package)
            if self.config.loader:
                _resolve_callable(self.config.loader, self.config.root)()
            self._kwargs_factory = (
                _resolve_callable(self.config.handler_kwargs, self.config.root) if self.config.handler_kwargs else None
            )
            result = LoadResult(ok=True)
        except BaseException as e:  # noqa: BLE001 - a SystemExit in user code must not kill the host
            if isinstance(e, KeyboardInterrupt):
                raise
            result = LoadResult(ok=False, error=f"{type(e).__name__}: {e}", traceback=format_user_traceback(e))
        self._owned |= {name for name in set(sys.modules) - before if self._owns(name)}
        self.generation += 1
        result.modules = sorted(self._owned)
        return result

    def reload(self) -> LoadResult:
        """Forget the library's modules and specs, then :meth:`load` it again."""
        owned = sorted(self._owned | {name for name in sys.modules if self._owns(name)})
        unregister_modules(owned)
        for name in owned:
            module = sys.modules.pop(name, None)
            # A .pyc is validated by the source's mtime in whole seconds and its size, so an edit
            # within the second that keeps the size (``2`` -> ``3``) would load the old bytecode.
            cached = getattr(module, "__cached__", None)
            if cached:
                pathlib.Path(cached).unlink(missing_ok=True)
        self._owned.clear()
        return self.load()

    def _owns(self, module_name: str) -> bool:
        """Whether a reload drops ``module_name``: it is under ``package`` or its file is under a
        watched folder. Asked of every module in ``sys.modules`` (thousands), so a string compare
        per module, with the answer for a module outside the library remembered."""
        if any(module_name == p or module_name.startswith(p + ".") for p in self.config.packages):
            return True
        if module_name in self._foreign:
            return False
        file = getattr(sys.modules.get(module_name), "__file__", None)
        owned = bool(file) and os.path.normcase(os.path.abspath(file)).startswith(self._watch_prefixes)
        if not owned:
            self._foreign.add(module_name)
        return owned

    # -- what it offers ----------------------------------------------------------------------

    def specs(self) -> list[dict[str, Any]]:
        """Every registered spec as the studio's form needs it, plus where its builder lives."""
        from ada.api.connections.sample import default_inputs

        out = []
        for reg in sorted(all_registered(), key=lambda r: r.spec.name):
            fn = getattr(reg.fn, "__wrapped__", reg.fn)
            schema = spec_to_form_schema(reg.spec)
            try:
                defaults = default_inputs(reg.spec)
            except Exception:  # noqa: BLE001 - a spec whose defaults cannot be derived still lists
                defaults = reg.spec.defaults or {}
            schema["defaults"] = defaults
            out.append(
                {
                    "name": reg.spec.name,
                    "schema": schema,
                    "defaults": defaults,
                    "module": getattr(reg.fn, "__module__", None),
                    "source": _source_location(fn),
                    "in_library": self._owns(getattr(reg.fn, "__module__", "") or ""),
                }
            )
        return out

    def build(self, spec_name: str, inputs: dict[str, dict[str, Any]], glb_path: pathlib.Path) -> dict[str, Any]:
        """Build ``spec_name`` from ``inputs`` (missing fields take the spec's defaults) into
        ``glb_path``. Raises what the builder raises; warnings it logs come back in ``log``."""
        from ada.api.connections import build_component
        from ada.api.connections.sample import default_inputs

        spec = get_registered(spec_name).spec
        merged = {role: {**fields, **(inputs.get(role) or {})} for role, fields in default_inputs(spec).items()}
        extra = dict(self._kwargs_factory() or {}) if self._kwargs_factory else {}
        with _CapturedLog() as log:
            conn = build_component(spec_name, merged, name=spec_name.replace(".", "_"), **extra)
            conn.to_gltf(glb_path)
        parts = conn.get_all_subparts(include_self=True)
        run, contacts_by_weld, clash_note = _measure_joint(glb_path, conn)
        return {
            "inputs": merged,
            "log": log.lines,
            "counts": {
                "beams": sum(len(p.beams) for p in parts),
                "plates": sum(len(p.plates) for p in parts),
                "welds": sum(len(p.welds) for p in parts),
            },
            "properties": _joint_properties(conn, list(merged), contacts_by_weld),
            # The clash the joint is: the members' measured contact in the built GLB, as a
            # convex-contact run (``ada.clash.sidecar``) the studio's Clashes view reads.
            "clash_run": run,
            "clash_note": clash_note,
        }


def _joint_members(conn) -> list:
    """The joint's own members: what ``build_component`` placed at the top of the connection (the
    builder's stiffeners and welds sit in its child part)."""
    return [*conn.beams, *conn.plates]


def _measure_joint(glb_path: pathlib.Path, conn) -> tuple[dict | None, dict[str, set[str]], str | None]:
    """Measure the built GLB's contacts (adacpp, ``ada.clash.contacts``).

    Returns the run restricted to the contacts BETWEEN the joint's members -- the clash that is the
    joint -- with roles and the applicable specs resolved against the connection; which members
    each weld touches (from the unrestricted run); and why there is no run, where there is none.
    A failed measurement never fails the build.
    """
    import dataclasses

    from ada.clash.contacts import annotate_applicable, available, check_glb

    if not available():
        return None, {}, "the installed adacpp cannot measure contacts"
    try:
        run = check_glb(
            glb_path.read_bytes(),
            target={"kind": "file", "source_key": "preview"},
            glb_key="preview",
            up_axis="z",
            engine="worker",
        )
        names = [str(m["name"]) for m in run.members]
        members = {m.name for m in _joint_members(conn)}
        touches: dict[str, set[str]] = {}
        for c in run.contacts:
            a, b = (names[k] for k in c.members)
            touches.setdefault(a, set()).add(b)
            touches.setdefault(b, set()).add(a)
        joint = tuple(c for c in run.contacts if {names[k] for k in c.members} <= members)
        annotated, _ = annotate_applicable(dataclasses.replace(run, contacts=joint), conn)
        return annotated.to_dict(), touches, None
    except Exception as e:  # noqa: BLE001 - the preview stands without its clash
        logger.warning("dev-connections: measuring the joint's contacts failed", exc_info=True)
        return None, {}, f"measuring the contacts failed: {type(e).__name__}: {e}"


def _joint_properties(conn, roles: list[str], touches: dict[str, set[str]]) -> dict[str, Any]:
    """What the studio lists for the joint: each member (its role, section or thickness, material,
    the cuts on it) and each weld (its metadata as the GLB carries it, plus whether it touches the
    members it says it joins)."""
    from ada import Beam
    from ada.comms.msg_handling.object_metadata import beam_metadata, plate_metadata
    from ada.visit.scene_handling.scene_from_part import _weld_metadata

    role_of = {f"sample_{role}": role for role in roles}
    members = []
    for m in _joint_members(conn):
        meta = (
            beam_metadata(m.name, m.section, m.material)
            if isinstance(m, Beam)
            else plate_metadata(m.name, m.t, m.material)
        )
        meta["role"] = role_of.get(m.name)
        meta["booleans"] = len(m.booleans)
        if isinstance(m, Beam):
            meta["length"] = float(m.length)
        members.append(meta)

    welds = []
    for part in conn.get_all_subparts(include_self=True):
        for w in part.welds:
            meta = {"name": w.name, **_weld_metadata(w)}
            touched = sorted(touches.get(w.name, set()))
            meta["touches"] = touched
            # Only judged where the contacts were measured at all.
            meta["joins_its_members"] = set(meta["members"]) <= set(touched) if touches else None
            welds.append(meta)
    return {"members": members, "welds": welds}


class _CapturedLog(logging.Handler):
    """Collects WARNING+ records from every logger while a build runs."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.lines: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append({"level": record.levelname, "logger": record.name, "message": record.getMessage()})
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> _CapturedLog:
        logging.getLogger().addHandler(self)
        self._patched = []
        # Loggers that do not propagate (a library's own) would bypass the root handler.
        for lg in list(logging.Logger.manager.loggerDict.values()):
            if isinstance(lg, logging.Logger) and not lg.propagate:
                lg.addHandler(self)
                self._patched.append(lg)
        return self

    def __exit__(self, *exc) -> None:
        logging.getLogger().removeHandler(self)
        for lg in self._patched:
            lg.removeHandler(self)


_HERE = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))


def format_user_traceback(exc: BaseException) -> str:
    """``exc``'s traceback from the first frame that is the library's own: the frames of this
    package (the host, the loader, ``build_component``) and of the import machinery that led
    there are the same every time and only push the developer's code out of view. A failure
    inside this package itself keeps every frame."""
    tb = exc.__traceback__
    while tb is not None:
        file = tb.tb_frame.f_code.co_filename
        module = tb.tb_frame.f_globals.get("__name__", "")
        machinery = file.startswith("<frozen") or module.split(".")[0] in ("importlib", "pkgutil")
        if not (machinery or os.path.normcase(os.path.dirname(os.path.abspath(file))) == _HERE):
            break
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb if tb is not None else exc.__traceback__))


def is_declined(exc: BaseException) -> bool:
    """Whether a builder DECLINED (``NotApplicable``: "not mine") rather than failed. Recognised by
    the class name anywhere in its MRO, as the detail loop does, so a library's own
    ``NotApplicable`` counts on a core that predates :class:`~ada.api.connections.spec.NotApplicable`."""
    return any(cls.__name__ == "NotApplicable" for cls in type(exc).__mro__)


def _import_recursive(package: str) -> list[ModuleType]:
    """Import ``package`` and every public submodule under it."""
    mod = importlib.import_module(package)
    imported = [mod]
    locations = getattr(mod, "__path__", None)
    if locations is None:
        return imported
    for info in pkgutil.walk_packages(locations, prefix=f"{package}."):
        if any(part.startswith("_") for part in info.name[len(package) + 1 :].split(".")):
            continue
        imported.append(importlib.import_module(info.name))
    return imported


def _resolve_callable(ref: str, root: pathlib.Path) -> Callable[..., Any]:
    """``module:function`` or ``file.py:function`` (relative to ``root``)."""
    target, sep, attr = ref.rpartition(":")
    if not sep or not target or not attr:
        raise LibraryConfigError(f"{ref!r}: expected `module:function` or `file.py:function`")
    if target.endswith(".py"):
        file = (root / target).resolve()
        mod_name = f"_ada_connections_{file.stem}_{abs(hash(str(file)))}"
        spec = importlib.util.spec_from_file_location(mod_name, file)
        if spec is None or spec.loader is None:
            raise LibraryConfigError(f"{file}: cannot be imported")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    fn = getattr(module, attr, None)
    if not callable(fn):
        raise LibraryConfigError(f"{ref!r}: {attr} is not a callable in {target}")
    return fn


def _source_location(fn: Any) -> dict[str, Any] | None:
    import inspect

    try:
        file = inspect.getsourcefile(fn)
        _, line = inspect.getsourcelines(fn)
    except (OSError, TypeError):
        return None
    return {"file": file, "line": line}
