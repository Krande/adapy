"""A Sesam superelement assembly (.SIN) read as input models: one per superelement, or all merged.

SESTRA echoes every superelement's input deck into the Results Interface File it writes for an
assembly, so each first-level superelement with a mesh can be read like a ``.FEM`` of its own
(:func:`read_superelement`), or all of them together as the one model the top level makes of them
(:func:`read_merged`).

The merge places each superelement with its HSUPTRAN transform and numbers nodes and elements the
way the results assembly does (``results.assembly.id_bases_for``: type number x 10^k), so ids stay
unique and readable. Only translations are applied: a rotation would have to turn every vector the
deck holds in the superelement's system (beam orientations, eccentricities, local systems), and a
file that asks for one is refused rather than written with the wrong geometry.
"""

from __future__ import annotations

import dataclasses
import pathlib
import tempfile
from typing import TYPE_CHECKING

import numpy as np

from ada.config import logger

if TYPE_CHECKING:
    from ada import Assembly
    from ada.fem.formats.sesam.results.sin_reader import SuperElementInfo


class SinAssemblyError(ValueError):
    """The SIN cannot be read the way it was asked for; the message says why and what to do."""


@dataclasses.dataclass(frozen=True)
class SinLayout:
    """What a SIN holds: its superelements and which of them can be read as input decks."""

    rows: tuple[SuperElementInfo, ...]
    #: First-level superelements with a mesh, in the file's order.
    meshes: tuple[SuperElementInfo, ...]
    #: The top level of the hierarchy, None for a file without one.
    top: SuperElementInfo | None
    #: Superelements with a mesh below the first level, which no mode here reads.
    deeper: tuple[SuperElementInfo, ...]

    @property
    def is_assembly(self) -> bool:
        """More than one superelement with a mesh: converting the file means choosing or combining."""
        return len(self.meshes) > 1

    def describe(self) -> str:
        return ", ".join(r.label for r in self.meshes)


def sin_layout(path: str | pathlib.Path) -> SinLayout:
    """The superelements of the SIN at ``path`` (the file is opened and closed again)."""
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    with open_sin(path) as sin:
        rows = sin.hierarchy()
    top = next((r for r in rows if r.is_top and r.children), None)
    meshes = tuple(r for r in rows if r.has_mesh and r.level == 1 and r.iref is not None)
    deeper = tuple(r for r in rows if r.has_mesh and r.level != 1)
    return SinLayout(rows, meshes, top, deeper)


def resolve_specs(path: str | pathlib.Path, layout: SinLayout, specs: list[str]) -> list[SuperElementInfo]:
    """The first-level superelements ``specs`` name (``12``, ``12.1``, ``SEL12.IND1``), in order."""
    from ada.fem.formats.sesam.results.sin_reader import SuperElementError, open_sin

    chosen: list[SuperElementInfo] = []
    with open_sin(path) as sin:
        for spec in specs:
            try:
                row = sin.resolve_super_element(spec)
            except SuperElementError as exc:
                raise SinAssemblyError(str(exc)) from None
            if row not in layout.meshes:
                raise SinAssemblyError(f"superelement {row.label} is not a first-level superelement with a mesh")
            if row not in chosen:
                chosen.append(row)
    return chosen


def _read_part(path: pathlib.Path, row: SuperElementInfo | None, name: str):
    """The deck of ``row`` (None: the file's only one) as a Part with nodes."""
    from ada.fem.formats.sesam.results.export_fem import read_deck_from_sin

    with tempfile.TemporaryDirectory(prefix="ada-sin-") as tmp:
        asm = read_deck_from_sin(path, tmp, name, super_element=row)
    part = next(p for p in asm.get_all_parts_in_assembly(include_self=False) if len(p.fem.nodes) > 0)
    if row is not None:
        # What the file says this superelement is, rather than what its echoed IDENT happens to hold.
        part.fem.metadata["sesam_seltyp"] = row.seltyp
    return part


def read_superelement(path: str | pathlib.Path, row: SuperElementInfo | None = None) -> Assembly:
    """One superelement of the SIN as its own model. ``row`` None reads the file's only one."""
    from ada import Assembly

    path = pathlib.Path(path)
    part = _read_part(path, row, "T1" if row is None else f"SE{row.seltyp}")
    return Assembly("TempAssembly") / part


def _translation(row: SuperElementInfo, placement: np.ndarray) -> np.ndarray:
    if not np.allclose(placement[:3, :3], np.eye(3), atol=1e-9):
        raise SinAssemblyError(
            f"superelement {row.label} is placed with a rotation, which --merge does not apply: it would have to "
            "turn the beam orientations, eccentricities and local systems with it. Convert the superelements "
            "one by one (leave --merge out) instead."
        )
    return np.asarray(placement[:3, 3], dtype=float)


def read_merged(path: str | pathlib.Path, layout: SinLayout) -> Assembly:
    """Every first-level superelement placed and merged into one single-part model.

    Its FEM carries ``sesam_seltyp`` of the top level, which is the number the merged deck is
    written with.
    """
    from ada import Assembly
    from ada.fem.concat import single_part_assembly
    from ada.fem.formats.sesam.results.assembly import id_bases_for, placements
    from ada.fem.formats.sesam.write.write_bcs import RETAINED_BY_NODE_KEY

    path = pathlib.Path(path)
    if layout.top is None:
        raise SinAssemblyError("the SIN has no hierarchy, so there is no top level to merge the superelements into")

    placement = placements(layout.rows)
    moves = [_translation(row, placement[row.ihref]) for row in layout.meshes]

    asm = Assembly(f"SE{layout.top.seltyp}")
    parts = []
    for row, move in zip(layout.meshes, moves):
        part = _read_part(path, row, f"SE{row.seltyp}")
        if np.any(move):
            part.fem.nodes.move(move=tuple(float(x) for x in move))
        parts.append(part)
        asm.add_part(part)

    largest = max(_largest_id(p) for p in parts)
    bases = id_bases_for([r.seltyp for r in layout.meshes], largest)
    offsets = {p.name: (base, base) for p, base in zip(parts, bases)}
    # Dofs retained on some of a node's dofs only, under the merged node ids (see write_bcs).
    retained = {
        nid + base: dofs
        for part, base in zip(parts, bases)
        for nid, dofs in (part.fem.metadata.get(RETAINED_BY_NODE_KEY) or {}).items()
    }
    merged = single_part_assembly(asm, id_offsets=offsets)

    for part in merged.get_all_parts_in_assembly(include_self=True):
        if len(part.fem.nodes) > 0:
            part.fem.metadata["sesam_seltyp"] = layout.top.seltyp
            if retained:
                part.fem.metadata[RETAINED_BY_NODE_KEY] = retained
    logger.info(
        "merged %d superelements (%s) into %s; ids are prefixed by type number",
        len(parts),
        layout.describe(),
        layout.top.label,
    )
    return merged


def _largest_id(part) -> int:
    store = part.fem.nodes.store
    ids = [int(store.node_ids.max())] if len(store.node_ids) else [0]
    ids += [int(blk.el_ids.max()) for blk in store.blocks.values() if len(blk.el_ids)]
    return max(ids)
