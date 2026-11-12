"""Where procedural models are kept: postgres when there is one, storage files when not.

A procedural model is one row in ``procedural_models`` (db/procedural_models.py): the
doc, its revision under optimistic concurrency, and a scope-unique name. That needs a
database, and a LOCAL viewer (``python -m ada.comms.rest`` on a laptop, storage kind
``local``, no ``DATABASE_URL``) has none - so its Build mode had no models at all, and
every procedural route answered 503.

:class:`FileProceduralStore` keeps the same record as a JSON file in the scope's own
storage, beside the artifacts the model already has there::

    _procedural/<id>/model.json      {id, scope_kind, scope_id, name, doc, revision,
                                      engine, schema_version, created_by, created_at,
                                      updated_at, archived}

``_procedural/`` is a hidden prefix, so the file browser does not list it. The routes ask
:func:`procedural_store` for "the store here" and call the same five operations either
way; what they cannot have without a database (the equipment/system catalogs, stats
exports, xlsx import) still says so.

ONE PROCESS. A local viewer is one server process, so a process-wide lock is enough to
make the name check and the revision bump atomic. The file store is not meant for a
deployment with several API replicas sharing a bucket: that is what postgres is for, and
:func:`procedural_store` only picks files when there is no pool.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import uuid
from typing import Any, Protocol

from .scope import Scope
from .storage import Storage

MODEL_DIR = "_procedural/"
MODEL_FILE = "model.json"
#: What a new model's doc is, as the postgres column default (migration 022).
EMPTY_DOC: dict[str, Any] = {"grid": {}, "spaces": [], "equipments": [], "openings": []}
#: The routing header defaults (migration 026).
DEFAULT_ENGINE = "adapy-default"
DEFAULT_SCHEMA_VERSION = "1.0"


def model_key(model_id: str) -> str:
    return f"{MODEL_DIR}{model_id}/{MODEL_FILE}"


class ProceduralStore(Protocol):
    """The operations the routes need, with db/procedural_models.py's return shapes."""

    async def create(self, scope: Scope, *, name: str, created_by: str | None) -> dict | None: ...
    async def list(self, scope: Scope) -> list[dict]: ...
    async def get(self, scope: Scope, model_id: str) -> dict | None: ...
    async def update_doc(self, scope: Scope, model_id: str, doc: dict, base_revision: int) -> int | None: ...
    async def rename(self, scope: Scope, model_id: str, name: str) -> dict | None | bool: ...
    async def archive(self, scope: Scope, model_id: str) -> bool: ...


class DbProceduralStore:
    """Postgres: db/procedural_models.py, unchanged."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def create(self, scope: Scope, *, name: str, created_by: str | None) -> dict | None:
        from . import db as db_module

        return await db_module.create_procedural_model(
            self.pool, scope_kind=scope.kind, scope_id=scope.id, name=name, created_by=created_by
        )

    async def list(self, scope: Scope) -> list[dict]:
        from . import db as db_module

        return await db_module.list_procedural_models(self.pool, scope_kind=scope.kind, scope_id=scope.id)

    async def get(self, scope: Scope, model_id: str) -> dict | None:
        from . import db as db_module

        try:
            row = await db_module.get_procedural_model(self.pool, model_id)
        except Exception:
            return None  # malformed UUID etc.: not found, not a 500
        if row is None or not _in_scope(row, scope):
            return None
        return row

    async def update_doc(self, scope: Scope, model_id: str, doc: dict, base_revision: int) -> int | None:
        from . import db as db_module

        return await db_module.update_procedural_model_doc(self.pool, model_id, doc, base_revision)

    async def rename(self, scope: Scope, model_id: str, name: str) -> dict | None | bool:
        from . import db as db_module

        return await db_module.rename_procedural_model(self.pool, model_id, name)

    async def archive(self, scope: Scope, model_id: str) -> bool:
        from . import db as db_module

        return await db_module.archive_procedural_model(self.pool, model_id)


def _in_scope(row: dict, scope: Scope) -> bool:
    return row.get("scope_kind") == scope.kind and (row.get("scope_id") or None) == (scope.id or None)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _summary(record: dict) -> dict:
    """A record as db/procedural_models.py's ``_procedural_row_summary`` returns it."""
    return {k: record.get(k) for k in ("id", "name", "revision", "created_by", "created_at", "updated_at", "engine", "schema_version")}


