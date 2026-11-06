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

#: The defaults of :func:`shell_von_mises`'s ``same_thickness`` and ``same_normal_deg``: the elements at a node
#: are averaged together only if each agrees with the node's first element on thickness (within this fraction)
#: and normal (within this angle), the reference postprocessor's rule for its nodal averages.
SAME_THICKNESS = 0.1
SAME_NORMAL_DEG = 5.0


def _von_mises(s: np.ndarray) -> np.ndarray:
    """Plane-stress von Mises of ``[..., (SIGXX, SIGYY, TAUXY)]``."""
    return np.sqrt(s[..., 0] ** 2 + s[..., 1] ** 2 - s[..., 0] * s[..., 1] + 3 * s[..., 2] ** 2)


def _shell_corners(result: FEAResult, cases: Iterable[int] | None):
    """Every shell corner's stresses per case: ``{case: (element, node, bottom (n, 3), top (n, 3))}``.

    From ``SHELL_STRESS`` (rows ``[elem, point, SIGXX, SIGYY, TAUXY]``, the bottom surface then the top, points
    in :data:`SURFACE_CORNERS` order), in each element's own axes: elements ascending, corners in connectivity
    order."""
    from ada.fem.shapes.definitions import ShellShapes

    from .field_data import FieldRole

    corners = {
        int(e): [int(n) for n in nodes]
        for block in result.mesh.elements
        if isinstance(block.elem_info.type, ShellShapes)
        for e, nodes in zip(block.identifiers, block.node_refs)
    }
    out = {}
    for case, fields in result.case_fields(FieldRole.SHELL_STRESS, cases=cases).items():
        element, node, bottom, top = [], [], [], []
        if fields:
            rows = np.vstack([np.asarray(f.values, dtype=float) for f in fields])
            rows = rows[np.lexsort((rows[:, 1], rows[:, 0]))]
            starts = np.flatnonzero(np.r_[True, rows[1:, 0] != rows[:-1, 0]])
            for lo, hi in zip(starts, np.r_[starts[1:], len(rows)]):
                e = int(rows[lo, 0])
                nodes = corners.get(e)
                if nodes is None or len(nodes) not in SURFACE_CORNERS:
                    continue
                surface = SURFACE_CORNERS[len(nodes)]
                n = len(surface)
                if hi - lo < 2 * n:
                    continue
                s = rows[lo:hi, 2:5]
                for j, k in enumerate(surface):
                    if k >= 0:
                        element.append(e)
                        node.append(nodes[k])
                        bottom.append(s[j])
                        top.append(s[n + j])
        out[int(case)] = (
            np.asarray(element, dtype=int),
            np.asarray(node, dtype=int),
            np.asarray(bottom, dtype=float).reshape(-1, 3),
            np.asarray(top, dtype=float).reshape(-1, 3),
        )
    return out


def _into(s: np.ndarray, ex: np.ndarray, ey: np.ndarray) -> np.ndarray:
    """In-plane stresses ``(n, 3)`` in other axes: ``ex`` / ``ey`` ``(n, 2)`` are each row's own x and y axes
    in the other axes."""
    sxx, syy, txy = s[:, 0], s[:, 1], s[:, 2]
    return np.stack(
        (
            ex[:, 0] ** 2 * sxx + 2 * ex[:, 0] * ey[:, 0] * txy + ey[:, 0] ** 2 * syy,
            ex[:, 1] ** 2 * sxx + 2 * ex[:, 1] * ey[:, 1] * txy + ey[:, 1] ** 2 * syy,
            ex[:, 0] * ex[:, 1] * sxx + (ex[:, 0] * ey[:, 1] + ey[:, 0] * ex[:, 1]) * txy + ey[:, 0] * ey[:, 1] * syy,
        ),
        axis=1,
    )


