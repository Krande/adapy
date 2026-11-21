"""The rows each producing document gives (``ada.assoc.records``): a clash run's contacts, a detail
model's connections and welds, a subject's members and a simulation run's.

Pure functions of the documents, so writing them again (a reindex) gives the same rows.

STDLIB ONLY.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from ada.clash.sidecar import target_token

from .keys import connection_key, file_key
from .records import LinkRow, contact_group, params_hash
from .resolve import ELEMENTS_PROVENANCE, member_identities

__all__ = [
    "SUBJECTS_PREFIX",
    "Member",
    "adopted_subject_rows",
    "classic_rows",
    "ck_of_subject_key",
    "connection_ck",
    "connection_members",
    "contact_rows",
    "detail_rows",
    "member_identities_of",
    "sim_run_rows",
    "subject_key",
    "subject_rows",
]

#: Where the subject made of one connection lives: ``_clash/subjects/<ck>.weld-assembly.json``.
SUBJECTS_PREFIX = "_clash/subjects/"
_SUBJECT_KEY = re.compile(r"^_clash/subjects/([0-9a-f]{16})\.([a-z0-9-]+)\.json$")


def subject_key(ck: str, kind: str = "weld-assembly") -> str:
    if not re.fullmatch(r"[0-9a-f]{16}", str(ck or "")):
        raise ValueError(f"{ck!r} is not a connection key")
    return f"{SUBJECTS_PREFIX}{ck}.{kind}.json"


def ck_of_subject_key(key: str) -> str | None:
    """The connection key a subject's storage key names, or None for a key not under
    :data:`SUBJECTS_PREFIX`."""
    m = _SUBJECT_KEY.match(str(key or "").lstrip("/"))
    return m.group(1) if m else None


#: ``(role, element key, ancestors, fingerprint, source)``.
Member = tuple[str, str, Sequence[str], "str | None", "str | None"]


def member_identities_of(run) -> list[dict]:
    """A run's members' identities: the ones its annotate resolved (``provenance.elements``), else by
    name alone."""
    stored = (run.provenance or {}).get(ELEMENTS_PROVENANCE)
    if isinstance(stored, list) and len(stored) == len(run.members):
        return [dict(e) if isinstance(e, Mapping) else {"key": None, "anc": []} for e in stored]
    return member_identities(run)


def _source_of(run, member: Mapping[str, Any]) -> str:
    if run.target.get("kind") == "group":
        parts = run.parts
        k = int(member.get("part", 0))
        if 0 <= k < len(parts):
            return target_token(dict(parts[k]["source"]))
    return target_token(dict(run.target))


def contact_rows(run, run_key: str, identities: Sequence[Mapping[str, Any]] | None = None) -> list[LinkRow]:
    """Two rows per clash of ``run`` (an ``ada.clash.sidecar.ContactRun``), one per member."""
    ids = list(identities) if identities is not None else member_identities_of(run)
    out: list[LinkRow] = []
    geometry_sha = run.geometry.get("sha256")
    for c in run.contacts:
        i, j = c.members
        a, b = ids[i] if i < len(ids) else {}, ids[j] if j < len(ids) else {}
        if not a.get("key") or not b.get("key"):
            continue
        group = contact_group(a["key"], b["key"])
        applicable = [str(s.get("name") or s.get("spec") or "") for s in c.applicable if isinstance(s, Mapping)]
        for me, other, mine, theirs in ((i, j, a, b), (j, i, b, a)):
            role = None
            if c.roles is not None:
                role = "incoming" if c.roles["incoming"] == me else "landing"
            out.append(
                LinkRow(
                    kind="contact",
                    element_key=mine["key"],
                    doc_key=run_key,
                    anc=tuple(mine.get("anc") or ()),
                    group=group,
                    source=_source_of(run, run.members[me]),
                    geometry_sha256=geometry_sha,
                    fp=mine.get("fp"),
                    created_at=run.created_at,
                    data={
                        "run_key": run_key,
                        "run_id": run.run_id,
                        "contact_id": c.id,
                        "other_key": theirs["key"],
                        "name": str(run.members[me].get("name") or ""),
                        "other_name": str(run.members[other].get("name") or ""),
                        "role": role,
                        "area": c.contact_area,
                        "distance": c.distance,
                        "n_patches": len(c.patches),
                        "applicable": [s for s in applicable if s],
                        "centre": [round(v, 6) for v in c.centre],
                    },
                )
            )
    return out


def _fallback_identity(record: Mapping[str, Any], name: str) -> dict:
    """A member known by name only (a classic clash result's detail): its file's name-path key."""
    source = record.get("source") or {}
    target = source.get("target") or {}
    source_key = target.get("source_key") or source.get("source_key")
    if source_key:
        key = file_key(source_key, path=[name])
        return {"key": key, "anc": [file_key(source_key)]}
    return {"key": None, "anc": []}


def connection_members(
    member_roles: Mapping[str, Sequence[str]],
    *,
    run=None,
    contact=None,
    identities: Sequence[Mapping[str, Any]] | None = None,
    record: Mapping[str, Any] | None = None,
) -> list[Member]:
    """A connection's members as ``(role, key, anc, fp)``: each name bound to the run member of that
    name -- one of the clash's own two first, so a name that repeats in the model binds to the member
    at this joint."""
    ids = list(identities) if identities is not None else (member_identities_of(run) if run is not None else [])
    order: list[int] = list(contact.members) if contact is not None else []
    if run is not None:
        order += [k for k in range(len(run.members)) if k not in order]
    out: list[Member] = []
    for role, names in member_roles.items():
        for name in names:
            hit = next((k for k in order if str(run.members[k].get("name")) == str(name)), None) if run else None
            ident = ids[hit] if hit is not None and hit < len(ids) else _fallback_identity(record or {}, str(name))
            if ident.get("key"):
                out.append(
                    (str(role), str(ident["key"]), tuple(ident.get("anc") or ()), ident.get("fp"), ident.get("source"))
                )
    return out


def _contact_members(run, contact, identities) -> list[Member]:
    """A clash's two members with the roles its annotate decided (``member`` where it did not)."""
    out: list[Member] = []
    for k in contact.members:
        ident = identities[k] if k < len(identities) else {}
        if not ident.get("key"):
            continue
        role = "member"
        if contact.roles is not None:
            role = "incoming" if contact.roles["incoming"] == k else "landing"
        out.append((role, str(ident["key"]), tuple(ident.get("anc") or ()), ident.get("fp"), ident.get("source")))
    return out


def connection_ck(
    conn: Mapping[str, Any], *, run=None, identities=None, record: Mapping[str, Any] | None = None
) -> str | None:
    """The ck of one ``stats["connections"]`` record (``ada.clash.detail.connection_records``): its
    spec, its members' element keys by role, and the clash's centre when it was detailed from a run
    (the connection's own centre otherwise). None when no member is known."""
    jid = str(conn.get("joint_id") or "")
    contact = run.contact(jid) if run is not None else None
    ids = identities if identities is not None else (member_identities_of(run) if run is not None else [])
    members = connection_members(
        conn.get("member_roles") or {}, run=run, contact=contact, identities=ids, record=record
    )
    if not members:
        return None
    spec = str(conn.get("spec") or (record or {}).get("spec") or "")
    centre = list(contact.centre) if contact is not None else conn.get("centre")
    return connection_key(spec, [(m[0], m[1]) for m in members], centre)


def detail_rows(
    record: Mapping[str, Any],
    stats: Mapping[str, Any],
    *,
    record_key: str,
    run=None,
) -> list[LinkRow]:
    """A settled detail model's ``connection`` and ``weld`` rows: one per member of every connection
    it built (``status: built``) or could not (``not_built`` / ``failed``), one per member of every
    weld, kept or taken out as a duplicate (``kept: false``, ``dup_of``).

    ``run`` is the clash run it was detailed from (``record["source"]["run_key"]``), which binds each
    member name to its element and gives each connection the clash's centre -- the ck is then the same
    whichever detail run built the connection."""
    detail_id = str(record.get("detail_id") or "")
    created = record.get("created_at")
    ids = member_identities_of(run) if run is not None else []
    rows: list[LinkRow] = []
    by_conn: dict[tuple[str, str], tuple[str, list[Member]]] = {}

    def _row(kind, member, ck, data):
        role, key, anc, fp, source = member
        rows.append(
            LinkRow(
                kind=kind,
                element_key=key,
                doc_key=record_key,
                anc=tuple(anc),
                ck=ck,
                source=source,
                fp=fp,
                created_at=created,
                data={"detail_id": detail_id, "requested_spec": record.get("spec"), **data},
            )
        )

    for conn in stats.get("connections") or ():
        if not isinstance(conn, Mapping):
            continue
        jid = str(conn.get("joint_id") or "")
        contact = run.contact(jid) if run is not None else None
        members = connection_members(
            conn.get("member_roles") or {}, run=run, contact=contact, identities=ids, record=record
        )
        if not members:
            continue
        spec = str(conn.get("spec") or record.get("spec") or "")
        centre = list(contact.centre) if contact is not None else conn.get("centre")
        ck = connection_key(spec, [(m[0], m[1]) for m in members], centre)
        by_conn[(jid, str(conn.get("connection") or ""))] = (ck, members)
        for m in members:
            _row(
                "connection",
                m,
                ck,
                {
                    "joint_id": jid,
                    "connection": str(conn.get("connection") or ""),
                    "spec": spec,
                    "role": m[0],
                    "centre": centre,
                    "status": "built",
                    "members": [m[1] for m in members],
                },
            )
        member_keys = {m[1]: m for m in members}
        for weld in conn.get("welds") or ():
            names = [str(n) for n in weld.get("members") or ()]
            # A weld's members as the connection's (their roles), the connection's all when it names none.
            joined = [
                member_keys[m[1]]
                for m in connection_members({"weld": names}, run=run, contact=contact, identities=ids, record=record)
                if m[1] in member_keys
            ] or members
            for m in joined:
                _row(
                    "weld",
                    m,
                    ck,
                    {
                        "weld_name": str(weld.get("name") or ""),
                        "weld_type": str(weld.get("weld_type") or ""),
                        "length": weld.get("length"),
                        "members": names,
                        "kept": True,
                        "dup_of": None,
                    },
                )

    for dup in stats.get("duplicate_welds") or ():
        if not isinstance(dup, Mapping):
            continue
        found = by_conn.get((str(dup.get("joint_id") or ""), str(dup.get("connection") or "")))
        if found is None:
            continue
        ck, members = found
        for m in members:
            _row(
                "weld",
                m,
                ck,
                {
                    "weld_name": str(dup.get("weld") or ""),
                    "weld_type": str(dup.get("weld_type") or ""),
                    "length": None,
                    "members": [],
                    "kept": False,
                    "dup_of": str(dup.get("kept_weld") or "") or None,
                },
            )

    built = {jid for jid, _ in by_conn}
    for miss in stats.get("not_built") or ():
        if not isinstance(miss, Mapping) or run is None:
            continue
        jid = str(miss.get("joint_id") or "")
        contact = run.contact(jid)
        if contact is None or jid in built:
            continue
        members = _contact_members(run, contact, ids)
        if not members:
            continue
        spec = str(miss.get("spec") or record.get("spec") or "")
        centre = list(contact.centre)
        ck = connection_key(spec, [(m[0], m[1]) for m in members], centre)
        status = "failed" if miss.get("kind") == "failed" else "not_built"
        for m in members:
            _row(
                "connection",
                m,
                ck,
                {
                    "joint_id": jid,
                    "connection": "",
                    "spec": spec,
                    "role": m[0],
                    "centre": centre,
                    "status": status,
                    "reason": str(miss.get("reason") or ""),
                    "members": [m[1] for m in members],
                },
            )
    return rows


def classic_rows(result, result_key: str) -> list[LinkRow]:
    """A classic clash check's joints (``ada.clash.result``) as ``contact`` rows: one per member per
    other member of each joint. Its members are known by guid (a file that persists them) or name."""
    source_key = str(getattr(result, "source_key", "") or "")
    if not source_key:
        return []
    created = (getattr(result, "provenance", None) or {}).get("created_at")
    out: list[LinkRow] = []
    for joint in result.joints:
        keys = [file_key(source_key, guid=m.guid, path=None if m.guid else [m.name]) for m in joint.members]
        names = [m.name for m in joint.members]
        for i, key in enumerate(keys):
            for j, other in enumerate(keys):
                if i == j or key == other:
                    continue
                out.append(
                    LinkRow(
                        kind="contact",
                        element_key=key,
                        doc_key=result_key,
                        anc=(file_key(source_key),),
                        group=contact_group(key, other),
                        created_at=created,
                        data={
                            "result_key": result_key,
                            "joint_id": joint.id,
                            "contact_id": joint.id,
                            "other_key": other,
                            "name": names[i],
                            "other_name": names[j],
                            "role": joint.members[i].role,
                            "type_label": joint.type_label,
                            "applicable": [a.spec for a in joint.applicable],
                            "centre": list(joint.centre),
                            "classic": True,
                        },
                    )
                )
    return out


def adopted_subject_rows(doc: Mapping[str, Any], doc_key: str) -> list[LinkRow]:
    """The rows of a subject index another tool kept (``ada.assoc/subject-index@1``)::

        {"schema": "ada.assoc/subject-index@1",
         "subjects": [{"key": <subject storage key>, "kind"?: "weld-assembly", "ck"?: <16 hex>,
                       "elements": [{"key": <element key>, "role"?: ..., "anc"?: [...]}]}]}

    A subject without a ck gets one of its elements (spec ``adopted``, no centre)."""
    out: list[LinkRow] = []
    for s in doc.get("subjects") or ():
        if not isinstance(s, Mapping) or not s.get("key"):
            continue
        elements = [e for e in s.get("elements") or () if isinstance(e, Mapping) and e.get("key")]
        if not elements:
            continue
        members = [
            (str(e.get("role") or "member"), str(e["key"]), tuple(e.get("anc") or ()), None, None) for e in elements
        ]
        ck = str(s.get("ck") or "") or connection_key("adopted", [(m[0], m[1]) for m in members], None)
        for row in subject_rows(
            str(s["key"]),
            ck,
            members,
            subject_kind=str(s.get("kind") or "weld-assembly"),
            created_at=s.get("created_at"),
        ):
            row.doc_key = doc_key
            row.data["adopted"] = True
            out.append(row)
    return out


def subject_rows(
    subject_key_: str,
    ck: str,
    members: Iterable[Member],
    *,
    subject_kind: str = "weld-assembly",
    detail_id: str | None = None,
    created_at: str | None = None,
) -> list[LinkRow]:
    """One ``subject`` row per member of the connection a subject file was made of."""
    return [
        LinkRow(
            kind="subject",
            element_key=key,
            doc_key=subject_key_,
            anc=tuple(anc),
            ck=ck,
            source=source,
            fp=fp,
            created_at=created_at,
            data={"subject_kind": subject_kind, "subject_key": subject_key_, "detail_id": detail_id, "role": role},
        )
        for role, key, anc, fp, source in members
    ]


def sim_run_rows(record: Mapping[str, Any], ck: str, members: Iterable[Member], *, record_key: str) -> list[LinkRow]:
    """One ``sim_run`` row per member of the connection a simulation run is on."""
    subject = record.get("subject") or {}
    batch = record.get("batch") if isinstance(record.get("batch"), Mapping) else None
    provider = record.get("provider") or {}
    started_by = record.get("started_by") or {}
    data = {
        "subject_key": subject.get("key"),
        "run_id": record.get("run_id"),
        "provider": provider.get("id"),
        "domain": provider.get("domain"),
        "params_hash": params_hash(record.get("parameters")),
        "status": record.get("status"),
        "started_by": started_by.get("display") or started_by.get("id"),
        "finished_at": record.get("finished_at"),
        "batch": {"id": batch.get("id"), "label": batch.get("label")} if batch else None,
    }
    return [
        LinkRow(
            kind="sim_run",
            element_key=key,
            doc_key=record_key,
            anc=tuple(anc),
            ck=ck,
            source=source,
            fp=fp,
            created_at=record.get("created_at"),
            data={**data, "role": role},
        )
        for role, key, anc, fp, source in members
    ]
