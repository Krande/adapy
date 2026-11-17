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
        # Core's built-in specs are registered too, up front: measuring a joint's clash resolves the
        # specs that claim it against the whole registry, built-ins included, and registers them
        # if they are not -- so without this the spec list would grow after the first build.
        from ada.clash.builtin_specs import register_builtin_specs

        register_builtin_specs()
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
        return spec_entries(self._owns)

    def subject(self, spec_name: str, inputs: dict[str, dict[str, Any]], path: pathlib.Path) -> dict[str, Any]:
        """Build ``spec_name`` as :meth:`build` does and write its weld assembly to ``path``
        (:func:`build_subject`)."""
        return build_subject(spec_name, inputs, path, self._extra_kwargs())

    def build(self, spec_name: str, inputs: dict[str, dict[str, Any]], glb_path: pathlib.Path) -> dict[str, Any]:
        """Build ``spec_name`` from ``inputs`` into ``glb_path`` (:func:`build_with_report`)."""
        return build_with_report(spec_name, inputs, glb_path, self._extra_kwargs())

    def _extra_kwargs(self) -> dict[str, Any]:
        return dict(self._kwargs_factory() or {}) if self._kwargs_factory else {}


# -- building a spec, with what the studio shows of it -------------------------------------------
#
# Module-level, not Library methods: the same build answers ``ada dev connections`` (a library held
# in a child process) and a worker's ``component_build`` job in report mode (the specs the worker's
# installed packages registered), so the studio's connection builder reads one shape from both.


def spec_entries(owns: Callable[[str], bool] | None = None) -> list[dict[str, Any]]:
    """Every registered spec as the studio's form needs it, plus where its builder lives.
    ``owns(module)`` says whether a builder's module is the served library's own (``in_library``)."""
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
        module = getattr(reg.fn, "__module__", None)
        out.append(
            {
                "name": reg.spec.name,
                "schema": schema,
                "defaults": defaults,
                "module": module,
                "source": _source_location(fn),
                "in_library": bool(owns and owns(module or "")),
            }
        )
    return out


def _merged_inputs(spec_name: str, inputs: dict[str, dict[str, Any]]) -> tuple[Any, dict[str, dict[str, Any]]]:
    from ada.api.connections.sample import default_inputs

    spec = get_registered(spec_name).spec
    return spec, {role: {**fields, **(inputs.get(role) or {})} for role, fields in default_inputs(spec).items()}


