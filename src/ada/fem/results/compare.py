"""Compare two FEA results computed on the same mesh (e.g. different loads or boundary conditions).

Fields are paired by name, step and element type; rows are paired by their key columns
(node id for nodal fields, element id + integration point for element fields), so the two
files need not list them in the same order.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ada.fem.results.field_data import ElementFieldData, NodalFieldData

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult, Mesh

FieldKey = tuple[str, int, str]  # (name, step, element type)

# Sesam stores results as float32, so two runs of the same case can differ in the last bits
# (~1e-7 relative). Differences below this are reported as noise, not as differences.
DEFAULT_RTOL = 1e-5


@dataclass
class FieldDiff:
    """The difference ``other - base`` for one field in one step, summarised per component."""

    name: str
    step: int
    other_step: int
    elem_type: str
    components: list[str]
    num_points: int  # rows present in both results
    num_unmatched: int  # rows present in only one of them
    max_abs_base: np.ndarray  # per component, max |base|
    max_abs_other: np.ndarray  # per component, max |other|
    max_abs_diff: np.ndarray  # per component, max |other - base|
    max_diff_at: list[tuple]  # per component, the row key (node id / (elem id, int point)) of max_abs_diff

    @property
    def rel_diff(self) -> np.ndarray:
        """Per component, ``max |other - base|`` relative to the larger of the two fields' max magnitudes."""
        ref = np.fmax(self.max_abs_base, self.max_abs_other)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(ref > 0, self.max_abs_diff / ref, np.where(self.max_abs_diff > 0, np.inf, 0.0))

    @property
    def worst_component(self) -> int:
        """Index of the component with the largest relative difference (NaN-safe)."""
        rel = np.nan_to_num(self.rel_diff, nan=-1.0, posinf=np.finfo(float).max)
        return int(np.argmax(rel)) if len(rel) else 0

    @property
    def is_identical(self) -> bool:
        return self.num_unmatched == 0 and bool(np.all(np.nan_to_num(self.max_abs_diff) == 0))


