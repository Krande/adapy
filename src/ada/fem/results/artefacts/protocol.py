"""The FEAStreamReader protocol every result-format stream reader implements."""

from __future__ import annotations

from typing import Iterator, Protocol

from .history import HistoryRecords
from .specs import (
    ElementFieldSpec,
    ElementStepValues,
    FieldSpec,
    MeshGeometry,
    SolidBeamMesh,
    StepValues,
)


class FEAStreamReader(Protocol):
    """Per-format streaming reader interface.

    The bake calls ``read_mesh_geometry`` once, then for each spec in
    ``field_specs`` calls ``iter_field_steps``. The reader is
    responsible for keeping any underlying file handle open across
    those calls.

    Element-field methods (``element_field_specs`` /
    ``iter_element_field_steps``) are optional: a reader that yields
    no element fields can return an empty list. The bake skips the
    element-field emission loop entirely when no specs come back.
    """

    def read_mesh_geometry(self) -> MeshGeometry: ...

    def field_specs(self) -> list[FieldSpec]: ...

    def iter_field_steps(self, field_name: str) -> Iterator[StepValues]: ...

    def element_field_specs(self) -> list[ElementFieldSpec]: ...

    def iter_element_field_steps(self, spec: ElementFieldSpec) -> Iterator[ElementStepValues]: ...

    def try_solid_beams(self) -> "SolidBeamMesh | None":
        """Optional: tessellate beam elements as 3D extruded solids.

        Readers that have section + axis info per beam element (SIF
        via the FEAResult adapter, future readers that carry similar
        metadata) return a :class:`SolidBeamMesh`. Readers without it
        (native RMED, FRD) return ``None`` — the bake then skips beam-
        solid emission and the manifest carries no ``beam_solids_url``.
        """
        ...

    def try_history_records(self) -> "HistoryRecords | None":
        """Optional: time-series history output at monitored points.

        Abaqus surfaces this via HistOutput (one row per sample); Sesam
        and Code_Aster have analogous concepts pending. Readers that
        have no history data return ``None`` and the manifest omits
        the ``history`` section. The bake also tolerates AttributeError
        from readers that pre-date this method.
        """
        ...

    def close(self) -> None: ...
