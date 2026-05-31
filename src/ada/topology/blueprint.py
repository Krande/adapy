"""Blueprint base: the generic plumbing for turning a cell graph into a part.

A blueprint owns an ``output_part`` and an ``area_map`` (area name -> list of
parts produced for that area), and knows how to assemble the area map into the
output part. The concrete ``build`` — what geometry each face/cell becomes — is
domain-specific and supplied by subclasses.

This base names no structural-engineering concept; domain layers subclass it and
add their design logic (loads, constraints, sections, …).
"""
from __future__ import annotations

import abc

import ada

__all__ = ["BlueprintBase"]


class BlueprintBase(abc.ABC):
    def __init__(self, builder: object | None = None):
        self.builder = builder
        self.output_part: ada.Part | None = None
        self.area_map: dict = {}

    @abc.abstractmethod
    def build(self) -> ada.Part:
        """Build and return the output part. Subclasses implement the design."""
        ...

    def _group_prefix(self) -> str:
        """Prefix for the per-area group names. Domain layers override (e.g. a
        structure name); empty by default."""
        return ""

    def load_parts_from_area_map(self) -> None:
        """Fold the accumulated ``area_map`` into ``output_part`` as one part per
        area, each carrying a group of its physical objects."""
        for area_name, parts in self.area_map.items():
            set_members = []
            for part in parts:
                set_members.extend(part.get_all_physical_objects())
            area_part = ada.Part(area_name) / parts

            # Create group for each area
            area_part.add_group(f"{self._group_prefix()}_area_{area_name}", set_members)

            self.output_part.add_part(area_part)
