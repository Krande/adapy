"""Where the equipment-type and system-template catalogs are kept: postgres when there is
one, storage files when not.

Both catalogs are postgres tables (migrations 023/024, db/catalogs.py): per scope, one
row per entry with a scope-unique slug, a ``doc``, a revision under optimistic
concurrency and an ``archived`` flag. A LOCAL viewer (no ``DATABASE_URL``, no queue) has
no database, so its Builder had no equipment catalog and "Add equipment" offered nothing.

:class:`FileCatalogStore` keeps the same records as JSON files in the scope's storage::

    _catalogs/equipment-types/<id>.json   {id, scope_kind, scope_id, slug, name,
                                           description, doc, cad_key, revision,
                                           created_by, created_at, updated_at, archived}
    _catalogs/system-templates/<id>.json  the same without cad_key

``_catalogs/`` is a hidden prefix. Archiving sets the flag and keeps the file, as the
postgres store keeps the row. A scope whose file catalog has no entry at all is seeded
with the defaults when it is first listed, read for a build or added to
(:func:`default_equipment_types`, :func:`default_system_templates`): a plain box and the
built-in archetypes, the built-in system kinds. Archiving every entry does not bring
them back.

:func:`catalog_store` picks the store like :func:`..procedural_store.procedural_store`:
postgres with a pool; files with neither a pool nor a queue (one process, so the
process-wide lock makes the slug check and the revision bump atomic); else ``None``, and
the routes answer 503 as before.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import uuid
from typing import Any, Literal, Protocol

from ada.config import logger

from .scope import Scope
from .storage import Storage

CATALOG_PREFIX = "_catalogs/"
EQUIPMENT_DIR = f"{CATALOG_PREFIX}equipment-types/"
SYSTEM_DIR = f"{CATALOG_PREFIX}system-templates/"

CatalogKind = Literal["equipment", "system"]

#: The slug of the seeded plain-box type: no ports, the placed cell's own extents.
BOX_SLUG = "box"


class CatalogSlugTaken(Exception):
    """An update would give an entry a slug another live entry in the scope has."""


def equipment_type_key(type_id: str) -> str:
    return f"{EQUIPMENT_DIR}{type_id}.json"


def system_template_key(template_id: str) -> str:
    return f"{SYSTEM_DIR}{template_id}.json"


def _default_doc(kind: CatalogKind) -> dict:
    """What a new entry's doc is: the validator's defaults, as the postgres column
    defaults (migrations 023/024) are."""
    from .catalog import validate_equipment_doc, validate_system_doc

    return validate_equipment_doc({}) if kind == "equipment" else validate_system_doc({})


def default_equipment_types() -> list[dict]:
    """``{slug, name, description, doc}`` for a new scope's equipment catalog: a plain box
    (the validator's default doc: 1 m cube, 1000 kg, ``IfcBuildingElementProxy``, no
    ports - a placed box keeps its own extents) and every built-in archetype of
    ``ada.topo_model.equipment`` (pump, tank, ...), with the doc a worker advertises."""
    from .catalog import validate_equipment_doc

    out = [
        {
            "slug": BOX_SLUG,
            "name": "Box",
            "description": "A plain box: the placed size, a mass, no ports.",
            "doc": _default_doc("equipment"),
        }
    ]
    try:
        from ada.topo_model.equipment import equipment_archetype_specs

        specs = equipment_archetype_specs()
    except Exception:  # noqa: BLE001 -- a slim install without the modelling stack: the box only
        logger.exception("catalog store: no built-in equipment archetypes to seed")
        specs = []
    for spec in specs:
        try:
            doc = validate_equipment_doc(spec["doc"])
        except (KeyError, ValueError):
            continue
        out.append(
            {
                "slug": spec["slug"],
                "name": spec.get("name") or spec["slug"],
                "description": "Synced from built-in archetype",
                "doc": doc,
            }
        )
    return out


def default_system_templates() -> list[dict]:
    """The built-in system kinds (piping, duct, cable, electrical) as system templates."""
    from .catalog import builtin_system_specs, validate_system_doc

    return [
        {
            "slug": s["slug"],
            "name": s["name"],
            "description": "Synced from built-in system kind",
            "doc": validate_system_doc(s["doc"]),
        }
        for s in builtin_system_specs()
    ]


class CatalogStore(Protocol):
    """The operations the catalog routes need, with db/catalogs.py's return shapes.

    ``get_*`` returns the full row (with ``scope_kind``/``scope_id``/``doc``) or ``None``
    when the entry is absent, archived, malformed or in another scope. ``update_*``
    returns the new revision, ``None`` on a revision conflict, and raises
    :class:`CatalogSlugTaken` on a slug collision."""

    async def list_equipment(self, scope: Scope) -> list[dict]: ...
    async def equipment_docs(self, scope: Scope) -> dict[str, dict]: ...
    async def equipment_cad_keys(self, scope: Scope) -> dict[str, str]: ...
    async def get_equipment(self, scope: Scope, type_id: str) -> dict | None: ...
    async def create_equipment(
        self, scope: Scope, *, slug: str, name: str, description: str | None, created_by: str | None
    ) -> dict | None: ...
    async def update_equipment(
        self,
        scope: Scope,
        type_id: str,
        *,
        slug: str,
        name: str,
        description: str | None,
        doc: dict,
        base_revision: int,
    ) -> int | None: ...
    async def set_equipment_cad(self, scope: Scope, type_id: str, cad_key: str | None) -> bool: ...
    async def apply_inferred_bbox(self, scope: Scope, type_id: str, bbox: dict) -> bool: ...
    async def archive_equipment(self, scope: Scope, type_id: str) -> bool: ...
    async def list_systems(self, scope: Scope) -> list[dict]: ...
    async def system_docs(self, scope: Scope) -> dict[str, dict]: ...
    async def get_system(self, scope: Scope, template_id: str) -> dict | None: ...
    async def create_system(
        self, scope: Scope, *, slug: str, name: str, description: str | None, created_by: str | None
    ) -> dict | None: ...
    async def update_system(
        self,
        scope: Scope,
        template_id: str,
        *,
        slug: str,
        name: str,
        description: str | None,
        doc: dict,
        base_revision: int,
    ) -> int | None: ...
    async def archive_system(self, scope: Scope, template_id: str) -> bool: ...
    async def fingerprint(self, scope: Scope) -> str: ...


def _in_scope(row: dict, scope: Scope) -> bool:
    return row.get("scope_kind") == scope.kind and (row.get("scope_id") or None) == (scope.id or None)


class DbCatalogStore:
    """Postgres: db/catalogs.py, unchanged."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @staticmethod
    def _db():
        from . import db as db_module

        return db_module

    async def list_equipment(self, scope: Scope) -> list[dict]:
        return await self._db().list_equipment_types(self.pool, scope_kind=scope.kind, scope_id=scope.id)

    async def equipment_docs(self, scope: Scope) -> dict[str, dict]:
        return await self._db().get_equipment_docs_by_scope(self.pool, scope_kind=scope.kind, scope_id=scope.id)

    async def equipment_cad_keys(self, scope: Scope) -> dict[str, str]:
        return await self._db().get_equipment_cad_keys_by_scope(self.pool, scope_kind=scope.kind, scope_id=scope.id)

    async def get_equipment(self, scope: Scope, type_id: str) -> dict | None:
        try:
            row = await self._db().get_equipment_type(self.pool, type_id)
        except Exception:
            return None  # malformed UUID etc.: not found, not a 500
        return row if row is not None and _in_scope(row, scope) else None

    async def create_equipment(self, scope: Scope, *, slug, name, description, created_by) -> dict | None:
        return await self._db().create_equipment_type(
            self.pool,
            scope_kind=scope.kind,
            scope_id=scope.id,
            slug=slug,
            name=name,
            description=description,
            created_by=created_by,
        )

    async def update_equipment(
        self, scope: Scope, type_id: str, *, slug, name, description, doc, base_revision
    ) -> int | None:
        import asyncpg

        try:
            return await self._db().update_equipment_type(
                self.pool,
                type_id,
                slug=slug,
                name=name,
                description=description,
                doc=doc,
                base_revision=base_revision,
            )
        except asyncpg.UniqueViolationError as exc:
            raise CatalogSlugTaken(slug) from exc

    async def set_equipment_cad(self, scope: Scope, type_id: str, cad_key: str | None) -> bool:
        return await self._db().set_equipment_type_cad(self.pool, type_id, cad_key)

    async def apply_inferred_bbox(self, scope: Scope, type_id: str, bbox: dict) -> bool:
        return await self._db().apply_inferred_bbox(self.pool, type_id, bbox)

    async def archive_equipment(self, scope: Scope, type_id: str) -> bool:
        return await self._db().archive_equipment_type(self.pool, type_id)

    async def list_systems(self, scope: Scope) -> list[dict]:
        return await self._db().list_system_templates(self.pool, scope_kind=scope.kind, scope_id=scope.id)

    async def system_docs(self, scope: Scope) -> dict[str, dict]:
        return await self._db().get_system_docs_by_scope(self.pool, scope_kind=scope.kind, scope_id=scope.id)

    async def get_system(self, scope: Scope, template_id: str) -> dict | None:
        try:
            row = await self._db().get_system_template(self.pool, template_id)
        except Exception:
            return None
        return row if row is not None and _in_scope(row, scope) else None

    async def create_system(self, scope: Scope, *, slug, name, description, created_by) -> dict | None:
        return await self._db().create_system_template(
            self.pool,
            scope_kind=scope.kind,
            scope_id=scope.id,
            slug=slug,
            name=name,
            description=description,
            created_by=created_by,
        )

    async def update_system(
        self, scope: Scope, template_id: str, *, slug, name, description, doc, base_revision
    ) -> int | None:
        import asyncpg

        try:
            return await self._db().update_system_template(
                self.pool,
                template_id,
                slug=slug,
                name=name,
                description=description,
                doc=doc,
                base_revision=base_revision,
            )
        except asyncpg.UniqueViolationError as exc:
            raise CatalogSlugTaken(slug) from exc

    async def archive_system(self, scope: Scope, template_id: str) -> bool:
        return await self._db().archive_system_template(self.pool, template_id)

    async def fingerprint(self, scope: Scope) -> str:
        return await self._db().get_catalog_fingerprint(self.pool, scope_kind=scope.kind, scope_id=scope.id)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


