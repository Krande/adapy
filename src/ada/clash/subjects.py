"""Simulation subjects made of the connections a detail model built: one per connection, at
``_clash/subjects/<ck>.weld-assembly.json`` (``ada.assoc.rows.subject_key``).

A subject is what a simulation run is started ON (``ada.simulation.subjects``). Made of a detail
model's connection, it is keyed by the connection's ck -- its spec, its members by element and its
centre -- so the same connection detailed again names the same subject, and "has this connection
been simulated" is one lookup however many detail runs built it.

CONVERTERS. The default is :func:`ada.simulation.subjects.weld_assembly_from_part` of the built
connection. A provider whose connections need another reading (members it keeps outside the
connection, a frame of its own) registers one for its capability::

    register_subject_converter("my-capability", lambda conn, *, found, spec: {...weld assembly...})

The converter returns the assembly document (``ada-weld-assembly/1`` or ``/2``); ``source`` is
filled in here.

Runs on a worker: building a connection needs the modelling stack.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

__all__ = [
    "SubjectConverter",
    "register_subject_converter",
    "subject_converter",
    "unregister_subject_converter",
    "weld_assemblies_for_detail",
    "weld_assembly_for_connection",
]

#: ``fn(conn, *, found, spec) -> weld assembly dict``.
SubjectConverter = Callable[..., Mapping[str, Any]]

_CONVERTERS: dict[str | None, SubjectConverter] = {}


def register_subject_converter(capability: str | None, fn: SubjectConverter) -> None:
    """Make ``fn`` the converter for the connections of ``capability``'s specs (None: every spec
    without a converter of its own)."""
    _CONVERTERS[capability] = fn


def unregister_subject_converter(capability: str | None) -> None:
    _CONVERTERS.pop(capability, None)


def _default_converter(conn, *, found=None, spec=None) -> Mapping[str, Any]:
    from ada.simulation.subjects import weld_assembly_from_part

    return weld_assembly_from_part(conn)


def subject_converter(capability: str | None) -> SubjectConverter:
    return _CONVERTERS.get(capability) or _CONVERTERS.get(None) or _default_converter


def weld_assembly_for_connection(
    conn, *, capability: str | None = None, found=None, spec: str | None = None, source: Mapping | None = None
) -> dict:
    """The weld assembly of one built connection, by its capability's converter, with ``source``."""
    doc = dict(subject_converter(capability)(conn, found=found, spec=spec))
    if source:
        doc["source"] = {**dict(doc.get("source") or {}), **dict(source)}
    return doc


def _registered(spec_name: str):
    from ada.api.connections.spec import get_registered
    from ada.clash.builtin_specs import register_builtin_specs

    register_builtin_specs()
    return get_registered(spec_name)


