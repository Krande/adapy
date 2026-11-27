"""Execution policy: which engine runs each operation, in what order.

An administrator decides, per OPERATION and optionally per QUALIFIER, an ordered
fallback list of engines. The viewer tries them in that order, skipping an engine
that cannot handle the input and falling back to the next one when an engine fails:

* ``native``  -- adacpp's embind wasm modules in the browser (no Python runtime),
* ``pyodide`` -- in-browser Python,
* ``worker``  -- the server worker pool.

The policy DOCUMENT is a JSON object keyed by operation, then by qualifier::

    {
      "convert": {"*": ["pyodide", "worker"], ".ifc": ["native", "pyodide", "worker"]},
      "plugin_job": {"*": ["pyodide", "worker"], "example-plugin": ["worker"]}
    }

Operations (:data:`OPERATIONS`): ``convert`` (an explicit conversion: the converter
page, a re-convert), ``read`` (loading a source for viewing), ``fea_bake``,
``procedural_compile`` (qualifier: engine slug), ``plugin_job`` (qualifier:
plugin id) and ``procedural_io`` (a procedural model's Excel export / import, its
relocation proposals and its IFC/Genie export; qualifier: engine slug), ``asset_build`` (an
asset node's on-demand build; qualifier: the build capability of the node's claim) and ``core_job``
(core work that used to need a worker and that the browser now runs first: the clash check,
clash detailing, component builds, the selection export, equipment bounding boxes and scene
utilities; qualifier: the job family, :data:`CORE_JOB_FAMILIES`). For ``convert``/``read``/``fea_bake`` the qualifier is the lower-case
source extension including the dot (``".ifc"``). A lookup takes the row for
``(operation, qualifier)`` and otherwise ``(operation, "*")``.

The GLOBAL document is completed from :data:`DEFAULT_POLICY` (an operation or a
``"*"`` row it does not name keeps the default), and a per-user OVERRIDE replaces
single ``(operation, qualifier)`` rows of it (:func:`effective_policy`).

The server enforces only the ``worker`` part: a submission that declares it chose
the worker over the browser (``X-Ada-Run-On: worker``, see :mod:`.worker_access`)
is refused when the effective policy for its operation does not list ``worker``
(:func:`require_execution_policy`). Which browser engine runs is the browser's
business. Like the worker-access policy this is governance of a client's declared
choice, not a security boundary.

ONE EXCEPTION, ``asset_build``: a browser build's output is stored in SHARED storage for
everyone who reads the node (``POST /assets/build/result``), so the server also judges the
browser part there -- an upload is refused when the effective policy does not list
``pyodide`` for the capability (:func:`require_browser_asset_build`). An administrator
who will not take geometry built in browsers sets that capability's row to the worker.
"""

from __future__ import annotations

import contextvars
import copy
import logging
import pathlib
import re
from collections.abc import Mapping

from fastapi import HTTPException

from . import db as db_module

logger = logging.getLogger(__name__)

OPERATIONS: tuple[str, ...] = (
    "convert",
    "read",
    "fea_bake",
    "procedural_compile",
    "plugin_job",
    "procedural_io",
    "asset_build",
    "core_job",
)
ENGINES: tuple[str, ...] = ("native", "pyodide", "worker")
WILDCARD = "*"
#: Operations whose qualifier is a source extension (``".ifc"``).
EXTENSION_OPERATIONS: frozenset[str] = frozenset({"convert", "read", "fea_bake"})
#: Operations whose qualifier is a procedural engine slug.
ENGINE_OPERATIONS: frozenset[str] = frozenset({"procedural_compile", "procedural_io"})
#: The job features of a procedural model's Excel export / import, relocation proposals and
#: IFC/Genie export: the ``procedural_io`` operation, which the browser runs first.
PROCEDURAL_IO_FEATURES: frozenset[str] = frozenset({"procedural_export", "procedural_import", "procedural_relocations"})
#: Operations whose qualifier is an asset build capability.
CAPABILITY_OPERATIONS: frozenset[str] = frozenset({"asset_build"})
#: The ``core_job`` operation's qualifiers, by the job feature each family is submitted under:
#: core work the browser runs first (``ada.comms.rest.wasm_jobs``). One operation with a row per
#: family, as ``plugin_job`` has a row per plugin: an administrator can send clash detailing to the
#: worker and keep the check in the browser, and the operation list stays short.
CORE_JOB_FAMILIES: dict[str, str] = {
    "clash_check": "clash_check",
    "clash_detail": "clash_detail",
    "component_build": "component_build",
    "export_selection": "export_selection",
    "bbox_inference": "equipment_bbox",
    "utilities": "utility",
}

