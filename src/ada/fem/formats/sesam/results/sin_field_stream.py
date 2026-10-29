"""A SIN's fields one result case and RV card at a time, without the whole result resident.

:func:`~.read_sin.read_sin_file` materialises every case of every field. This reads the
mesh and the step-invariant blocks up front and then one ``(case, RV card)`` at a time:
the same records, mapped by the same :class:`~.read_sif.Sif2Mesh` code and, on an
assembly, placed and merged by the same :class:`~.assembly.AssembledMesh`. Concatenating
every chunk gives the fields :func:`~.read_sin.read_sin_file` returns.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator

from ada.fem.formats.sesam.results.read_sin import (
    _RV_TYPE_NAMES,
    SinCases,
    assembly_parts,
    _refuse_selected_without_mesh,
    attach_sesam_metadata,
    plan_sin_read,
)

if TYPE_CHECKING:
    from ada.fem.formats.sesam.results.sin_reader import SinFile
    from ada.fem.results.common import FEAResult, Mesh

__all__ = ["SinFieldStream"]


@dataclass
class _Part:
    """One superelement: its cases (:class:`~.read_sin.SinCases`) and the RV cards it has."""

    cases: SinCases
    cards: set[str]
    eigen_data: object
    property_fields_taken: bool = False

    @property
    def mesh(self) -> Mesh:
        return self.cases.mesh

    @property
    def cases_to_read(self) -> set[int]:
        return set(self.cases.cases)

    def load(self, step: int, card: str) -> list:
        """The fields of ``card`` in result case ``step``, as a full read maps them: the
        property fields come with the first case that has any field."""
        if card not in self.cards:
            return []
        fields = self.fields(step, {card}, property_fields=not self.property_fields_taken)
        if fields:
            self.property_fields_taken = True
        return fields

    def fields(self, step, cards=None, *, property_fields=True, requested_fields=None) -> list:
        from ada.fem.formats.sesam.results.read_sif import stamp_eigen_data

        if cards is not None:
            cards = set(cards) & self.cards
            if not cards and step is not None:
                return []
        fields = self.cases.fields(step, cards, property_fields=property_fields, requested_fields=requested_fields)
        stamp_eigen_data(fields, self.eigen_data)
        return fields


@dataclass
class SinFieldStream:
    """The fields of a SIN in chunks of one result case and one RV card.

    ``header`` is the result without fields: the mesh (on an assembly, every superelement
    placed and merged), names and metadata. :meth:`iter_chunks` yields ``((case, card
    rank), fields)`` in ascending order; each chunk is built when asked for and holds
    nothing of the previous one. The chunks together are the fields of
    :func:`~.read_sin.read_sin_file` with the same arguments.
    """

    header: FEAResult
    steps: list[int]
    cards: list[str]
    _parts: list[_Part] = field(repr=False)
    _assembled: object = field(default=None, repr=False)

    @classmethod
    def open(cls, sin_file, *, super_element=None, complex_phase: float = 0.0) -> SinFieldStream:
        sin, rows, merge = plan_sin_read(sin_file, super_element)
        name_path = pathlib.Path(str(sin.path if sin.path is not None else sin_file))
        return cls._open(sin, rows, merge, name_path, complex_phase, None)

    @classmethod
    def over(
        cls, sin: SinFile, *, super_element=None, complex_phase: float = 0.0, forces_elements=None
    ) -> SinFieldStream:
        """The stream of an open ``sin``: ``super_element`` when given, else the superelement
        ``sin`` has selected, else, on an assembly, every superelement merged.

        ``forces_elements`` narrows the RVFORCES decode to these element ids; it needs a
        single superelement, whose own numbering the ids are in.
        """
        rows = merge = None
        if super_element is not None:
            sin.select_super_element(super_element)
        elif sin._selected is None:
            rows, merge = assembly_parts(sin)
        if merge is not None and forces_elements is not None:
            raise ValueError("forces_elements narrows one superelement; choose it with super_element")
        name_path = sin.path if sin.path is not None else pathlib.Path("remote.sin")
        return cls._open(sin, rows, merge, pathlib.Path(str(name_path)), complex_phase, forces_elements)

    @classmethod
    def _open(cls, sin, rows, merge, name_path, complex_phase, forces_elements) -> SinFieldStream:
        from ada.fem.formats.sesam.results.assembly import assemble_meshes, placements

        # Every case of every RV card is read in turn: group each card's records by case once.
        sin.index_cases = True
        if merge is None:
            _refuse_selected_without_mesh(sin)
            part, header = _open_part(sin, None, name_path, complex_phase, forces_elements)
            return cls._build(header, [part], None)

        # One SinFile for every superelement: each entry is decoded once and kept, and
        # switching the active one is a lookup.
        parts, headers = [], []
        for row in merge:
            sin.select_super_element(row)
            part, head = _open_part(sin, row.iref, name_path, complex_phase, None)
            parts.append(part)
            headers.append(head)
        assembled = assemble_meshes(merge, headers, placements(rows), name=name_path.stem, results_file_path=sin.path)
        return cls._build(assembled.result, parts, assembled)

    @classmethod
    def _build(cls, header, parts, assembled) -> SinFieldStream:
        steps = sorted(set().union(*(p.cases_to_read for p in parts)))
        cards = [c for c in _RV_TYPE_NAMES if any(c in p.cards for p in parts)]
        return cls(header, steps, cards, parts, assembled)

    def iter_chunks(self) -> Iterator[tuple[tuple[int, int], list]]:
        need_super_element_field = True
        for step in self.steps:
            for rank, card in enumerate(self.cards):
                fields = self._merged([p.load(step, card) for p in self._parts], need_super_element_field)
                if fields:
                    need_super_element_field = False
                yield (step, rank), fields
                del fields

    def fields(
        self,
        step: int | None,
        cards: "set[str] | None" = None,
        *,
        property_fields: bool = True,
        requested_fields: "set[str] | None" = None,
    ) -> list:
        """The fields of ``cards`` (None: every card) in case ``step``, every superelement's
        merged; with ``property_fields``, the property fields too (they ride on a case).
        ``step=None`` with ``cards=set()`` reads no case."""
        per_part = [
            p.fields(step, cards, property_fields=property_fields, requested_fields=requested_fields)
            for p in self._parts
        ]
        fields = self._merged(per_part, property_fields)
        if requested_fields is not None:
            fields = [f for f in fields if f.name in requested_fields]
        return fields

    def property_fields(self, step: int) -> list:
        """The property fields (thickness, material, section; on an assembly, the
        superelement) on case ``step``, without reading any case."""
        from ada.fem.formats.sesam.results.property_fields import build_property_fields

        per_part = []
        for p in self._parts:
            reader = p.cases.reader
            p.cases._activate()
            per_part.append(build_property_fields(p.mesh, reader, step=step))
        return self._merged(per_part, True)

    def _merged(self, per_part: list[list], super_element_field: bool) -> list:
        if self._assembled is None:
            fields = per_part[0]
        else:
            fields = self._assembled.merge_fields(per_part)
            if super_element_field and fields:
                fields += self._assembled.super_element_fields(fields)
        for f in fields:
            f._mesh = self.header.mesh
        return fields

    def close(self) -> None:
        self._parts[0].cases.sin.close()

    def __enter__(self) -> SinFieldStream:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _open_part(
    sin: SinFile, iref, name_path: pathlib.Path, complex_phase: float, forces_elements=None
) -> tuple[_Part, FEAResult]:
    """The active superelement's step-invariant blocks read, and its result without fields."""
    from ada.fem.formats.sesam.results.read_sif import Sif2Mesh, read_lis_eigen_data

    cases = SinCases(sin, iref=iref, complex_phase=complex_phase, forces_elements=forces_elements)
    reader = cases.reader
    reader.load_step(None, cards=set())
    header = Sif2Mesh(reader).convert(name_path)
    attach_sesam_metadata(header, reader, sin)
    cases.mesh = header.mesh
    cards = {c for c in _RV_TYPE_NAMES if c in sin.type_blocks}
    part = _Part(cases, cards, read_lis_eigen_data(name_path))
    return part, header
