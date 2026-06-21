"""CapacityManager — orchestrate capacity-model reconstruction from a SIN.

Public entry point. Reads a Sesam ``.SIN`` via the existing
:func:`~ada.fem.formats.sesam.results.read_sin.read_sin_file`, assembles panel
groups from a :class:`~ada.fem.capacity.sources.PanelGroupSource`, and builds
neutral :class:`~ada.fem.capacity.model.CapacityModel` objects with a
:class:`~ada.fem.capacity.stiffened_plate.CapacityModelBuilder`.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable

from ada.fem.capacity import serialize
from ada.fem.capacity.extract import AuxRecords
from ada.fem.capacity.model import CapacityModel, ResolvedCase, write_neutral_json
from ada.fem.capacity.sources import PanelGroupSource
from ada.fem.capacity.stiffened_plate import CapacityModelBuilder, StiffenedPlateBuilder


class CapacityManager:
    def __init__(
        self,
        sin_path: str | pathlib.Path,
        source: PanelGroupSource,
        *,
        builder: CapacityModelBuilder | None = None,
    ) -> None:
        self.sin_path = pathlib.Path(sin_path)
        self.source = source
        self.builder = builder or StiffenedPlateBuilder()
        self._aux: AuxRecords | None = None
        self._mesh = None
        self._models: list[CapacityModel] | None = None

    # ── construction ──────────────────────────────────────────────────
    @classmethod
    def from_sin(
        cls,
        sin_path: str | pathlib.Path,
        source: PanelGroupSource,
        *,
        builder: CapacityModelBuilder | None = None,
    ) -> "CapacityManager":
        return cls(sin_path, source, builder=builder)

    @property
    def mesh(self):
        if self._mesh is None:
            from ada.fem.formats.sesam.results.read_sin import read_sin_file

            # Step 1 is enough for geometry; the mesh is step-invariant.
            self._mesh = read_sin_file(self.sin_path, step=1).mesh
        return self._mesh

    @property
    def aux(self) -> AuxRecords:
        if self._aux is None:
            self._aux = AuxRecords.from_sin(self.sin_path)
        return self._aux

    # ── capacity models ───────────────────────────────────────────────
    def capacity_models(
        self,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[CapacityModel]:
        """Build (and cache) the capacity models.

        ``on_progress(completed, total)`` is called once per built model so a
        caller can drive a progress bar without this package depending on it.
        """
        if self._models is None:
            groups = list(self.source.groups(self.mesh, self.aux))
            total = len(groups)
            models: list[CapacityModel] = []
            for index, group in enumerate(groups, start=1):
                models.append(self.builder.build(self.mesh, self.aux, group))
                if on_progress is not None:
                    on_progress(index, total)
            self._models = models
        elif on_progress is not None:
            count = len(self._models)
            on_progress(count, count)
        return self._models

    # ── resolved design variables (Phase 3) ───────────────────────────
    def resolve_cases(
        self,
        result_cases: list[int] | None = None,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[ResolvedCase]:
        from ada.fem.capacity.stress_resolve import resolve_cases

        return resolve_cases(
            self.sin_path,
            self.capacity_models(),
            result_cases=result_cases,
            on_progress=on_progress,
        )

    # ── serialization ─────────────────────────────────────────────────
    def to_genie_json(self, path: str | pathlib.Path) -> None:
        serialize.write_genie_json(path, self.capacity_models())

    def to_neutral_json(self, path: str | pathlib.Path, result_cases: list[int] | None = None) -> None:
        write_neutral_json(path, self.capacity_models(), self.resolve_cases(result_cases))
