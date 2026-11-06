"""Quantities worked out from a result's role fields, the same from every format.

A result read case by case (``Assembly.to_fem(..., lazy=True)``, ``ada.from_fem_res(path, lazy=True)``) holds
its fields in adapy's canonical layout (``FieldRole``), so a quantity derived from them -- the von Mises stress
of the plating -- is worked out once, here, for every solver. ``FEAResult.derived_field`` gives one per case,
and ``FEAResult.show(derived=[...])`` puts them in the viewer beside the file's own fields.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .common import FEAResult

#: Result points per shell surface as corner indices, -1 the centroid (``FieldRole.SHELL_STRESS``): a 4-node
#: shell's ``n1 n2 c n4 n3``, a 3-node one's ``n1 n2 c n3``.
SURFACE_CORNERS = {4: (0, 1, -1, 3, 2), 3: (0, 1, -1, 2)}


def shell_von_mises(result: FEAResult, cases: Iterable[int] | None = None) -> dict[int, dict[int, float]]:
    """``{case: {node: von Mises stress}}`` of the shells, in the result's stress unit (Pa).

    From ``SHELL_STRESS``: the plane-stress von Mises ``sqrt(sx^2 + sy^2 - sx sy + 3 txy^2)`` at each corner on
    both surfaces, the larger of the two, and at a node the mean over its elements. Von Mises needs no common
    axes, so each element's own are used as they are.
    """
    from ada.fem.shapes.definitions import ShellShapes

    from .field_data import FieldRole

    corners = {
        int(e): [int(n) for n in nodes]
        for block in result.mesh.elements
        if isinstance(block.elem_info.type, ShellShapes)
        for e, nodes in zip(block.identifiers, block.node_refs)
    }
    out: dict[int, dict[int, float]] = {}
    for case, fields in result.case_fields(FieldRole.SHELL_STRESS, cases=cases).items():
        if not fields:
            out[case] = {}
            continue
        rows = np.vstack([np.asarray(f.values, dtype=float) for f in fields])
        sums: dict[int, list[float]] = {}
        order = np.argsort(rows[:, 0], kind="stable")
        rows = rows[order]
        starts = np.flatnonzero(np.r_[True, rows[1:, 0] != rows[:-1, 0]])
        for lo, hi in zip(starts, np.r_[starts[1:], len(rows)]):
            element = int(rows[lo, 0])
            nodes = corners.get(element)
            if nodes is None or len(nodes) not in SURFACE_CORNERS:
                continue
            surface = SURFACE_CORNERS[len(nodes)]
            s = rows[lo:hi][np.argsort(rows[lo:hi, 1], kind="stable"), 2:5]
            vm = np.sqrt(s[:, 0] ** 2 + s[:, 1] ** 2 - s[:, 0] * s[:, 1] + 3 * s[:, 2] ** 2)
            n = len(surface)
            if len(vm) < 2 * n:
                continue
            for j, k in enumerate(surface):
                if k >= 0:
                    sums.setdefault(nodes[k], []).append(float(max(vm[j], vm[n + j])))
        out[int(case)] = {node: float(np.mean(v)) for node, v in sums.items()}
    return out


#: The quantities ``FEAResult.derived_field`` and ``show(derived=...)`` know: ``{name: (components, category,
#: compute)}``, compute giving ``{case: {node: values}}``.
DERIVED: dict[str, tuple[list[str], str, Callable]] = {
    "von_mises": (["VON_MISES"], "stress", shell_von_mises),
}


def derived_nodal(result: FEAResult, name: str, cases: Iterable[int] | None = None) -> dict[int, dict[int, float]]:
    """``{case: {node: value}}`` of the derived quantity ``name`` (:data:`DERIVED`)."""
    if name not in DERIVED:
        raise KeyError(f"no derived quantity {name!r}; known: {sorted(DERIVED)}")
    return DERIVED[name][2](result, cases)


class WithDerivedFields:
    """A stream reader of a result's file with derived nodal fields beside the file's own, for the viewer bake.

    Each derived field has a step per stored case, with the step values of the file's displacements (its first
    nodal field otherwise), so it sits on the same load cases in the viewer; a node with no value is NaN.
    """

    def __init__(self, reader, result: FEAResult, names: Iterable[str]):
        self._reader = reader
        self._result = result
        self._names = [str(n) for n in names]
        for n in self._names:
            if n not in DERIVED:
                raise KeyError(f"no derived quantity {n!r}; known: {sorted(DERIVED)}")

    def __getattr__(self, item):
        return getattr(self._reader, item)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def _points(self) -> tuple[int, dict[int, int]]:
        geometry = self._reader.read_mesh_geometry()
        n_points = int(geometry.points.shape[0])
        labels = geometry.node_labels
        if labels is not None:
            return n_points, {int(label): i for i, label in enumerate(labels)}
        return n_points, {i + 1: i for i in range(n_points)}

    def field_specs(self):
        from .artefacts.specs import FieldSpec

        specs = list(self._reader.field_specs())
        nodal = [s for s in specs if s.support == "nodal"]
        base = next((s for s in nodal if s.category == "displacement"), nodal[0] if nodal else None)
        stored = list(self._result.cases.stored)
        steps = (
            list(base.step_values) if base is not None and base.n_steps == len(stored) else [float(c) for c in stored]
        )
        n_points, _ = self._points()
        for name in self._names:
            components, category, _ = DERIVED[name]
            specs.append(
                FieldSpec(
                    name=name,
                    components=list(components),
                    n_steps=len(stored),
                    n_points=n_points,
                    support="nodal",
                    step_values=steps,
                    category=category,
                    analysis_kind=getattr(base, "analysis_kind", None),
                )
            )
        return specs

    def iter_field_steps(self, field_name: str):
        if field_name not in self._names:
            yield from self._reader.iter_field_steps(field_name)
            return
        from .artefacts.specs import StepValues

        n_points, index = self._points()
        spec = next(s for s in self.field_specs() if s.name == field_name)
        by_case = derived_nodal(self._result, field_name)
        for i, case in enumerate(self._result.cases.stored):
            values = np.full((n_points, spec.n_components), np.nan, dtype=float)
            for node, value in by_case.get(int(case), {}).items():
                row = index.get(int(node))
                if row is not None:
                    values[row, :] = value
            yield StepValues(step_index=i, step_value=spec.step_values[i], values=values)


__all__ = ["DERIVED", "SURFACE_CORNERS", "WithDerivedFields", "derived_nodal", "shell_von_mises"]