_EXT_QUALIFIER = re.compile(r"^\.[a-z0-9][a-z0-9_+-]{0,31}$")
_NAME_QUALIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:@/+-]{0,127}$")

_BROWSER_FIRST = ["pyodide", "worker"]
_NATIVE_FIRST = ["native", "pyodide", "worker"]

#: Today's behaviour: the browser first (adacpp's native module first where one
#: covers the input), the server worker pool after it.
DEFAULT_POLICY: dict[str, dict[str, list[str]]] = {
    "convert": {"*": _BROWSER_FIRST, ".ifc": _NATIVE_FIRST, ".step": _NATIVE_FIRST, ".stp": _NATIVE_FIRST},
    "read": {"*": _BROWSER_FIRST, ".ifc": _NATIVE_FIRST, ".step": _NATIVE_FIRST, ".stp": _NATIVE_FIRST},
    "fea_bake": {"*": _BROWSER_FIRST},
    "procedural_compile": {"*": _BROWSER_FIRST},
    "plugin_job": {"*": _BROWSER_FIRST},
    "procedural_io": {"*": _BROWSER_FIRST},
    "asset_build": {"*": _BROWSER_FIRST},
    "core_job": {"*": _BROWSER_FIRST},
}


class ExecutionPolicyError(ValueError):
    """A policy document that does not validate. The message says what is wrong."""


def default_policy() -> dict[str, dict[str, list[str]]]:
    """A fresh copy of :data:`DEFAULT_POLICY` (safe to mutate)."""
    return copy.deepcopy(DEFAULT_POLICY)


def normalise_qualifier(operation: str, qualifier: str) -> str:
    """Lower-case and check ``qualifier`` for ``operation``; raises on a bad one."""
    q = str(qualifier).strip().lower()
    if q == WILDCARD:
        return q
    if operation in EXTENSION_OPERATIONS:
        if not _EXT_QUALIFIER.match(q):
            raise ExecutionPolicyError(
                f"{operation}: qualifier {qualifier!r} must be '*' or a source extension with its dot (e.g. '.ifc')"
            )
        return q
    if operation == "core_job":
        families = sorted(set(CORE_JOB_FAMILIES.values()))
        if q not in families:
            raise ExecutionPolicyError(
                f"{operation}: qualifier {qualifier!r} must be '*' or one of {', '.join(families)}"
            )
        return q
    if not _NAME_QUALIFIER.match(q):
        if operation in ENGINE_OPERATIONS:
            what = "an engine slug"
        elif operation in CAPABILITY_OPERATIONS:
            what = "a build capability"
        else:
            what = "a plugin id"
        raise ExecutionPolicyError(f"{operation}: qualifier {qualifier!r} must be '*' or {what}")
    return q


def validate_policy(doc: object) -> dict[str, dict[str, list[str]]]:
    """Validate a (possibly partial) policy document and return its normalised copy.

    Known operations and engines only, every list non-empty and without
    duplicates, qualifiers well-formed (and unique once lower-cased). ``None`` or
    ``{}`` is the empty document.
    """
    if doc is None:
        return {}
    if not isinstance(doc, Mapping):
        raise ExecutionPolicyError("the execution policy must be a JSON object keyed by operation")
    out: dict[str, dict[str, list[str]]] = {}
    for op, rows in doc.items():
        if op not in OPERATIONS:
            raise ExecutionPolicyError(f"unknown operation {op!r} (known: {', '.join(OPERATIONS)})")
        if not isinstance(rows, Mapping):
            raise ExecutionPolicyError(f"{op}: must be an object keyed by qualifier")
        table: dict[str, list[str]] = {}
        for qualifier, engines in rows.items():
            q = normalise_qualifier(op, qualifier)
            if q in table:
                raise ExecutionPolicyError(f"{op}: qualifier {q!r} appears more than once")
            if not isinstance(engines, (list, tuple)) or not engines:
                raise ExecutionPolicyError(f"{op} {q}: needs a non-empty list of engines")
            order: list[str] = []
            for engine in engines:
                e = str(engine).strip().lower()
                if e not in ENGINES:
                    raise ExecutionPolicyError(f"{op} {q}: unknown engine {engine!r} (known: {', '.join(ENGINES)})")
                if e in order:
                    raise ExecutionPolicyError(f"{op} {q}: engine {e!r} is listed twice")
                order.append(e)
            table[q] = order
        if table:
            out[op] = table
    return out


