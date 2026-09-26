"""The solver-neutral exchange format, and how the node-correspondence problem is solved.

**The problem.** Sestra meshes the Sesam FEM that adapy writes. Abaqus meshes the CAE
geometry. Nothing makes the two node numberings agree, and nothing should: they are
independent meshers, and node 7 in one deck has no relationship to node 7 in the other.
Comparing by node id would compare unrelated points and report whatever it found.

**The solution: compare by position, at points both meshers are forced to seed.** Each
side is sampled at :data:`model.PROBE_POINTS` -- member ends, mid-spans and girder quarter
points -- and each probe is resolved to a node by coordinate match, not by id. Three rules
make that safe:

1. *Every probe lies at an integer multiple of* :data:`model.MESH_SIZE` *from a member
   end.* Member ends are nodes by topology in any mesher; the intermediate probes are nodes
   because the seed count along each member is even. This is checked, not assumed:
   :func:`model.assert_probes_are_seeded` on the Sesam side, and the Abaqus half must call
   it too.
2. *The match must be unique.* Zero nodes within :data:`MATCH_TOL` raises
   :class:`ProbeNotFound`; two or more raises :class:`AmbiguousProbe`. A duplicated,
   unmerged node at a joint -- exactly the defect a cross-solver check should catch -- shows
   up here as an ambiguity rather than as a coin flip.
3. :data:`MATCH_TOL` *is a round-off tolerance, not a search radius.* Both meshes descend
   from the same adapy geometry, so a probe coordinate is exact in both to within the
   mesher's floating-point arithmetic. 1 um on a 6 m frame is 1.7e-7 relative: far above
   double-precision noise, far below the 1 m element size, so it cannot reach a neighbouring
   node.

The resolved node id travels in the table (:attr:`DisplacementTable.node_ids`) so a reader
can see *which* node each side actually used and confirm the two are different -- if they
happen to coincide, that is luck, not the mechanism.

**Why one sampler serves both solvers.** ``read_sin_file`` and adapy's Abaqus ODB reader
both return an :class:`ada.fem.results.common.FEAResult` carrying a mesh and nodal field
data. :func:`sample_fea_result` works on that, so node correspondence is solved once, in
one function, for both halves -- rather than twice, differently.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field

import numpy as np

#: Coordinate match tolerance, metres. A round-off tolerance, not a search radius -- see
#: the module docstring, rule 3.
MATCH_TOL = 1.0e-6

#: The six components, in order, as they are stored in a :class:`DisplacementTable`:
#: three global translations in metres, three rotations in radians.
COMPONENTS = ("u1", "u2", "u3", "r1", "r2", "r3")

#: Field names to try, in order, when pulling nodal displacement out of an
#: :class:`~ada.fem.results.common.FEAResult`.
#:
#: ``RVNODDIS`` is the Sesam result card verbatim: components ``U1..U6``, six columns after
#: the node label, nothing to strip. ``sesam.nodes.displacement`` is the same data under
#: adapy's semantic name but with a leading ``ALL`` column (the translation magnitude), so
#: it needs an offset -- handled by matching on the component names rather than by assuming
#: a column layout. ``U`` is what the Abaqus reader calls it.
DISPLACEMENT_FIELD_CANDIDATES = ("RVNODDIS", "U", "sesam.nodes.displacement")

#: Component-name aliases -> canonical index 0..5. Solvers name the same six numbers
#: differently; resolving by name means a reader that reorders or prefixes its columns
#: cannot silently shift rotations into translations.
_COMPONENT_ALIASES: dict[str, int] = {
    "u1": 0, "ux": 0, "x": 0, "x-disp": 0,
    "u2": 1, "uy": 1, "y": 1, "y-disp": 1,
    "u3": 2, "uz": 2, "z": 2, "z-disp": 2,
    "u4": 3, "ur1": 3, "rx": 3, "urx": 3,
    "u5": 4, "ur2": 4, "ry": 4, "ury": 4,
    "u6": 5, "ur3": 5, "rz": 5, "urz": 5,
}  # fmt: skip


class ProbeNotFound(LookupError):
    """No node within :data:`MATCH_TOL` of a probe point.

    Deliberately fatal. A comparison that quietly drops the point it could not find is the
    failure mode this whole package exists to prevent.
    """


class AmbiguousProbe(LookupError):
    """More than one node within :data:`MATCH_TOL` of a probe point."""


class MissingDisplacementField(LookupError):
    """The result carries no recognisable nodal displacement field."""


@dataclass
class DisplacementTable:
    """Nodal displacements at named points, from one solver, in one load case.

    This is the *only* thing the comparator consumes, and the contract the Abaqus half has
    to satisfy. It is JSON round-trippable on purpose (:meth:`to_json` / :meth:`from_json`)
    so the two halves need not run in the same process -- or the same Python: Abaqus's
    kernel interpreter can emit one of these and :mod:`compare` will read it.

    Units are fixed: **metres and radians**, global axes. There is no unit field to get
    wrong; a runner that works in millimetres converts before constructing one.
    """

    #: ``"sestra"``, ``"abaqus"``, ... Free-form, used only in reports.
    solver: str
    #: Version string as reported by the solver or its locator.
    solver_version: str
    #: Which model this is (``"portal_frame"``).
    model: str
    #: Load case / step label.
    load_case: str
    #: ``{probe name: (u1, u2, u3, r1, r2, r3)}`` -- metres and radians, global axes.
    displacements: dict[str, tuple[float, float, float, float, float, float]]
    #: ``{probe name: solver node id}``. Provenance: lets a reader confirm the two solvers
    #: used genuinely different nodes and that matching, not luck, lined them up.
    node_ids: dict[str, int] = field(default_factory=dict)
    #: ``{probe name: (x, y, z)}`` of the node actually used, so a reader can see the
    #: residual between probe and node position.
    node_coords: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    #: Path to the result file this was read from.
    source: str = ""

    def component(self, probe: str, comp: str) -> float:
        """One component at one probe, by canonical name (``"u1"`` .. ``"r3"``)."""
        return self.displacements[probe][COMPONENTS.index(comp)]

    def to_json(self, path: str | pathlib.Path) -> pathlib.Path:
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["displacements"] = {k: list(v) for k, v in self.displacements.items()}
        payload["node_coords"] = {k: list(v) for k, v in self.node_coords.items()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_json(cls, path: str | pathlib.Path) -> DisplacementTable:
        payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        payload["displacements"] = {k: tuple(float(x) for x in v) for k, v in payload["displacements"].items()}
        payload["node_coords"] = {k: tuple(float(x) for x in v) for k, v in payload.get("node_coords", {}).items()}
        payload["node_ids"] = {k: int(v) for k, v in payload.get("node_ids", {}).items()}
        return cls(**payload)


def sample_fea_result(
    result,
    probes,
    *,
    solver: str,
    solver_version: str,
    model_name: str,
    load_case: str,
    step: int | None = None,
    match_tol: float = MATCH_TOL,
) -> DisplacementTable:
    """Build a :class:`DisplacementTable` from an adapy ``FEAResult`` by coordinate match.

    ``result`` is anything with ``.mesh.nodes`` (``identifiers`` + ``coords``) and a nodal
    displacement field -- which covers ``read_sin_file``'s output and adapy's Abaqus ODB
    reader, so both halves of the comparison go through this one function.

    Raises :class:`ProbeNotFound` or :class:`AmbiguousProbe` rather than returning a partial
    table. There is no "skip what you cannot find" mode and there should not be one.
    """
    node_ids = np.asarray(result.mesh.nodes.identifiers)
    coords = np.asarray(result.mesh.nodes.coords, dtype=float)
    if coords.ndim != 2 or coords.shape[1] != 3:
        raise ValueError(f"expected an (n, 3) node coordinate array, got {coords.shape}")

    values, comp_index = _nodal_displacements(result, step)

    displacements: dict[str, tuple[float, float, float, float, float, float]] = {}
    resolved_ids: dict[str, int] = {}
    resolved_coords: dict[str, tuple[float, float, float]] = {}

    for probe in probes:
        target = np.asarray(probe.xyz, dtype=float)
        dist = np.linalg.norm(coords - target, axis=1)
        hits = np.flatnonzero(dist <= match_tol)
        if hits.size == 0:
            nearest = int(np.argmin(dist))
            raise ProbeNotFound(
                f"{solver}: no node within {match_tol} m of probe {probe.name} at {probe.xyz}. "
                f"Nearest is node {int(node_ids[nearest])} at {tuple(coords[nearest])}, "
                f"{dist[nearest]:.6g} m away. The mesh does not seed this point -- fix the "
                f"seeding, do not widen the tolerance."
            )
        if hits.size > 1:
            raise AmbiguousProbe(
                f"{solver}: {hits.size} nodes within {match_tol} m of probe {probe.name} at "
                f"{probe.xyz}: {[int(node_ids[i]) for i in hits]}. Coincident nodes at a "
                f"probe mean the mesh is unmerged there, which is itself a defect."
            )
        idx = int(hits[0])
        nid = int(node_ids[idx])
        row = _row_for_node(values, nid)
        displacements[probe.name] = tuple(float(row[comp_index[c]]) for c in range(6))
        resolved_ids[probe.name] = nid
        resolved_coords[probe.name] = tuple(float(x) for x in coords[idx])

    return DisplacementTable(
        solver=solver,
        solver_version=solver_version,
        model=model_name,
        load_case=load_case,
        displacements=displacements,
        node_ids=resolved_ids,
        node_coords=resolved_coords,
        source=str(getattr(result, "results_file_path", "") or ""),
    )


def _nodal_displacements(result, step: int | None):
    """``(values, component_index)`` for the nodal displacement field.

    ``values`` is the raw ``NodalFieldData.values`` array -- column 0 the node label,
    the rest the components. ``component_index[i]`` is the column holding canonical
    component ``i``, resolved from the field's own ``components`` names rather than assumed,
    because the candidate fields do not share a column layout (``sesam.nodes.displacement``
    carries a leading translation-magnitude column that ``RVNODDIS`` does not).
    """
    grouped = result.get_results_grouped_by_field_value()
    for name in DISPLACEMENT_FIELD_CANDIDATES:
        if name not in grouped:
            continue
        candidates = grouped[name]
        if step is not None:
            candidates = [c for c in candidates if c.step == step] or candidates
        fld = candidates[0]
        comp_index: dict[int, int] = {}
        for col, comp_name in enumerate(fld.components):
            canonical = _COMPONENT_ALIASES.get(str(comp_name).strip().lower())
            if canonical is not None and canonical not in comp_index:
                # +1: column 0 of ``values`` is the node label.
                comp_index[canonical] = col + 1
        if len(comp_index) != 6:
            raise MissingDisplacementField(
                f"field '{name}' has components {list(fld.components)}, from which only "
                f"{sorted(comp_index)} of the six displacement components could be "
                f"identified. Add the missing names to _COMPONENT_ALIASES."
            )
        return np.asarray(fld.values, dtype=float), comp_index

    raise MissingDisplacementField(
        f"no nodal displacement field found. Tried {list(DISPLACEMENT_FIELD_CANDIDATES)}; "
        f"the result carries {sorted(grouped)}."
    )


def _row_for_node(values: np.ndarray, node_id: int) -> np.ndarray:
    hits = np.flatnonzero(values[:, 0].astype(np.int64) == node_id)
    if hits.size != 1:
        raise ProbeNotFound(
            f"the displacement field has {hits.size} rows for node {node_id}, expected "
            f"exactly one. A node present in the mesh but absent from the field is a result "
            f"file that did not store what it claims to."
        )
    return values[int(hits[0])]
