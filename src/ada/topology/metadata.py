"""Metadata side-table for the topology graph.

A plain, backend-neutral key/value record attached to cells and faces, keyed by
a stable name. Replaces the kernel-specific dictionary the original toolkit
hung off topology handles, so the graph layer carries no kernel dependency for
metadata. Domain layers (e.g. structural models) can subclass or wrap this to
expose typed accessors without the generic core knowing about them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TopologyMetadata:
    """Free-form metadata for a cell or face.

    ``name`` is the stable identifier the graph uses for lookups; ``properties``
    holds everything else (e.g. ingested IFC property sets) as a plain dict.
    """

    name: str = "Cell"
    properties: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.properties.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.properties[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.properties
