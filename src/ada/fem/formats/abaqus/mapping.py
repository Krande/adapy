"""Which adapy FEA object is which Abaqus construct -- one table, read by the reader AND the writer.

The reader and the writer used to each carry their own idea of the correspondence: the reader a
table from Abaqus element types to adapy shapes, the writer a separate default per shape; the
writer a map from boundary-condition types to CAE's names, the reader whatever string the
deck's comment held. Two tables drift, and a drift is a round trip that changes the model -- an
adapy boundary condition that reads back as a different ``BcTypes`` value, an element written as
a type the reader files under another shape.

Each :class:`Correspondence` here is the single answer for one kind of construct. A row pairs an
adapy value with the Abaqus spelling the writer emits, plus the other spellings the reader
accepts for it (CAE's comment text, the Keywords Guide's names, what older adapy versions
wrote). Reading goes through :meth:`Correspondence.from_abaqus` and writing through
:meth:`Correspondence.to_abaqus`, so a round trip is a fixed point by construction; the
round-trip tests keep it one.

Tables are built on first use (:func:`functools.cache`): they name adapy's enums, and
``ada.fem`` is still importing when this package is.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Generic, Iterator, Mapping, TypeVar

from .read.lexer import normalize

T = TypeVar("T")


class UnmappedAbaqusValue(KeyError):
    """A value one side of a :class:`Correspondence` has no row for."""


@dataclass(frozen=True)
class Row(Generic[T]):
    """One adapy value and its Abaqus spellings.

    ``abaqus`` is what the writer emits; ``aliases`` are further spellings the reader accepts.
    ``writable`` is False for a row that exists only so a deck reads -- a construct adapy
    understands but cannot write back yet.
    """

    ada: T
    abaqus: str
    aliases: tuple[str, ...] = ()
    writable: bool = True


@dataclass(frozen=True)
class Correspondence(Generic[T]):
    """The adapy <-> Abaqus table for one kind of construct.

    ``canonical`` maps adapy values that are synonyms of another (an enum member kept for
    compatibility) onto the value that has the row.
    """

    name: str
    rows: tuple[Row[T], ...]
    canonical: Mapping[Any, Any] = field(default_factory=dict)
    _by_abaqus: dict = field(init=False, repr=False, compare=False)
    _by_ada: dict = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_abaqus: dict[str, Row[T]] = {}
        by_ada: dict[Any, Row[T]] = {}
        for row in self.rows:
            for spelling in (row.abaqus, *row.aliases):
                key = normalize(spelling)
                if key in by_abaqus and by_abaqus[key] is not row:
                    raise ValueError(f"{self.name}: Abaqus {spelling!r} is listed for two adapy values")
                by_abaqus[key] = row
            if row.ada in by_ada:
                raise ValueError(f"{self.name}: adapy {row.ada!r} has two rows")
            by_ada[row.ada] = row
        object.__setattr__(self, "_by_abaqus", by_abaqus)
        object.__setattr__(self, "_by_ada", by_ada)

    def from_abaqus(self, text: str) -> T:
        """The adapy value for any accepted spelling of ``text``, in any case."""
        try:
            return self._by_abaqus[normalize(text)].ada
        except KeyError:
            raise UnmappedAbaqusValue(f"{self.name}: no adapy value for Abaqus {text!r}") from None

    def to_abaqus(self, value: T) -> str:
        """The spelling the writer emits for ``value``."""
        row = self._by_ada.get(self.canonical.get(value, value))
        if row is None or not row.writable:
            raise UnmappedAbaqusValue(f"{self.name}: adapy {value!r} has no Abaqus form the writer can emit")
        return row.abaqus

    def knows(self, text: str) -> bool:
        return normalize(text) in self._by_abaqus

    def __iter__(self) -> Iterator[Row[T]]:
        return iter(self.rows)


# ── boundary conditions ─────────────────────────────────────────────────────────────────────


@functools.cache
def bc_types() -> Correspondence:
    """``BcTypes`` <-> the type Abaqus/CAE names in the comment above ``*Boundary``.

    The keyword itself is type-free; CAE writes ``** Name: BC-1 Type: Displacement/Rotation``,
    which is where the reader takes the type from and where the writer puts it. ``DISPL_ROT``
    and ``VELOCITY_ANGULAR`` are CAE spellings that leaked into the enum: they write as the
    canonical value they mean, and a deck reads back as that canonical value.
    """
    from ada.fem.constraints import BcTypes as B

    return Correspondence(
        "boundary condition types",
        (
            Row(B.DISPL, "Displacement/Rotation", aliases=("displacement",)),
            Row(B.VELOCITY, "Velocity/Angular velocity", aliases=("velocity",)),
            Row(B.CONN_DISPL, "Connector displacement", aliases=("connector_displacement",)),
            Row(B.CONN_VEL, "Connector velocity", aliases=("connector_velocity",)),
            Row(B.ENCASTRE, "Symmetry/Antisymmetry/Encastre"),
        ),
        canonical={B.DISPL_ROT: B.DISPL, B.VELOCITY_ANGULAR: B.VELOCITY},
    )


# ── element types ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ElementTypes:
    """adapy element shapes <-> Abaqus element types.

    Not one-to-one, so not a :class:`Correspondence`: many Abaqus types share one shape (``S3``,
    ``S3R``, ``CPS3``, ``M3D3`` are all a 3-node triangle). The shape is what adapy models; the
    TYPE is the formulation, and a round trip must keep it -- an element read as ``CPS3`` (plane
    stress) and written back as the shape's default ``S3`` (a shell) is a different analysis.

    The data is :data:`.elem_shapes.ada_to_abaqus_format`; this is the one place both the reader
    and the writer ask.
    """

    by_shape: Mapping[Any, tuple[str, ...]]

    def shape_of(self, abaqus_type: str):
        """The adapy shape of an Abaqus element type."""
        from .elem_shapes import UnsupportedAbaqusElementType

        key = normalize(abaqus_type)
        for shape, types in self.by_shape.items():
            if key in types:
                return shape
        raise UnsupportedAbaqusElementType(f'Element type "{abaqus_type}" has no adapy shape yet')

    def accepts(self, shape, abaqus_type) -> bool:
        """Is ``abaqus_type`` a formulation of ``shape``?"""
        return isinstance(abaqus_type, str) and normalize(abaqus_type) in self.by_shape.get(shape, ())

    def write_type(self, elem, defaults, target: str = "abaqus") -> str:
        """The Abaqus type to write ``elem`` as (``ada.fem.formulations.resolve``): the caller's
        formulation rules, else the Abaqus type it was read as when that is a formulation of its
        shape, else ``defaults`` (the writer's configurable ``AbaqusDefaultElemTypes``)."""
        from ada.fem.formulations import resolve

        return normalize(
            resolve(
                elem,
                target,
                default=defaults.get_element_type,
                accepts=self.accepts,
                stage=f"{target} writer",
            )
        )


@functools.cache
def element_types() -> ElementTypes:
    from .elem_shapes import ada_to_abaqus_format

    return ElementTypes({shape: tuple(normalize(t) for t in types) for shape, types in ada_to_abaqus_format.items()})