def weld_assemblies_for_detail(
    record: Mapping[str, Any],
    stats: Mapping[str, Any],
    storage,
    *,
    record_key: str,
    run=None,
    cks: Iterable[str] | None = None,
    built: list | None = None,
    write: bool = True,
) -> dict:
    """Write the subject of every built connection of a detail model (``record`` and its take-off
    ``stats``), or of those whose ck is in ``cks``. Returns ``{"subjects": [{ck, key, kind,
    detail_id, joint_id, members}], "errors": [{ck, joint_id, error}]}``; ``members`` are
    ``ada.assoc.rows.Member`` tuples, for the caller to file the ``subject`` rows.

    ``built``: the ``(joint id, connection)`` pairs the detail run built (``build_detail``'s
    ``keep_built``) -- the subjects are then made of those objects. Without it the run's members are
    rebuilt (``ada.clash.rebuild``) and each joint is built again (``build_joint``), as the detail
    run built it. ``storage``: a job's synchronous, scope-bound facade."""
    from ada.assoc.rows import detail_rows, subject_key
    from ada.assoc.store import utc_now
    from ada.clash.sidecar import parse_run
    from ada.simulation.subjects import WELD_ASSEMBLY_KIND, write_weld_assembly

    source = record.get("source") or {}
    if run is None and source.get("run_key"):
        run = parse_run(storage.get_bytes(str(source["run_key"])))
    rows = [r for r in detail_rows(record, stats, record_key=record_key, run=run) if r.kind == "connection"]
    wanted = set(cks) if cks is not None else None
    by_ck: dict[str, dict] = {}
    for r in rows:
        if r.data.get("status") != "built" or not r.ck or (wanted is not None and r.ck not in wanted):
            continue
        entry = by_ck.setdefault(
            r.ck,
            {
                "ck": r.ck,
                "joint_id": r.data.get("joint_id"),
                "connection": r.data.get("connection"),
                "spec": r.data.get("spec"),
                "members": [],
            },
        )
        entry["members"].append((r.data.get("role"), r.element_key, r.anc, r.fp, r.source))

    out: dict[str, list] = {"subjects": [], "errors": []}
    if not by_ck:
        return out

    objects: dict[tuple[str, str], Any] = {}
    founds: dict[str, Any] = {}
    if built is not None:
        objects = {(str(jid), str(getattr(conn, "name", ""))): conn for jid, conn in built}
    else:
        if run is None:
            raise ValueError("a detail model made from a classic clash result is rebuilt by its own job")
        from ada.clash.contacts import founds_by_id
        from ada.clash.detail import JointNotBuilt, build_joint
        from ada.clash.rebuild import check_geometry_unchanged, rebuild_run_members

        check_geometry_unchanged(run, storage)
        model, _ = rebuild_run_members(run, storage, source.get("geometry_provider") or None)
        jids = sorted({str(e["joint_id"]) for e in by_ck.values()})
        founds, problems = founds_by_id(run, model, jids)
        for e in by_ck.values():
            if e["joint_id"] in problems:
                out["errors"].append({"ck": e["ck"], "joint_id": e["joint_id"], "error": problems[e["joint_id"]]})
        for jid in [j for j in jids if j in founds]:
            # The spec the detail run was asked for: a builder may name its connections' spec otherwise.
            spec = str(record.get("spec") or next(e["spec"] for e in by_ck.values() if e["joint_id"] == jid))
            try:
                _, conns = build_joint(
                    _registered(spec), founds[jid], joint_id=jid, gen_options=record.get("options") or {}
                )
            except (JointNotBuilt, KeyError) as exc:
                for e in by_ck.values():
                    if e["joint_id"] == jid:
                        out["errors"].append({"ck": e["ck"], "joint_id": jid, "error": str(exc)})
                continue
            for conn in conns:
                objects[(jid, str(conn.name))] = conn

    for e in by_ck.values():
        if any(err["ck"] == e["ck"] for err in out["errors"]):
            continue
        conn = objects.get((str(e["joint_id"]), str(e["connection"])))
        if conn is None:
            out["errors"].append({"ck": e["ck"], "joint_id": e["joint_id"], "error": "the connection was not rebuilt"})
            continue
        try:
            capability = getattr(_registered(str(record.get("spec") or e["spec"])).spec, "capability", None)
        except KeyError:
            capability = None
        try:
            doc = weld_assembly_for_connection(
                conn,
                capability=capability,
                found=founds.get(str(e["joint_id"])),
                spec=e["spec"],
                source={
                    "kind": "detailing",
                    "spec_name": e["spec"],
                    "model": record.get("detail_id"),
                    "joint": e["joint_id"],
                    "ck": e["ck"],
                    "elements": [{"role": m[0], "key": m[1]} for m in e["members"]],
                },
            )
            key = subject_key(e["ck"], WELD_ASSEMBLY_KIND)
            if write:
                write_weld_assembly(storage, None, key, doc)
        except Exception as exc:  # noqa: BLE001 - one connection that makes no subject is not all of them
            out["errors"].append({"ck": e["ck"], "joint_id": e["joint_id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        out["subjects"].append(
            {
                "ck": e["ck"],
                "key": key,
                "kind": WELD_ASSEMBLY_KIND,
                "detail_id": record.get("detail_id"),
                "joint_id": e["joint_id"],
                "members": e["members"],
                "created_at": utc_now(),
                **({} if write else {"doc": doc}),
            }
        )
    return out