def build_with_report(
    spec_name: str,
    inputs: dict[str, dict[str, Any]],
    glb_path: pathlib.Path,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``spec_name`` from ``inputs`` (missing fields take the spec's defaults) into
    ``glb_path``. Raises what the builder raises; warnings it logs come back in ``log``."""
    from ada.api.connections import build_component

    _, merged = _merged_inputs(spec_name, inputs)
    with _CapturedLog() as log:
        conn = build_component(spec_name, merged, name=spec_name.replace(".", "_"), **(extra_kwargs or {}))
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


def build_subject(
    spec_name: str,
    inputs: dict[str, dict[str, Any]],
    path: pathlib.Path,
    extra_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build ``spec_name`` as :func:`build_with_report` does and write its weld assembly (a
    simulation subject, :mod:`ada.simulation.subjects`) to ``path``. ``ValueError`` when the detail
    has no welds; raises what the builder raises."""
    from ada.api.connections import build_component
    from ada.simulation.subjects import (
        WeldAssemblyError,
        dump_weld_assembly,
        weld_assembly_from_part,
    )

    _, merged = _merged_inputs(spec_name, inputs)
    with _CapturedLog() as log:
        conn = build_component(spec_name, merged, name=spec_name.replace(".", "_"), **(extra_kwargs or {}))
    if not sum(len(p.welds) for p in conn.get_all_subparts(include_self=True)):
        raise ValueError("the detail has no welds")
    try:
        assembly = weld_assembly_from_part(
            conn,
            stub_length=None,
            source={"kind": "connection-spec", "spec_name": spec_name, "spec_inputs": merged},
        )
        data = dump_weld_assembly(assembly)
    except WeldAssemblyError as e:
        raise ValueError(str(e)) from e
    pathlib.Path(path).write_bytes(data)
    return {
        "inputs": merged,
        "log": log.lines,
        "welds": len(assembly["welds"]),
        "members": len(assembly["members"]),
    }


def _joint_members(conn) -> list:
    """The joint's own members: what ``build_component`` placed at the top of the connection (the
    builder's stiffeners and welds sit in its child part)."""
    return [*conn.beams, *conn.plates]


# Whether a weld touches its members is asked of solids that are meant to meet exactly. The check's
# defaults (a 10 mm margin, patches under 1 mm² dropped) are for a model's clashes, and drop the thin
# patch where a corner fillet meets a rounded hollow section: those welds then read "not joined".
_JOINT_CONTACT_OPTIONS = {"security_margin": 0.002, "min_contact_area": 0.0}


def _measure_joint(
    glb_path: pathlib.Path, conn
) -> tuple[dict | None, dict[str, dict[str, tuple[float, float, float]]], str | None]:
    """Measure the built GLB's contacts (adacpp, ``ada.clash.contacts``).

    Returns the run restricted to the contacts BETWEEN the joint's members -- the clash that is the
    joint -- with roles and the applicable specs resolved against the connection; for every object,
    what it touches and where (from the unrestricted run); and why there is no run, where there is
    none. A failed measurement never fails the build.
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
            options=_JOINT_CONTACT_OPTIONS,
        )
        names = [str(m["name"]) for m in run.members]
        members = {m.name for m in _joint_members(conn)}
        touches: dict[str, dict[str, tuple[float, float, float]]] = {}
        for c in run.contacts:
            a, b = (names[k] for k in c.members)
            # Where they touch: the midpoint of the two nearest points, in the GLB's model frame.
            at = tuple((p + q) / 2 for p, q in zip(*c.near_points))
            touches.setdefault(a, {})[b] = at
            touches.setdefault(b, {})[a] = at
        joint = tuple(c for c in run.contacts if {names[k] for k in c.members} <= members)
        annotated, _ = annotate_applicable(dataclasses.replace(run, contacts=joint), conn)
        return annotated.to_dict(), touches, None
    except Exception as e:  # noqa: BLE001 - the preview stands without its clash
        logger.warning("dev-connections: measuring the joint's contacts failed", exc_info=True)
        return None, {}, f"measuring the contacts failed: {type(e).__name__}: {e}"


def _joint_properties(
    conn, roles: list[str], touches: dict[str, dict[str, tuple[float, float, float]]]
) -> dict[str, Any]:
    """What the studio lists for the joint: each member (its role, section or thickness, material,
    the cuts on it) and each weld (its metadata as the GLB carries it, plus whether it touches the
    members it says it joins). Positions are in the GLB's model frame, for the studio's overlays:
    a beam's ``axis`` (n1, n2), a plate's ``centre``, a weld's ``at`` (where it meets its members)."""
    from ada import Beam
    from ada.api.weld_symbols import weld_symbol_svg
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
            meta["axis"] = [_xyz(m.n1.p), _xyz(m.n2.p)]
        else:
            meta["centre"] = _xyz(m.poly.get_centroid())
        members.append(meta)

    welds = []
    for part in conn.get_all_subparts(include_self=True):
        for w in part.welds:
            meta = {"name": w.name, **_weld_metadata(w)}
            touched = touches.get(w.name, {})
            meta["touches"] = sorted(touched)
            # Only judged where the contacts were measured at all.
            meta["joins_its_members"] = set(meta["members"]) <= set(touched) if touches else None
            meta["at"] = _weld_position(w, [touched[m] for m in meta["members"] if m in touched])
            # Its symbol (ISO 2553, `ada.api.weld_symbols`) as an SVG icon in the text colour.
            meta["symbol_svg"] = weld_symbol_svg(w.type, size=20) if w.type is not None else None
            welds.append(meta)
    return {"members": members, "welds": welds}


def _xyz(p) -> list[float]:
    return [float(v) for v in p]


def _weld_position(weld, contact_points: list[tuple[float, float, float]]) -> list[float] | None:
    """Where to label a weld: on its line, nearest the mean of where it meets its members; else
    its line's middle.

    On its line, not at that mean: around a tube, a weld meets its members all the way round, and
    the mean of where is the middle of the tube -- inside it, nowhere near the weld.
    """
    points = getattr(getattr(weld, "sweep_curve", None), "points3d", None)
    line = [_xyz(p) for p in points] if points is not None else []
    if not contact_points:
        return line[len(line) // 2] if line else None
    mean = [sum(p[i] for p in contact_points) / len(contact_points) for i in range(3)]
    if not line:
        return mean
    return min(line, key=lambda p: sum((p[i] - mean[i]) ** 2 for i in range(3)))


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