_SUMMARY_FIELDS = {
    "equipment": ("id", "slug", "name", "description", "cad_key", "revision", "created_by", "created_at", "updated_at"),
    "system": ("id", "slug", "name", "description", "revision", "created_by", "created_at", "updated_at"),
}


def _summary(kind: CatalogKind, record: dict) -> dict:
    """A record as db/catalogs.py's ``_equipment_row_summary``/``_system_row_summary``."""
    return {k: record.get(k) for k in _SUMMARY_FIELDS[kind]}


def _full(kind: CatalogKind, record: dict) -> dict:
    return {
        **_summary(kind, record),
        "scope_kind": record["scope_kind"],
        "scope_id": record["scope_id"],
        "doc": record["doc"],
    }


#: One lock for every file catalog in this process (see the module docstring).
_LOCK = asyncio.Lock()


class FileCatalogStore:
    """Storage files: one ``_catalogs/<catalog>/<id>.json`` per entry."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    @staticmethod
    def _dir(kind: CatalogKind) -> str:
        return EQUIPMENT_DIR if kind == "equipment" else SYSTEM_DIR

    def _key(self, kind: CatalogKind, entry_id: str) -> str:
        return f"{self._dir(kind)}{entry_id}.json"

    async def _load(self, scope: Scope, kind: CatalogKind, entry_id: str) -> dict | None:
        """The record, archived or not; ``None`` when absent, malformed or out of scope. The id is read
        as postgres reads a uuid (its case does not matter); the file is the lower-case spelling."""
        try:
            entry_id = str(uuid.UUID(str(entry_id)))
        except ValueError:
            return None
        try:
            record = json.loads(await self.storage.get_bytes(scope, self._key(kind, entry_id)))
        except Exception:
            return None
        if not isinstance(record, dict) or not _in_scope(record, scope) or record.get("id") != entry_id:
            return None
        return record

    async def _read(self, scope: Scope, kind: CatalogKind, entry_id: str) -> dict | None:
        record = await self._load(scope, kind, entry_id)
        return None if record is None or record.get("archived") else record

    async def _write(self, scope: Scope, kind: CatalogKind, record: dict) -> None:
        await self.storage.put_bytes(scope, self._key(kind, record["id"]), json.dumps(record).encode("utf-8"))

    async def _all(self, scope: Scope, kind: CatalogKind, *, strict: bool = False) -> list[dict]:
        """Every record of the catalog, archived ones included. A listing that fails reads as empty,
        or raises with ``strict`` (the seeding must not take a failed listing for an empty catalog)."""
        prefix = self._dir(kind)
        out: list[dict] = []
        try:
            entries = await self.storage.list_prefix(scope, prefix)
        except Exception:
            if strict:
                raise
            return out
        for entry in entries:
            name = entry.key[len(prefix) :] if entry.key.startswith(prefix) else ""
            if "/" in name or not name.endswith(".json"):
                continue
            record = await self._load(scope, kind, name[: -len(".json")])
            if record is not None:
                out.append(record)
        return out

    async def _live(self, scope: Scope, kind: CatalogKind) -> list[dict]:
        return [r for r in await self._all(scope, kind) if not r.get("archived")]

    def _new_record(self, scope: Scope, kind: CatalogKind, *, slug, name, description, created_by, doc) -> dict:
        stamp = _now()
        record = {
            "id": str(uuid.uuid4()),
            "scope_kind": scope.kind,
            "scope_id": scope.id,
            "slug": slug,
            "name": name,
            "description": description,
            "doc": doc,
            "revision": 0,
            "created_by": created_by,
            "created_at": stamp,
            "updated_at": stamp,
            "archived": False,
        }
        if kind == "equipment":
            record["cad_key"] = None
        return record

    async def _seed(self, scope: Scope, kind: CatalogKind) -> None:
        """Write the defaults into a catalog that has no entry at all (archived included)."""
        async with _LOCK:
            try:
                if await self._all(scope, kind, strict=True):
                    return
            except Exception:  # noqa: BLE001 -- a listing that failed is not an empty catalog
                logger.exception("catalog store: could not list the %s catalog of %s; not seeding it", kind, scope)
                return
            defaults = default_equipment_types() if kind == "equipment" else default_system_templates()
            try:
                for d in defaults:
                    await self._write(
                        scope,
                        kind,
                        self._new_record(
                            scope,
                            kind,
                            slug=d["slug"],
                            name=d["name"],
                            description=d["description"],
                            created_by=None,
                            doc=d["doc"],
                        ),
                    )
            except Exception:  # noqa: BLE001 -- a read-only store lists what it has
                logger.exception("catalog store: could not seed the %s catalog of %s", kind, scope)

    async def _entries(self, scope: Scope, kind: CatalogKind) -> list[dict]:
        """The live records, after seeding a catalog that has none at all."""
        await self._seed(scope, kind)
        return await self._live(scope, kind)

    async def _list(self, scope: Scope, kind: CatalogKind) -> list[dict]:
        return sorted((_summary(kind, r) for r in await self._entries(scope, kind)), key=lambda r: r["name"])

    async def _create(self, scope: Scope, kind: CatalogKind, *, slug, name, description, created_by) -> dict | None:
        await self._seed(scope, kind)  # the defaults first, so a first create does not stop them
        async with _LOCK:
            if any(r["slug"] == slug for r in await self._live(scope, kind)):
                return None
            record = self._new_record(
                scope,
                kind,
                slug=slug,
                name=name,
                description=description,
                created_by=created_by,
                doc=_default_doc(kind),
            )
            await self._write(scope, kind, record)
        return {**_summary(kind, record), "doc": record["doc"]}

    async def _update(
        self, scope: Scope, kind: CatalogKind, entry_id: str, *, slug, name, description, doc, base_revision
    ) -> int | None:
        async with _LOCK:
            record = await self._read(scope, kind, entry_id)
            if record is None or record["revision"] != base_revision:
                return None
            if any(r["slug"] == slug and r["id"] != record["id"] for r in await self._live(scope, kind)):
                raise CatalogSlugTaken(slug)
            record.update(slug=slug, name=name, description=description, doc=doc, updated_at=_now())
            record["revision"] += 1
            await self._write(scope, kind, record)
            return record["revision"]

    async def _patch(self, scope: Scope, kind: CatalogKind, entry_id: str, change) -> bool:
        async with _LOCK:
            record = await self._read(scope, kind, entry_id)
            if record is None:
                return False
            change(record)
            record["updated_at"] = _now()
            await self._write(scope, kind, record)
            return True

    # ── equipment ────────────────────────────────────────────────────

    async def list_equipment(self, scope: Scope) -> list[dict]:
        return await self._list(scope, "equipment")

    async def equipment_docs(self, scope: Scope) -> dict[str, dict]:
        return {r["slug"]: r["doc"] for r in await self._entries(scope, "equipment")}

    async def equipment_cad_keys(self, scope: Scope) -> dict[str, str]:
        return {
            r["slug"]: r["cad_key"]
            for r in await self._entries(scope, "equipment")
            if isinstance(r.get("cad_key"), str)
        }

    async def get_equipment(self, scope: Scope, type_id: str) -> dict | None:
        record = await self._read(scope, "equipment", type_id)
        return None if record is None else _full("equipment", record)

    async def create_equipment(self, scope: Scope, *, slug, name, description, created_by) -> dict | None:
        return await self._create(
            scope, "equipment", slug=slug, name=name, description=description, created_by=created_by
        )

    async def update_equipment(
        self, scope: Scope, type_id: str, *, slug, name, description, doc, base_revision
    ) -> int | None:
        return await self._update(
            scope,
            "equipment",
            type_id,
            slug=slug,
            name=name,
            description=description,
            doc=doc,
            base_revision=base_revision,
        )

    async def set_equipment_cad(self, scope: Scope, type_id: str, cad_key: str | None) -> bool:
        return await self._patch(scope, "equipment", type_id, lambda r: r.__setitem__("cad_key", cad_key))

    async def apply_inferred_bbox(self, scope: Scope, type_id: str, bbox: dict) -> bool:
        # No revision bump, as the postgres store: a background inference must not
        # race the user's optimistic commits.
        return await self._patch(scope, "equipment", type_id, lambda r: r["doc"].__setitem__("bbox", bbox))

    async def archive_equipment(self, scope: Scope, type_id: str) -> bool:
        return await self._patch(scope, "equipment", type_id, lambda r: r.__setitem__("archived", True))

    # ── systems ──────────────────────────────────────────────────────

    async def list_systems(self, scope: Scope) -> list[dict]:
        return await self._list(scope, "system")

    async def system_docs(self, scope: Scope) -> dict[str, dict]:
        return {r["slug"]: r["doc"] for r in await self._entries(scope, "system")}

    async def get_system(self, scope: Scope, template_id: str) -> dict | None:
        record = await self._read(scope, "system", template_id)
        return None if record is None else _full("system", record)

    async def create_system(self, scope: Scope, *, slug, name, description, created_by) -> dict | None:
        return await self._create(scope, "system", slug=slug, name=name, description=description, created_by=created_by)

    async def update_system(
        self, scope: Scope, template_id: str, *, slug, name, description, doc, base_revision
    ) -> int | None:
        return await self._update(
            scope,
            "system",
            template_id,
            slug=slug,
            name=name,
            description=description,
            doc=doc,
            base_revision=base_revision,
        )

    async def archive_system(self, scope: Scope, template_id: str) -> bool:
        return await self._patch(scope, "system", template_id, lambda r: r.__setitem__("archived", True))

    # ── fingerprint ──────────────────────────────────────────────────

    async def fingerprint(self, scope: Scope) -> str:
        """The value db/catalogs.py's ``get_catalog_fingerprint`` computes, over the files."""

        def _md5(parts: list[str]) -> str:
            return hashlib.md5(",".join(parts).encode("utf-8")).hexdigest()

        def _by_slug(records: list[dict]) -> list[dict]:
            return sorted(records, key=lambda r: r["slug"])

        eq = [
            f"{r['slug']}:{r['revision']}:{r.get('cad_key') or ''}"
            for r in _by_slug(await self._entries(scope, "equipment"))
        ]
        sy = [f"{r['slug']}:{r['revision']}" for r in _by_slug(await self._entries(scope, "system"))]
        return hashlib.sha256(f"eq:{_md5(eq)}|sy:{_md5(sy)}".encode("utf-8")).hexdigest()[:16]


def catalog_store(pool: Any, storage: Storage, *, local: bool = True) -> CatalogStore | None:
    """Postgres when this server has a pool; the scope's storage when it has none AND
    runs its jobs in-process (``local``: no queue); else None (the routes answer 503).
    Same rule, same reason as :func:`..procedural_store.procedural_store`."""
    if pool is not None:
        return DbCatalogStore(pool)
    return FileCatalogStore(storage) if local else None


__all__ = [
    "BOX_SLUG",
    "CATALOG_PREFIX",
    "CatalogSlugTaken",
    "CatalogStore",
    "DbCatalogStore",
    "FileCatalogStore",
    "catalog_store",
    "default_equipment_types",
    "default_system_templates",
    "equipment_type_key",
    "system_template_key",
]