def shell_von_mises(
    result: FEAResult,
    cases: Iterable[int] | None = None,
    *,
    same_thickness: float = SAME_THICKNESS,
    same_normal_deg: float = SAME_NORMAL_DEG,
) -> dict[int, dict[int, float]]:
    """``{case: {node: von Mises stress}}`` of the plating at its nodes, in the result's stress unit (Pa).

    The reference postprocessor's nodal average, from ``SHELL_STRESS`` whichever solver wrote it: at a node the
    stresses of the elements meeting there are averaged, surface by surface, von Mises is taken of each
    average, and the larger surface counts. A node is averaged only where at least two elements meet and every
    one agrees with the first on thickness (``same_thickness``, a fraction of the first's) and normal
    (``same_normal_deg``, degrees): a plate's free edge and a junction of plates (a girder's web on its flange)
    have no value. The defaults (:data:`SAME_THICKNESS`, :data:`SAME_NORMAL_DEG`) are the postprocessor's;
    wider limits average across more junctions, ``same_normal_deg=90`` across every one that is not folded
    back. The stresses are
    turned into the first element's axes before they are averaged, so the value does not depend on how a
    solver orients its elements' axes in their plane. On a Sesam SIN, with the defaults, it is the reader's own
    nodal VONMISES.
    """
    if not same_thickness >= 0 or not 0 <= same_normal_deg <= 180:
        raise ValueError(
            f"same_thickness must be >= 0 and same_normal_deg in [0, 180], got {same_thickness} and {same_normal_deg}"
        )
    mesh = result.mesh
    frames = {int(k): np.asarray(v, dtype=float) for k, v in (mesh.element_frames or {}).items()}
    thickness = {int(k): float(v) for k, v in (mesh.thickness or {}).items()}
    missing = np.full((3, 3), np.nan)
    cos_limit = np.cos(np.deg2rad(same_normal_deg))
    out: dict[int, dict[int, float]] = {}
    for case, (element, node, bottom, top) in _shell_corners(result, cases).items():
        if not len(node):
            out[case] = {}
            continue
        order = np.argsort(node, kind="stable")
        element, node, bottom, top = element[order], node[order], bottom[order], top[order]
        frame = np.stack([frames.get(int(e), missing) for e in element])
        thick = np.array([thickness.get(int(e), np.nan) for e in element])
        groups, starts, counts = np.unique(node, return_index=True, return_counts=True)
        first = np.repeat(starts, counts)
        ref = frame[first]
        with np.errstate(invalid="ignore"):
            same_thickness = np.isfinite(thick) & (
                np.abs(thick - thick[first]) <= same_thickness * np.maximum(np.abs(thick[first]), 1e-30)
            )
            same_normal = np.einsum("ij,ij->i", frame[:, 2], ref[:, 2]) >= cos_limit
        averaged = np.logical_and.reduceat(same_thickness & same_normal, starts) & (counts >= 2)

        # Each element's x and y axes in the first element's: the two are coplanar within the normal limit.
        ex = np.stack(
            (np.einsum("ij,ij->i", frame[:, 0], ref[:, 0]), np.einsum("ij,ij->i", frame[:, 0], ref[:, 1])), axis=1
        )
        with np.errstate(invalid="ignore", divide="ignore"):
            ex /= np.linalg.norm(ex, axis=1)[:, None]
            ey = np.stack((-ex[:, 1], ex[:, 0]), axis=1)
            mean_bottom = np.add.reduceat(_into(bottom, ex, ey), starts) / counts[:, None]
            mean_top = np.add.reduceat(_into(top, ex, ey), starts) / counts[:, None]
            vm = np.maximum(_von_mises(mean_bottom), _von_mises(mean_top))
        out[case] = {int(n): float(v) for n, v, ok in zip(groups, vm, averaged) if ok}
    return out


