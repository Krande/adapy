"""Read/list/use per-scope catalog equipment types and system templates from
the viewer's postgres, from the **Python API**.

The viewer stores reusable equipment archetypes (bounding box, mass, IFC class,
ports) and system templates (service type, medium, voltage, pipe sizing) per
scope in postgres, editable from the admin panels. This module exposes them to
a plain Python caller so a script or notebook can list them, turn one into an
:class:`ada.Equipment` / :class:`~ada.topology.entities.TopoSystem`, and — for
equipment — get the ``slug -> catalog doc`` resolver
:class:`~ada.topo_model.builder.ProceduralBuilder` needs.

The actual DB layer is asyncpg/async and its pool is bound to the event loop
that created it. :class:`ProceduralCatalog` therefore owns a dedicated loop
for its lifetime and drives the async helpers on it, presenting a
synchronous API. Use it as a context manager (or call :meth:`close`) to
release the pool + loop.

:class:`ProceduralCatalog` never imports the DB layer itself — that would
make ``ada.topo_model`` depend on ``ada.comms.rest``, backwards from every
other dependency in the package (``ada.comms.rest`` depends on
``ada.topo_model``, not the other way round). Instead :meth:`connect` takes
a ``db`` argument satisfying :class:`CatalogDbAdapter`, a small
:class:`~typing.Protocol` naming just the handful of async operations the
catalog needs. ``ada.comms.rest.db`` already exposes matching module-level
functions, so the module itself is a valid adapter — no wrapper needed::

    from ada.comms.rest import db as db_module

    with ProceduralCatalog.connect(scope_kind="user", scope_id="me", db=db_module) as cat:
        for et in cat.list_equipment_types():
            print(et.slug, et.name)
        pump = cat.get_equipment_type("pump").to_equipment("P1", origin=(2, 2, 3))
        builder = ProceduralBuilder(spaces=[...], equipments=[pump],
                                    equipment_resolver=cat.equipment_resolver())
        builder.compile()

``asyncpg`` is imported lazily inside :meth:`connect`, so importing this
module never requires the viewer/DB dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Protocol

if TYPE_CHECKING:
    import ada
    from ada.topology.entities import TopoSystem

__all__ = ["EquipmentType", "SystemTemplate", "ProceduralCatalog", "CatalogDbAdapter"]


class CatalogDbAdapter(Protocol):
    """The async DB operations :class:`ProceduralCatalog` needs.

    Structurally matches ``ada.comms.rest.db``'s module-level functions
    (same names, same keyword-only ``scope_kind``/``scope_id``), so that
    module satisfies this Protocol as-is and can be passed directly as
    ``db=`` — the whole point being that ``ada.topo_model`` names the shape
    of what it needs, never the module that provides it.
    """

    async def init_pool(self, database_url: str): ...

    async def close_pool(self, pool) -> None: ...

    async def list_equipment_types(self, pool, *, scope_kind: str, scope_id: str | None) -> list[dict]: ...

    async def get_equipment_docs_by_scope(self, pool, *, scope_kind: str, scope_id: str | None) -> dict[str, dict]: ...

    async def list_system_templates(self, pool, *, scope_kind: str, scope_id: str | None) -> list[dict]: ...

    async def get_system_template(self, pool, template_id: str) -> dict | None: ...


@dataclass
class EquipmentType:
    """A catalog equipment archetype: identity + the catalog ``doc`` (bbox, mass,
    cog, IFC element class, ports) the compiler resolves a placed equipment to."""

    slug: str
    name: str
    doc: dict
    description: str | None = None
    revision: int = 0
    cad_key: str | None = None
    id: str | None = None

    def to_equipment(self, name: str, origin, lx=None, ly=None, lz=None) -> "ada.Equipment":
        """Build a placed :class:`ada.Equipment` (with ports + IFC class) from
        this type's catalog doc, overriding the box extents when given."""
        from .equipment import build_equipment_from_catalog

        return build_equipment_from_catalog(name, origin, self.doc, lx=lx, ly=ly, lz=lz)


@dataclass
class SystemTemplate:
    """A catalog system template: identity + the catalog ``doc`` (service type,
    medium, voltage, pipe sizing) that seeds a routed system."""

    slug: str
    name: str
    doc: dict
    description: str | None = None
    revision: int = 0
    id: str | None = None

    def to_system(self, name: str, connections=None) -> "TopoSystem":
        """Seed a :class:`~ada.topology.entities.TopoSystem` from this template's
        service type + medium; ``connections`` are the endpoints to route."""
        from ada.topology.entities import TopoSystem

        return TopoSystem(
            NAME=name,
            TYPE=self.doc.get("type", "piping"),
            MEDIUM=self.doc.get("medium"),
            CONNECTIONS=connections or [],
        )