#: One lock for every file store in this process (see the module docstring).
_LOCK = asyncio.Lock()


class FileProceduralStore:
    """Storage files: one ``_procedural/<id>/model.json`` per model."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    async def _read(self, scope: Scope, model_id: str) -> dict | None:
        try:
            uuid.UUID(str(model_id))
        except ValueError:
            return None
        try:
            record = json.loads(await self.storage.get_bytes(scope, model_key(model_id)))
        except Exception:
            return None
        if not isinstance(record, dict) or record.get("archived") or not _in_scope(record, scope):
            return None
        return record

    async def _write(self, scope: Scope, record: dict) -> None:
        await self.storage.put_bytes(scope, model_key(record["id"]), json.dumps(record).encode("utf-8"))

    async def _records(self, scope: Scope) -> list[dict]:
        out: list[dict] = []
        for entry in await self.storage.list_prefix(scope, MODEL_DIR):
            key = entry.key
            if not key.endswith(f"/{MODEL_FILE}") or key.count("/") != 2:
                continue
            record = await self._read(scope, key.split("/")[1])
            if record is not None:
                out.append(record)
        return out

    async def _name_taken(self, scope: Scope, name: str, *, other_than: str | None = None) -> bool:
        return any(r["name"] == name and r["id"] != other_than for r in await self._records(scope))

    async def create(self, scope: Scope, *, name: str, created_by: str | None) -> dict | None:
        async with _LOCK:
            if await self._name_taken(scope, name):
                return None
            stamp = _now()
            body = json.loads(json.dumps(EMPTY_DOC))
            record = {
                "id": str(uuid.uuid4()),
                "scope_kind": scope.kind,
                "scope_id": scope.id,
                "name": name,
                "doc": body,
                "revision": 0,
                "engine": DEFAULT_ENGINE,
                "schema_version": DEFAULT_SCHEMA_VERSION,
                "created_by": created_by,
                "created_at": stamp,
                "updated_at": stamp,
                "archived": False,
            }
            await self._write(scope, record)
        return {**_summary(record), "doc": record["doc"]}

    async def list(self, scope: Scope) -> list[dict]:
        return sorted((_summary(r) for r in await self._records(scope)), key=lambda r: r["name"])

    async def get(self, scope: Scope, model_id: str) -> dict | None:
        record = await self._read(scope, model_id)
        if record is None:
            return None
        return {**_summary(record), "scope_kind": record["scope_kind"], "scope_id": record["scope_id"], "doc": record["doc"]}

    async def update_doc(self, scope: Scope, model_id: str, doc: dict, base_revision: int) -> int | None:
        async with _LOCK:
            record = await self._read(scope, model_id)
            if record is None or record["revision"] != base_revision:
                return None
            record["doc"] = doc
            record["revision"] += 1
            record["updated_at"] = _now()
            # The routing header is mirrored from the doc, as the postgres update does.
            record["engine"] = doc.get("engine") or record.get("engine") or DEFAULT_ENGINE
            record["schema_version"] = doc.get("schema_version") or record.get("schema_version") or DEFAULT_SCHEMA_VERSION
            await self._write(scope, record)
            return record["revision"]

    async def rename(self, scope: Scope, model_id: str, name: str) -> dict | None | bool:
        async with _LOCK:
            record = await self._read(scope, model_id)
            if record is None:
                return None
            if await self._name_taken(scope, name, other_than=model_id):
                return False
            record["name"] = name
            record["updated_at"] = _now()
            await self._write(scope, record)
            return _summary(record)

    async def archive(self, scope: Scope, model_id: str) -> bool:
        async with _LOCK:
            record = await self._read(scope, model_id)
            if record is None:
                return False
            record["archived"] = True
            record["updated_at"] = _now()
            await self._write(scope, record)
            return True


def procedural_store(pool: Any, storage: Storage) -> ProceduralStore:
    """Postgres when this server has a pool, the scope's storage when it has none."""
    return DbProceduralStore(pool) if pool is not None else FileProceduralStore(storage)


__all__ = [
    "DbProceduralStore",
    "FileProceduralStore",
    "ProceduralStore",
    "model_key",
    "procedural_store",
]