def shell_von_mises_corners(result: FEAResult, cases: Iterable[int] | None = None) -> dict[int, dict[int, float]]:
    """``{case: {node: von Mises stress}}`` with von Mises taken first: at every element corner, the larger
    surface, then the mean over the node's elements, at every node, free edges and junctions included.

    Higher than :func:`shell_von_mises` where the stress changes fast (a point load, a junction): it shows the
    hot spots, not the postprocessor's figure. Von Mises needs no common axes, so each element's own are used.
    """
    out: dict[int, dict[int, float]] = {}
    for case, (_, node, bottom, top) in _shell_corners(result, cases).items():
        if not len(node):
            out[case] = {}
            continue
        vm = np.maximum(_von_mises(bottom), _von_mises(top))
        order = np.argsort(node, kind="stable")
        groups, starts, counts = np.unique(node[order], return_index=True, return_counts=True)
        mean = np.add.reduceat(vm[order], starts) / counts
        out[case] = {int(n): float(v) for n, v in zip(groups, mean)}
    return out


#: The quantities ``FEAResult.derived_field`` and ``show(derived=...)`` know: ``{name: (components, category,
#: compute)}``, compute giving ``{case: {node: values}}``.
DERIVED: dict[str, tuple[list[str], str, Callable]] = {
    "von_mises": (["VON_MISES"], "stress", shell_von_mises),
    "von_mises_corners": (["VON_MISES"], "stress", shell_von_mises_corners),
}


def _options_of(name: str) -> list[str]:
    """The keyword options the derived quantity ``name`` takes."""
    import inspect

    parameters = inspect.signature(DERIVED[name][2]).parameters.values()
    return [p.name for p in parameters if p.kind is inspect.Parameter.KEYWORD_ONLY]


def check_options(name: str, options: dict) -> None:
    """Refuse an unknown derived quantity, or an option it does not take."""
    if name not in DERIVED:
        raise KeyError(f"no derived quantity {name!r}; known: {sorted(DERIVED)}")
    unknown = sorted(set(options) - set(_options_of(name)))
    if unknown:
        raise TypeError(f"{name!r} takes no option {', '.join(unknown)}; its options: {_options_of(name) or 'none'}")


def requests(derived) -> list[tuple[str, dict]]:
    """``[(name, options)]`` of a ``derived=`` argument: a name, a list of names (or of ``(name, options)``), or ``{name: {option: value}}``."""
    if not derived:
        return []
    if isinstance(derived, str):
        derived = [derived]
    items = (
        derived.items()
        if isinstance(derived, dict)
        else (item if isinstance(item, tuple) and len(item) == 2 else (item, {}) for item in derived)
    )
    out = []
    for name, options in items:
        name, options = str(name), dict(options or {})
        if any(name == n for n, _ in out):
            raise ValueError(f"derived quantity {name!r} asked for twice")
        check_options(name, options)
        out.append((name, options))
    return out


def derived_nodal(
    result: FEAResult, name: str, cases: Iterable[int] | None = None, **options
) -> dict[int, dict[int, float]]:
    """``{case: {node: value}}`` of the derived quantity ``name`` (:data:`DERIVED`), with its keyword
    ``options`` (``von_mises``: ``same_thickness``, ``same_normal_deg``)."""
    check_options(name, options)
    return DERIVED[name][2](result, cases, **options)


class WithDerivedFields:
    """A stream reader of a result's file with derived nodal fields beside the file's own, for the viewer bake.

    Each derived field has a step per stored case, with the step values of the file's displacements (its first
    nodal field otherwise), so it sits on the same load cases in the viewer; a node with no value is NaN.
    """

    def __init__(self, reader, result: FEAResult, derived):
        self._reader = reader
        self._result = result
        self._options = dict(requests(derived))
        self._names = list(self._options)

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
        by_case = derived_nodal(self._result, field_name, **self._options[field_name])
        for i, case in enumerate(self._result.cases.stored):
            values = np.full((n_points, spec.n_components), np.nan, dtype=float)
            for node, value in by_case.get(int(case), {}).items():
                row = index.get(int(node))
                if row is not None:
                    values[row, :] = value
            yield StepValues(step_index=i, step_value=spec.step_values[i], values=values)


__all__ = [
    "DERIVED",
    "SAME_NORMAL_DEG",
    "SAME_THICKNESS",
    "SURFACE_CORNERS",
    "WithDerivedFields",
    "check_options",
    "derived_nodal",
    "requests",
    "shell_von_mises",
    "shell_von_mises_corners",
]
