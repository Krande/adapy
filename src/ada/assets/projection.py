"""``hierarchy.json`` -- the browser-readable spine. Columnar, name-indexed, depth-bounded.

Columnar because the numbers demand it: a spine is ~64 bytes per node in this form, so a 41k-node
tree is one 2.6 MB fetch instead of tens of thousands of per-node objects. The browser expands
lazily from one slice rather than walking the store.

Three rules, each of which exists because breaking it has bitten the prior art:

1. **Refuse an unknown schema.** Never read the columns you recognise out of a future document.
2. **Index columns by NAME, never by position.** ``cols`` is a header; adding a column must not
   silently reinterpret existing rows for an older reader.
3. **Accept ``leaf`` as 1 or true.** Producers have emitted both; a strict reader turns every
   node into a branch and the tree quietly stops being expandable at the right places.

``delivery`` on a row is the CLAIM for that node -- "" (none), "mesh" or "build". It travels with
the spine so the browser can render an actionable row without a per-node round trip.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterator, Mapping, Sequence

__all__ = [
    "HIERARCHY_SCHEMA",
    "BASE_COLS",
    "HierarchyError",
    "HierarchySlice",
    "build_hierarchy",
    "parse_hierarchy",
]

HIERARCHY_SCHEMA = "ada.assets/hierarchy@1"

# The columns every slice carries. "path" is the one optional extra core knows about; a provider
# may add its own, and a reader that does not know a column ignores it.
BASE_COLS = ("id", "parent", "label", "kind", "leaf", "delivery")

_DELIVERY_VALUES = ("", "mesh", "build")


class HierarchyError(ValueError):
    """A hierarchy document that cannot be read."""


def _as_leaf(value: Any) -> bool:
    """1 | true | "1" | "true" -> True. Producers have emitted all of these."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


@dataclass(frozen=True)
class HierarchySlice:
    provider: str
    collection: str
    produced_at: str
    cols: tuple[str, ...]
    rows: tuple[tuple, ...]
    root: str | None = None  # None = the collection index (depth 1 below the declared roots)
    depth: int = 1
    schema: str = HIERARCHY_SCHEMA

    def __post_init__(self) -> None:
        missing = [c for c in BASE_COLS if c not in self.cols]
        if missing:
            raise HierarchyError(f"hierarchy is missing required column(s): {', '.join(missing)}")
        width = len(self.cols)
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise HierarchyError(
                    f"row {i} has {len(row)} values but there are {width} columns -- "
                    f"rows are positional against 'cols', so a short row is unreadable"
                )

    def column(self, name: str) -> int:
        """Index of a column BY NAME. The only sanctioned way to reach into a row."""
        try:
            return self.cols.index(name)
        except ValueError:
            raise HierarchyError(f"no column {name!r} in this slice; it has {self.cols}") from None

    def records(self) -> Iterator[dict]:
        """Rows as name-keyed dicts, with ``leaf`` normalised to bool."""
        idx = {name: i for i, name in enumerate(self.cols)}
        leaf_at = idx["leaf"]
        for row in self.rows:
            rec = {name: row[i] for name, i in idx.items()}
            rec["leaf"] = _as_leaf(row[leaf_at])
            yield rec

    def node_ids(self) -> tuple[str, ...]:
        at = self.column("id")
        return tuple(str(r[at]) for r in self.rows)

    def to_json(self) -> bytes:
        doc = {
            "schema": self.schema,
            "provider": self.provider,
            "collection": self.collection,
            "root": self.root,
            "produced_at": self.produced_at,
            "depth": self.depth,
            "cols": list(self.cols),
            "rows": [list(r) for r in self.rows],
        }
        return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def build_hierarchy(
    *,
    provider: str,
    collection: str,
    produced_at: str,
    nodes: Sequence[Mapping[str, Any]],
    root: str | None = None,
    depth: int = 1,
    extra_cols: Sequence[str] = (),
) -> HierarchySlice:
    """Compose a slice from name-keyed node dicts. Validates the delivery vocabulary, which is
    core's, unlike ``kind`` and ``label`` which are the provider's to choose."""
    cols = tuple(BASE_COLS) + tuple(c for c in extra_cols if c not in BASE_COLS)
    rows = []
    for n in nodes:
        delivery = n.get("delivery", "") or ""
        if delivery not in _DELIVERY_VALUES:
            raise HierarchyError(
                f"node {n.get('id')!r}: delivery {delivery!r} not in {_DELIVERY_VALUES} "
                f"(that vocabulary is core's; 'kind' and 'label' are yours)"
            )
        rows.append(
            tuple(
                [
                    n.get("id"),
                    n.get("parent"),
                    n.get("label"),
                    n.get("kind"),
                    1 if _as_leaf(n.get("leaf")) else 0,
                    delivery,
                ]
                + [n.get(c) for c in cols[len(BASE_COLS) :]]
            )
        )
    return HierarchySlice(
        provider=provider,
        collection=collection,
        produced_at=produced_at,
        cols=cols,
        rows=tuple(rows),
        root=root,
        depth=depth,
    )


def parse_hierarchy(doc: bytes | str) -> HierarchySlice:
    try:
        raw = json.loads(doc)
    except (ValueError, TypeError) as exc:
        raise HierarchyError(f"hierarchy is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise HierarchyError(f"hierarchy must be a JSON object, got {type(raw).__name__}")

    schema = raw.get("schema")
    if schema != HIERARCHY_SCHEMA:
        raise HierarchyError(
            f"unknown hierarchy schema {schema!r}: this core reads {HIERARCHY_SCHEMA!r} only. "
            f"Refusing rather than reading the columns it recognises."
        )
    for required in ("provider", "collection", "produced_at", "cols", "rows"):
        if required not in raw:
            raise HierarchyError(f"hierarchy missing required field {required!r}")

    cols = tuple(raw["cols"])
    rows = tuple(tuple(r) for r in raw["rows"])
    return HierarchySlice(
        schema=schema,
        provider=str(raw["provider"]),
        collection=str(raw["collection"]),
        produced_at=str(raw["produced_at"]),
        cols=cols,
        rows=rows,
        root=raw.get("root"),
        depth=int(raw.get("depth", 1)),
    )
