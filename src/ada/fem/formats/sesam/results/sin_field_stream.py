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
    SinReader,
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
    """One superelement: its reader, holding the step-invariant blocks, and its mesh."""

    sin: SinFile
    #: The superelement's entry in ``sin``, made active before each read.
    iref: int | None
    reader: SinReader
    mesh: Mesh
    #: The RV cards the superelement has.
    cards: set[str]
    stored: set[int]
    #: Combinations the file does not store, read as recipes over its stored cases.
    unstored: dict
    eigen_data: object
    property_fields_taken: bool = False

    def load(self, step: int, card: str) -> list:
        """The fields of ``card`` in result case ``step``, as a full read maps them."""
        from ada.fem.formats.sesam.results.read_sif import Sif2Mesh, stamp_eigen_data

        if card not in self.cards:
            return []
        if self.iref is not None:
            self.sin.use_super_element(self.iref)
        reader = self.reader
        if step in self.stored:
            reader.load_step(step, cards={card})
        elif step in self.unstored:
            reader.load_combination(step, self.unstored[step], cards={card})
        else:
            return []
        s2m = Sif2Mesh(reader)
        s2m.mesh = self.mesh
        # A full read puts the property fields on its first case only.
        fields = s2m.get_sif_results(property_fields=not self.property_fields_taken)
        reader.results = list(reader._static_results)
        stamp_eigen_data(fields, self.eigen_data)
        if fields:
            self.property_fields_taken = True
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
        from ada.fem.formats.sesam.results.assembly import assemble_meshes, placements

        sin, rows, merge = plan_sin_read(sin_file, super_element)
        # Every case of every RV card is read in turn: group each card's records by case once.
        sin.index_cases = True
        name_path = pathlib.Path(str(sin.path if sin.path is not None else sin_file))
        if merge is None:
            _refuse_selected_without_mesh(sin)
            part, header = _open_part(sin, None, name_path, complex_phase)
            return cls._build(header, [part], None)

        # One SinFile for every superelement: each entry is decoded once and kept, and
        # switching the active one is a lookup.
        parts, headers = [], []
        for row in merge:
            sin.select_super_element(row)
            part, head = _open_part(sin, row.iref, name_path, complex_phase)
            parts.append(part)
            headers.append(head)
        assembled = assemble_meshes(merge, headers, placements(rows), name=name_path.stem, results_file_path=sin.path)
        return cls._build(assembled.result, parts, assembled)

    @classmethod
    def _build(cls, header, parts, assembled) -> SinFieldStream:
        steps = sorted(set().union(*(p.stored | set(p.unstored) for p in parts)))
        cards = [c for c in _RV_TYPE_NAMES if any(c in p.cards for p in parts)]
        return cls(header, steps, cards, parts, assembled)

    def iter_chunks(self) -> Iterator[tuple[tuple[int, int], list]]:
        need_super_element_field = self._assembled is not None
        for step in self.steps:
            for rank, card in enumerate(self.cards):
                per_part = [p.load(step, card) for p in self._parts]
                if self._assembled is None:
                    fields = per_part[0]
                else:
                    fields = self._assembled.merge_fields(per_part)
                    if need_super_element_field and fields:
                        fields += self._assembled.super_element_fields(fields)
                        need_super_element_field = False
                del per_part
                for f in fields:
                    f._mesh = self.header.mesh
                yield (step, rank), fields
                del fields

    def close(self) -> None:
        self._parts[0].sin.close()

    def __enter__(self) -> SinFieldStream:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _open_part(sin: SinFile, iref, name_path: pathlib.Path, complex_phase: float) -> tuple[_Part, FEAResult]:
    """The active superelement's step-invariant blocks read, and its result without fields."""
    from ada.fem.formats.sesam.results.read_sif import Sif2Mesh, read_lis_eigen_data

    reader = SinReader(sin=sin, complex_phase=complex_phase)
    reader.load_step(None, cards=set())
    header = Sif2Mesh(reader).convert(name_path)
    attach_sesam_metadata(header, reader, sin)
    stored = reader.stored_steps()
    unstored = {i: r for i, r in reader.combination_terms().items() if r and i not in stored}
    cards = {c for c in _RV_TYPE_NAMES if c in sin.type_blocks}
    part = _Part(sin, iref, reader, header.mesh, cards, stored, unstored, read_lis_eigen_data(name_path))
    return part, header