@dataclass
class ProceduralCatalog:
    """A synchronous, scope-bound reader over the viewer's postgres catalog.

    Create with :meth:`connect`. Bound to one scope (``scope_kind`` +
    ``scope_id``) for its lifetime; use a different instance for a different
    scope."""

    scope_kind: str
    scope_id: str | None
    _db: CatalogDbAdapter = field(repr=False)
    _pool: object = field(repr=False)
    _loop: object = field(repr=False)

    @classmethod
    def connect(
        cls,
        database_url: str | None = None,
        *,
        scope_kind: str = "shared",
        scope_id: str | None = None,
        db: CatalogDbAdapter,
    ) -> "ProceduralCatalog":
        """Open a catalog against ``database_url`` (defaults to the ``DATABASE_URL``
        environment variable), bound to the given scope, using ``db`` for the actual
        queries (see :class:`CatalogDbAdapter` — ``ada.comms.rest.db`` is a ready-made
        one). Raises if no URL is set or the DB is in shared-only mode (no pool)."""
        import asyncio
        import os

        url = database_url or os.environ.get("DATABASE_URL", "").strip()
        if not url:
            raise RuntimeError("DATABASE_URL is not set — pass database_url= or export DATABASE_URL")

        loop = asyncio.new_event_loop()
        pool = loop.run_until_complete(db.init_pool(url))
        if pool is None:
            loop.close()
            raise RuntimeError(f"could not open a DB pool for {url!r} (shared-only mode / unreachable)")
        return cls(scope_kind=scope_kind, scope_id=scope_id, _db=db, _pool=pool, _loop=loop)

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    # --- equipment ----------------------------------------------------------
    def list_equipment_types(self) -> list[EquipmentType]:
        """All live equipment types in this scope, each carrying its catalog doc."""

        async def _load():
            rows = await self._db.list_equipment_types(self._pool, scope_kind=self.scope_kind, scope_id=self.scope_id)
            docs = await self._db.get_equipment_docs_by_scope(
                self._pool, scope_kind=self.scope_kind, scope_id=self.scope_id
            )
            return rows, docs

        rows, docs = self._run(_load())
        return [
            EquipmentType(
                slug=r["slug"],
                name=r["name"],
                doc=docs.get(r["slug"], {}),
                description=r.get("description"),
                revision=r.get("revision", 0),
                cad_key=r.get("cad_key"),
                id=r.get("id"),
            )
            for r in rows
        ]

    def get_equipment_type(self, slug: str) -> EquipmentType:
        """The equipment type with ``slug`` in this scope (raises ``KeyError`` if
        absent, listing the available slugs)."""
        types = {et.slug: et for et in self.list_equipment_types()}
        if slug not in types:
            raise KeyError(f"no equipment type {slug!r} in scope; available: {sorted(types)}")
        return types[slug]

    def equipment_resolver(self) -> Callable[[str], dict | None]:
        """A ``slug -> catalog doc`` callable to pass as
        ``ProceduralBuilder(equipment_resolver=...)`` — the same mapping the
        compile worker uses."""
        docs = self._run(
            self._db.get_equipment_docs_by_scope(self._pool, scope_kind=self.scope_kind, scope_id=self.scope_id)
        )
        return docs.get

    # --- systems ------------------------------------------------------------
    def list_system_templates(self) -> list[SystemTemplate]:
        """All live system templates in this scope, each carrying its catalog doc."""

        async def _load():
            rows = await self._db.list_system_templates(self._pool, scope_kind=self.scope_kind, scope_id=self.scope_id)
            out = []
            for r in rows:
                full = await self._db.get_system_template(self._pool, r["id"])
                out.append((r, full.get("doc", {}) if full else {}))
            return out

        return [
            SystemTemplate(
                slug=r["slug"],
                name=r["name"],
                doc=doc,
                description=r.get("description"),
                revision=r.get("revision", 0),
                id=r.get("id"),
            )
            for r, doc in self._run(_load())
        ]

    def get_system_template(self, slug: str) -> SystemTemplate:
        """The system template with ``slug`` in this scope (raises ``KeyError``)."""
        templates = {st.slug: st for st in self.list_system_templates()}
        if slug not in templates:
            raise KeyError(f"no system template {slug!r} in scope; available: {sorted(templates)}")
        return templates[slug]

    # --- lifecycle ----------------------------------------------------------
    def close(self) -> None:
        """Release the DB pool and the owned event loop."""
        if self._pool is not None:
            self._run(self._db.close_pool(self._pool))
            self._pool = None
        if self._loop is not None:
            self._loop.close()
            self._loop = None

    def __enter__(self) -> "ProceduralCatalog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