@dataclass
class FEAResultComparison:
    base: FEAResult
    other: FEAResult
    step_map: dict[int, int]
    diffs: list[FieldDiff] = field(default_factory=list)
    only_in_base: list[FieldKey] = field(default_factory=list)
    only_in_other: list[FieldKey] = field(default_factory=list)

    def differing(self, rtol: float = DEFAULT_RTOL) -> list[FieldDiff]:
        """The compared fields whose largest relative difference exceeds ``rtol``, worst first."""
        out = [d for d in self.diffs if not d.is_identical and float(d.rel_diff[d.worst_component]) > rtol]
        return sorted(out, key=lambda d: -float(np.nan_to_num(d.rel_diff[d.worst_component], posinf=np.inf)))

    def __repr__(self):
        differing = self.differing()
        worst = ""
        if differing:
            d = differing[0]
            c = d.worst_component
            worst = (
                f", largest: {d.name} step {d.step} {d.components[c]} "
                f"|diff|={d.max_abs_diff[c]:.4g} (rel {d.rel_diff[c]:.2%})"
            )
        return (
            f"FEAResultComparison(base={self.base.name!r}, other={self.other.name!r}, "
            f"steps={len(self.step_map)}, compared={len(self.diffs)}, differing={len(differing)}, "
            f"unmatched={len(self.only_in_base) + len(self.only_in_other)}{worst})"
        )

    def stats(self, rtol: float = DEFAULT_RTOL, max_rows: int | None = 50) -> str:
        """A table of the fields that differ by more than ``rtol`` (relative), worst first.

        One row per field and step, reporting its worst component. ``max_rows=None`` lists all.
        """
        base_names = self.base.step_name_map or {}
        other_names = self.other.step_name_map or {}
        lines = [
            f"Comparison: other={self.other.name!r} minus base={self.base.name!r}",
            f"  steps compared:   {len(self.step_map)}",
        ]
        for b, o in self.step_map.items():
            b_name, o_name = base_names.get(b, ""), other_names.get(o, "")
            label = f"{b} ({b_name})" if b_name else str(b)
            if o != b or o_name != b_name:
                label += f" <-> {o} ({o_name})" if o_name else f" <-> {o}"
            lines.append(f"    {label}")
        differing = self.differing(rtol)
        num_identical = sum(d.is_identical for d in self.diffs)
        lines.append(f"  fields compared:  {len(self.diffs)}")
        lines.append(f"  identical:        {num_identical}")
        lines.append(f"  within rtol:      {len(self.diffs) - num_identical - len(differing)} (rel <= {rtol:g})")
        lines.append(f"  differing:        {len(differing)}")
        if self.only_in_base:
            lines.append(f"  only in base:     {_summarise_keys(self.only_in_base)}")
        if self.only_in_other:
            lines.append(f"  only in other:    {_summarise_keys(self.only_in_other)}")
        if not differing:
            return "\n".join(lines)

        lines.append("")
        rows = [("field", "step", "elem type", "component", "max|base|", "max|other|", "max|diff|", "rel", "at")]
        shown = differing if max_rows is None else differing[:max_rows]
        for d in shown:
            c = d.worst_component
            step = str(d.step) if d.step == d.other_step else f"{d.step}<->{d.other_step}"
            at = d.max_diff_at[c]
            rows.append(
                (
                    d.name,
                    step,
                    d.elem_type,
                    d.components[c],
                    f"{d.max_abs_base[c]:.4g}",
                    f"{d.max_abs_other[c]:.4g}",
                    f"{d.max_abs_diff[c]:.4g}",
                    f"{d.rel_diff[c]:.2%}",
                    ",".join(str(x) for x in at) if at else "",
                )
            )
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for row in rows:
            lines.append("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
        if len(shown) < len(differing):
            lines.append(f"  ... {len(differing) - len(shown)} more (stats(max_rows=None) lists all)")
        return "\n".join(lines)

    def to_fea_result(self) -> FEAResult:
        """The difference ``other - base`` as an FEAResult on the shared mesh (e.g. to ``.show()`` it).

        One difference field per compared field and step: every load case and every load
        combination the base has (a Sesam read materialises the combinations a deck does
        not store, so they are steps like the others). Each component is diffed as it is
        stored, derived ones included: a von Mises difference is the change in von Mises.

        Steps carry the base's numbering and names. Rows present in only one result are
        left out. The model-property fields (``props.*``: material, section, thickness)
        are the base's own, not differenced -- a difference of two material ids means
        nothing, and the meshes are the same -- so the difference can still be coloured
        by section or material. The deck's sets and case names come along too.
        """
        from ada.fem.results.common import FEAResult

        base_fields = _group_fields(self.base.results)
        other_fields = _group_fields(self.other.results)
        results = []
        for d in self.diffs:
            if _is_property_field(d.name):
                continue
            base_field = base_fields[(d.name, d.step, d.elem_type)]
            other_field = other_fields[(d.name, d.other_step, d.elem_type)]
            keys, diff = _aligned_diff(base_field, other_field)[:2]
            results.append(dataclasses.replace(base_field, values=np.column_stack((keys, diff)), difference=True))
        results += [f for f in self.base.results if _is_property_field(f.name)]
        out = FEAResult(
            name=f"{self.other.name} - {self.base.name}",
            software=self.base.software,
            results=results,
            mesh=self.base.mesh,
            step_name_map=self.base.step_name_map,
            description=f"Difference: {self.other.name} minus {self.base.name}",
            analysis_kind=self.base.analysis_kind,
        )
        # What the Sesam readers carry beside the fields (sets, case names, superelements):
        # the difference is on the base's mesh and steps, so the base's apply.
        for attr in ("sesam_groups", "sesam_case_names", "sesam_result_cases", "sesam_super_elements"):
            if hasattr(self.base, attr):
                setattr(out, attr, getattr(self.base, attr))
        return out

    def show(self, **kwargs):
        """Show the difference ``other - base`` in the viewer: one field per compared field, for
        every step. Each field's colour scale is centred on zero, so an unchanged model is one
        colour. ``kwargs`` go to :meth:`FEAResult.show`."""
        return self.to_fea_result().show(**kwargs)


def compare_results(
    base: FEAResult,
    other: FEAResult,
    step_map: dict[int, int] | None = None,
    fields: list[str] | None = None,
    check_mesh: bool = True,
) -> FEAResultComparison:
    """Compare ``other`` against ``base``. See :meth:`FEAResult.compare`."""
    if check_mesh:
        problem = mesh_difference(base.mesh, other.mesh)
        if problem:
            raise ValueError(
                f"Cannot compare {base.name!r} and {other.name!r}: the meshes differ ({problem}). "
                "compare() is for results on the same mesh; pass check_mesh=False to compare anyway."
            )

    base_steps, other_steps = base.get_steps(), other.get_steps()
    if step_map is None:
        step_map = {s: s for s in base_steps if s in other_steps}
    else:
        missing = [s for s in step_map if s not in base_steps] + [s for s in step_map.values() if s not in other_steps]
        if missing:
            raise ValueError(f"step_map refers to steps that do not exist: {missing}")

    base_fields = _group_fields(base.results, fields)
    other_fields = _group_fields(other.results, fields)
    comparison = FEAResultComparison(base=base, other=other, step_map=dict(step_map))
    paired_other: set[FieldKey] = set()
    for (name, step, elem_type), base_field in base_fields.items():
        if step not in step_map:
            continue
        other_key = (name, step_map[step], elem_type)
        other_field = other_fields.get(other_key)
        if other_field is None:
            comparison.only_in_base.append((name, step, elem_type))
            continue
        paired_other.add(other_key)
        comparison.diffs.append(_field_diff(base_field, other_field))
    mapped_other_steps = set(step_map.values())
    comparison.only_in_other = [k for k in other_fields if k[1] in mapped_other_steps and k not in paired_other]
    return comparison


def mesh_difference(a: Mesh, b: Mesh) -> str | None:
    """A short description of how meshes ``a`` and ``b`` differ, or ``None`` if they are the same."""
    a_ids, b_ids = np.asarray(a.nodes.identifiers), np.asarray(b.nodes.identifiers)
    if len(a_ids) != len(b_ids):
        return f"{len(a_ids)} vs {len(b_ids)} nodes"
    a_order, b_order = np.argsort(a_ids, kind="stable"), np.argsort(b_ids, kind="stable")
    if not np.array_equal(a_ids[a_order], b_ids[b_order]):
        return "different node ids"
    a_xyz, b_xyz = np.asarray(a.nodes.coords)[a_order], np.asarray(b.nodes.coords)[b_order]
    scale = max(float(np.nanmax(np.abs(a_xyz))) if a_xyz.size else 0.0, 1.0)
    moved = np.flatnonzero(np.any(np.abs(a_xyz - b_xyz) > 1e-6 * scale, axis=1))
    if len(moved):
        return f"{len(moved)} node(s) at different coordinates, e.g. node {int(a_ids[a_order][moved[0]])}"

    a_conn, b_conn = _connectivity(a), _connectivity(b)
    if len(a_conn) != len(b_conn):
        return f"{len(a_conn)} vs {len(b_conn)} elements"
    if a_conn.keys() != b_conn.keys():
        return "different element ids"
    for el_id, refs in a_conn.items():
        if refs != b_conn[el_id]:
            return f"element {el_id} has different nodes"
    return None


def _connectivity(mesh: Mesh) -> dict[int, tuple]:
    out = {}
    for block in mesh.elements or []:
        refs = block.node_refs
        if block.node_refs_are_indices:
            # Back to node ids, so two meshes with different node order still compare equal.
            ids = np.asarray(mesh.nodes.identifiers)
            refs = [ids[np.asarray(r, dtype=int)] for r in refs]
        for label, r in zip(block.identifiers, refs):
            out[int(label)] = tuple(int(x) for x in np.asarray(r).ravel())
    return out


def _is_property_field(name: str) -> bool:
    """A model-property field (material, section, thickness, superelement): model data
    painted like a result, the same in both results of a comparison."""
    return str(name).startswith("props.")


def _elem_type_str(f) -> str:
    t = getattr(f, "elem_type", None)
    return "" if t is None else str(getattr(t, "value", t))


def _group_fields(results, names: list[str] | None = None) -> dict[FieldKey, NodalFieldData | ElementFieldData]:
    """``{(name, step, elem type): field}``; fields sharing a key are stacked into one."""
    grouped: dict[FieldKey, list] = {}
    for f in results:
        if names is not None and f.name not in names:
            continue
        grouped.setdefault((f.name, int(f.step), _elem_type_str(f)), []).append(f)
    out = {}
    for key, parts in grouped.items():
        if len(parts) == 1 or len({np.shape(p.values)[1] for p in parts}) != 1:
            out[key] = parts[0]
        else:
            out[key] = dataclasses.replace(parts[0], values=np.vstack([p.values for p in parts]))
    return out


def _num_key_cols(f) -> int:
    return len(getattr(f, "COLS", ["id"]))


def _row_keys(values: np.ndarray, num_key_cols: int) -> np.ndarray:
    """One int64 key per row from its key columns (node id, or element id + integration point)."""
    keys = np.asarray(values[:, 0], dtype=np.int64)
    for col in range(1, num_key_cols):
        keys = keys * 1_000_000 + np.asarray(values[:, col], dtype=np.int64)
    return keys


def _aligned_diff(base_field, other_field):
    """``(key columns, other - base, base values, other values, num unmatched)`` over the shared rows."""
    n_keys = _num_key_cols(base_field)
    a, b = np.asarray(base_field.values, dtype=float), np.asarray(other_field.values, dtype=float)
    if a.shape == b.shape and np.array_equal(a[:, :n_keys], b[:, :n_keys]):
        a_vals, b_vals, keys, unmatched = a[:, n_keys:], b[:, n_keys:], a[:, :n_keys], 0
    else:
        ka, kb = _row_keys(a, n_keys), _row_keys(b, n_keys)
        _, ia, ib = np.intersect1d(ka, kb, assume_unique=False, return_indices=True)
        ia, ib = np.sort(ia), ib[np.argsort(ia)]
        a_vals, b_vals, keys = a[ia, n_keys:], b[ib, n_keys:], a[ia, :n_keys]
        unmatched = len(a) + len(b) - 2 * len(ia)
    n_comp = min(a_vals.shape[1], b_vals.shape[1])
    a_vals, b_vals = a_vals[:, :n_comp], b_vals[:, :n_comp]
    return keys, b_vals - a_vals, a_vals, b_vals, unmatched


def _nanmax_abs(x: np.ndarray) -> np.ndarray:
    if x.shape[0] == 0:
        return np.full(x.shape[1], np.nan)
    with np.errstate(invalid="ignore"):
        out = np.abs(x)
    all_nan = np.all(np.isnan(out), axis=0)
    return np.where(all_nan, np.nan, np.nanmax(np.where(np.isnan(out), -np.inf, out), axis=0))


def _field_diff(base_field, other_field) -> FieldDiff:
    keys, diff, a_vals, b_vals, unmatched = _aligned_diff(base_field, other_field)
    max_abs_diff = _nanmax_abs(diff)
    at = []
    for c in range(diff.shape[1]):
        col = np.abs(diff[:, c])
        if col.size == 0 or np.all(np.isnan(col)):
            at.append(())
            continue
        row = int(np.nanargmax(col))
        at.append(tuple(int(x) for x in keys[row]))
    components = list(base_field.components or [])[: diff.shape[1]]
    components += [str(i) for i in range(len(components), diff.shape[1])]
    return FieldDiff(
        name=base_field.name,
        step=int(base_field.step),
        other_step=int(other_field.step),
        elem_type=_elem_type_str(base_field),
        components=components,
        num_points=len(keys),
        num_unmatched=unmatched,
        max_abs_base=_nanmax_abs(a_vals),
        max_abs_other=_nanmax_abs(b_vals),
        max_abs_diff=max_abs_diff,
        max_diff_at=at,
    )


def _summarise_keys(keys: list[FieldKey], limit: int = 5) -> str:
    names = sorted({k[0] for k in keys})
    shown = ", ".join(names[:limit]) + (", ..." if len(names) > limit else "")
    return f"{len(keys)} field/step(s): {shown}"