def stored_policy(doc: object) -> dict[str, dict[str, list[str]]]:
    """A document read back from the database, validated; one that no longer
    validates (an operation or engine since removed) reads as empty, so a stale row
    falls back to the defaults instead of failing every request."""
    try:
        return validate_policy(doc)
    except ExecutionPolicyError as exc:
        logger.warning("ignoring a stored execution policy that does not validate: %s", exc)
        return {}


def merge_policy(base: Mapping, override: Mapping | None) -> dict[str, dict[str, list[str]]]:
    """``base`` with each ``(operation, qualifier)`` row of ``override`` replacing its own."""
    out = {op: {q: list(e) for q, e in rows.items()} for op, rows in base.items()}
    for op, rows in (override or {}).items():
        table = out.setdefault(op, {})
        for q, engines in rows.items():
            table[q] = list(engines)
    return out


def complete_policy(doc: Mapping | None) -> dict[str, dict[str, list[str]]]:
    """A stored global document made whole: an operation it does not name keeps its
    default table, and an operation without a ``"*"`` row gets the default one, so
    every lookup has an answer. Qualifier rows of a named operation are the
    document's own (an administrator can remove a default ``.ifc`` row)."""
    out = default_policy()
    for op, rows in (doc or {}).items():
        table = {q: list(e) for q, e in rows.items()}
        table.setdefault(WILDCARD, list(DEFAULT_POLICY[op][WILDCARD]))
        out[op] = table
    return out


def effective_policy(global_doc: Mapping | None, override: Mapping | None) -> dict[str, dict[str, list[str]]]:
    """The policy one user runs under: the completed global document, overlaid per
    row by that user's override."""
    return merge_policy(complete_policy(global_doc), override)


def lookup(policy: Mapping, operation: str, qualifier: str | None) -> list[str]:
    """The engine order for ``(operation, qualifier)``, else ``(operation, "*")``."""
    rows = policy.get(operation) or {}
    q = (qualifier or WILDCARD).strip().lower() or WILDCARD
    if q in rows:
        return list(rows[q])
    return list(rows.get(WILDCARD) or [])


def extension_of(key: str | None) -> str:
    """Lower-case suffix of a storage key including the dot, or ``"*"``."""
    suffix = pathlib.PurePosixPath(str(key or "")).suffix.lower()
    return suffix or WILDCARD


# A conversion is either an explicit one (converter page, re-convert) or a source
# loaded for viewing; the job looks the same, so the viewer says which it is.
OPERATION_HEADER = "x-ada-operation"

_requested_operation: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ada_requested_operation", default=None
)


def requested_operation(headers) -> str | None:
    """The ``X-Ada-Operation`` header when it names a known operation."""
    op = str(headers.get(OPERATION_HEADER, "")).strip().lower()
    return op if op in OPERATIONS else None


def set_requested_operation(value: str | None) -> contextvars.Token:
    return _requested_operation.set(value)


def reset_requested_operation(token: contextvars.Token) -> None:
    _requested_operation.reset(token)


def current_requested_operation() -> str | None:
    return _requested_operation.get()


def job_operation(
    feature: str,
    *,
    source_key: str | None = None,
    conversion_options: Mapping | None = None,
    plugin_id: str | None = None,
    requested: str | None = None,
) -> tuple[str, str] | None:
    """Map a job submission to its ``(operation, qualifier)``, or None when it is no
    operation the policy covers (an export, an asset build, ...)."""
    opts = conversion_options or {}
    if feature == "conversion":
        op = "read" if requested == "read" else "convert"
        return op, extension_of(source_key)
    if feature == "bake":
        return "fea_bake", extension_of(source_key)
    if feature == "procedural_build":
        engine = str(opts.get("engine") or "").strip().lower()
        return "procedural_compile", engine or WILDCARD
    if feature in PROCEDURAL_IO_FEATURES:
        engine = str(opts.get("engine") or "").strip().lower()
        return "procedural_io", engine or WILDCARD
    if feature in CORE_JOB_FAMILIES:
        return "core_job", CORE_JOB_FAMILIES[feature]
    if feature == "plugin_jobs":
        pid = str(plugin_id or opts.get("plugin_id") or "").strip().lower()
        return "plugin_job", pid or WILDCARD
    if feature == "asset_build":
        capability = str(opts.get("capability") or "").strip().lower()
        return "asset_build", capability or WILDCARD
    return None


def worker_permitted(policy: Mapping, operation: str, qualifier: str | None) -> bool:
    return "worker" in lookup(policy, operation, qualifier)


def browser_permitted(policy: Mapping, operation: str, qualifier: str | None) -> bool:
    return "pyodide" in lookup(policy, operation, qualifier)


async def load_effective_policy(pool, user_sub: str | None) -> dict[str, dict[str, list[str]]]:
    """The effective policy for ``user_sub``; the defaults without a database."""
    if pool is None:
        return default_policy()
    global_row = await db_module.get_execution_policy(pool)
    override_row = await db_module.get_user_execution_policy(pool, user_sub) if user_sub else None
    return effective_policy(
        stored_policy(global_row["policy"]) if global_row else None,
        stored_policy(override_row["policy"]) if override_row else None,
    )


async def require_execution_policy(
    pool,
    user,
    *,
    explicit: bool,
    feature: str,
    source_key: str | None = None,
    conversion_options: Mapping | None = None,
    plugin_id: str | None = None,
    requested: str | None = None,
) -> None:
    """Refuse (403) an explicit worker choice the effective policy does not allow.

    Judged only when the submission declared it chose the worker over the browser
    (``explicit``) and has a user; an operation the policy does not cover passes.
    A policy that cannot be read is treated as the defaults (which allow the worker
    everywhere), so a database hiccup never blocks a job the defaults allow.
    """
    if not explicit or user is None:
        return
    key = job_operation(
        feature,
        source_key=source_key,
        conversion_options=conversion_options,
        plugin_id=plugin_id,
        requested=requested,
    )
    if key is None:
        return
    op, qualifier = key
    try:
        policy = await load_effective_policy(pool, getattr(user, "sub", None))
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("execution policy unreadable, using the defaults: %s", exc)
        policy = default_policy()
    if worker_permitted(policy, op, qualifier):
        return
    allowed = lookup(policy, op, qualifier)
    raise HTTPException(
        status_code=403,
        detail=(
            f"the execution policy does not allow the server worker pool for {op} ({qualifier}); "
            f"allowed engines: {', '.join(allowed) or 'none'}. "
            "Ask an administrator to change it in the Admin Panel (Execution)."
        ),
    )


async def require_browser_asset_build(pool, user, capability: str | None) -> None:
    """Refuse (403) storing a browser-built asset when the effective policy does not list
    ``pyodide`` for ``asset_build`` and ``capability`` (see the module docstring: a browser
    build lands in shared storage, so this part of the policy is the server's to judge).

    Like :func:`require_execution_policy`, a policy that cannot be read is the defaults
    (which list the browser), and a request without a user is judged on the defaults."""
    try:
        policy = await load_effective_policy(pool, getattr(user, "sub", None) if user is not None else None)
    except Exception as exc:  # noqa: BLE001 - see require_execution_policy
        logger.warning("execution policy unreadable, using the defaults: %s", exc)
        policy = default_policy()
    qualifier = str(capability or "").strip().lower() or WILDCARD
    if browser_permitted(policy, "asset_build", qualifier):
        return
    allowed = lookup(policy, "asset_build", qualifier)
    raise HTTPException(
        status_code=403,
        detail=(
            f"the execution policy does not allow building {qualifier!r} assets in the browser; "
            f"allowed engines: {', '.join(allowed) or 'none'}. Build it on the worker, or ask an "
            "administrator to change it in the Admin Panel (Execution)."
        ),
    )
