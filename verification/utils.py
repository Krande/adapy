"""Verification-report helpers.

Convention: each paradoc doc keeps its private helpers in `<doc>/utils.py`
alongside `tasks.py` / `filters.py` / `build_hooks.py` / `paradoc.toml`.
Anything that used to live here AND has a natural home in adapy (solver
version probes, ODB → SQLite dumping, "what solvers are available")
moved into `ada.fem.formats.*`; what stays is verification-specific
post-processing + comparison-table conventions.

Surface:

- `FeaVerificationResult`: wrap an adapy `FEAResult` / `FEAResultV2`
  plus metadata + JSON cache I/O. Exposes a `safe_name` cached_property
  that maps the result name to a paradoc-Filter-safe identifier.
- `postprocess_result(result, metadata)`: build a `FeaVerificationResult`
  from a freshly-run case.
- `retrieve_cached_results(results, cache_dir)`: in-place augment a
  results list with cached JSON entries for cases that weren't
  re-executed this run.
- Bundle bake / collect lives in :mod:`ada.fem.results.docs`
  (``bake_fea_bundles`` / ``collect_fea_bundles``) — duck-typed on
  ``case.name`` / ``case.results`` so any per-report case wrapper
  (this one's :class:`FeaVerificationResult`, future param_models
  counterparts, …) plugs straight in without duplicating.
- `eig_data_to_df` / `append_df`: thin pandas helpers used by the
  comparison-table builder.
- `create_df_of_data(results, geom, order, hexquad)`: build one
  comparison-table DataFrame (eg `eig_compare_solid_o1`).
- `shorten_name` / `short_name_map`: verification's per-solver +
  per-geom column naming convention for the comparison tables.
"""

from __future__ import annotations

import logging
import math
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

import pandas as pd

from ada.fem.formats.abaqus.post_processing import FEAResultV2
from ada.fem.results import EigenDataSummary, FeaCaseResult, walk_cached_case_results
from ada.fem.results.common import FEAResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")


@dataclass
class FeaVerificationResult(FeaCaseResult):
    """Eigen-analysis case wrapper for the verification report.

    Adds an :class:`EigenDataSummary` slot to :class:`FeaCaseResult`'s
    common skeleton so the comparison-table builder can pull mode
    frequencies directly from the live (or cached) wrapper without
    re-parsing the source solver file. JSON round-trip preserves the
    eig summary via the ``_extra_payload`` / ``_hydrate_extras`` hooks
    the base class exposes; everything else (``name``, ``fem_format``,
    ``metadata``, ``last_modified``, :attr:`safe_name`, cache replay)
    comes from the base class.
    """

    eig_data: EigenDataSummary = None

    def _extra_payload(self) -> dict:
        return {"eigen_mode_data": self.eig_data.to_dict()}

    def _hydrate_extras(self, payload: dict) -> None:
        eig = EigenDataSummary([])
        eig.from_dict(payload["eigen_mode_data"])
        self.eig_data = eig


def postprocess_result(result: Union[FEAResult, FEAResultV2], metadata: dict) -> FeaVerificationResult:
    """Build a FeaVerificationResult from a freshly-executed FEA case."""
    from ada.fem.formats.general import FEATypes

    if isinstance(result.software, FEATypes):
        software = result.software.name.lower()
    else:
        software = result.software.lower()

    return FeaVerificationResult(
        name=result.name,
        fem_format=software,
        results=result,
        metadata=metadata,
        eig_data=result.get_eig_summary(),
    )


def _case_identity(res: FeaVerificationResult) -> tuple:
    """What makes two results the same case, independent of the name they were cached under."""
    m = res.metadata
    return (res.fem_format, m["geo"], m["elo"], m["hexquad"], m.get("reduced_integration", False))


def retrieve_cached_results(results: list[FeaVerificationResult], cache_dir: pathlib.Path) -> None:
    """Augment ``results`` in-place with cached cases from ``cache_dir``.

    The cache-walk + decode itself is generic (lives in
    :func:`ada.fem.results.walk_cached_case_results`); what stays here
    is the verification-specific *insertion ordering*: we slot each
    cached entry next to a live entry sharing the same ``metadata['elo']``
    so the comparison-table column order keeps the by-element-order
    grouping the report expects. Falls back to plain append when no
    matching live entry exists yet (the CI / cache-only path that
    seeds the list from scratch).
    """
    cached = walk_cached_case_results(
        FeaVerificationResult,
        cache_dir,
        skip_names={r.name for r in results},
    )

    res_names = [r.name for r in results]
    res_elo = [r.metadata["elo"] for r in results]
    # Cache files have been written under more than one naming scheme (e.g. the legacy
    # `cantilever_EIG_sesam_SHELL_o1_hqTrue`), so a name match alone lets the same case in
    # twice -- and a duplicated case is a duplicated comparison-table column.
    seen = {_case_identity(r) for r in results}
    for cached_result in cached:
        identity = _case_identity(cached_result)
        if identity in seen:
            logger.info(f"skipping cached {cached_result.name}: same case as an entry already loaded")
            continue
        seen.add(identity)
        cache_elo = cached_result.metadata["elo"]
        try:
            results.insert(res_elo.index(cache_elo), cached_result)
        except ValueError:
            results.append(cached_result)
            res_elo.append(cache_elo)
            res_names.append(cached_result.name)


# ---------------- comparison-table builders ----------------


def append_df(old_df, new_df):
    """Append `new_df` as columns onto `old_df`. Returns `new_df` if old is None."""
    return new_df if old_df is None else pd.concat([old_df, new_df], axis=1)


def eig_data_to_df(eig_data: EigenDataSummary, columns: list[str]) -> pd.DataFrame:
    """DataFrame of (mode_number, frequency_hz) from an EigenDataSummary."""
    return pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=columns)


def shorten_name(name: str, fem_format: str, geom_repr: str) -> str:
    """Compress a case name to the verification report's short form.

    `cantilever_EIG_code_aster_solid_o1_hqFalse_riFalse` becomes
    `ca_so_o1_hqFalse_riFalse` — fits the comparison-table column
    headers without wrapping.
    """
    short = name.replace("cantilever_EIG_", "")
    geom_repr_map = dict(solid="so", line="li", shell="sh")
    short = short.replace(fem_format, short_name_map[fem_format])
    short = short.replace(geom_repr, geom_repr_map[geom_repr])
    return short


def create_df_of_data(
    results: list[FeaVerificationResult], geom_repr: str, el_order: int, hexquad: bool
) -> pd.DataFrame | None:
    """Build one comparison DataFrame: rows are modes, columns are
    `(solver, hexquad-or-tri-quad-tag, reduced-int-tag)` for every
    matching case.

    Returns None if no result matches the (geom_repr, el_order)
    filter — caller skips registering an empty table.
    """
    df_main = None

    for res in results:
        soft = res.fem_format
        geo = res.metadata["geo"]
        elo = res.metadata["elo"]
        hq = res.metadata["hexquad"]
        uri = res.metadata.get("reduced_integration", False)

        if geom_repr != geo or elo != el_order:
            continue

        uri_str = "R" if uri is True else ""
        if geo.upper() == "SOLID":
            s_str = "_TET" if hq is False else "_HEX"
        elif geo.upper() == "SHELL":
            s_str = "_TRI" if hq is False else "_QUAD"
        else:
            s_str = ""

        short_name = short_name_map[soft]
        value_col = f"{short_name}{s_str}{uri_str}"
        df_current = eig_data_to_df(res.eig_data, ["Mode", value_col])
        new_col = df_current[value_col] if df_main is not None else df_current
        df_main = append_df(df_main, new_col)

    return df_main


def _case_label(res: FeaVerificationResult) -> str:
    """Compact ``solver_geom_oN[_tag][R]`` label matching the comparison
    tables' column convention."""
    geo = res.metadata["geo"]
    elo = res.metadata["elo"]
    hq = res.metadata["hexquad"]
    uri = res.metadata.get("reduced_integration", False)
    uri_str = "R" if uri is True else ""
    if geo.upper() == "SOLID":
        s_str = "_TET" if hq is False else "_HEX"
    elif geo.upper() == "SHELL":
        s_str = "_TRI" if hq is False else "_QUAD"
    else:
        s_str = ""
    return f"{short_name_map[res.fem_format]}_{geo}_o{elo}{s_str}{uri_str}"


_DIRECTIONS = ("X", "Y", "Z")


def _effective_mass(mode, direction: str) -> float | None:
    return getattr(mode, f"ef{direction.lower()}")


def _mass_normalised_participation(mode, direction: str) -> float | None:
    """The participation factor of the mode scaled to unit generalised mass.

    A participation factor is relative to the eigenvector's normalisation, which is each solver's
    own (Abaqus: largest displacement 1; Calculix, Code_Aster, Sestra: unit generalised mass), so
    raw factors do not compare. Scaled to unit generalised mass they do, and that factor is
    ``sign(Γ)·√Meff`` because ``Meff = Γ²·m_gen``. The sign is the solver's eigenvector sign, which
    is arbitrary per mode.
    """
    gamma = getattr(mode, f"p{direction.lower()}")
    meff = _effective_mass(mode, direction)
    if gamma is None or meff is None:
        return None
    return float(math.copysign(math.sqrt(max(meff, 0.0)), gamma))


#: The per-mode modal-mass quantities the appendix compares: value function and rounding.
MODAL_MASS_QUANTITIES = {
    "meff": (_effective_mass, 1),
    "pf": (_mass_normalised_participation, 3),
}


def create_modal_mass_comparison_df(
    results: list[FeaVerificationResult], geom_repr: str, el_order: int, quantity: str
) -> pd.DataFrame:
    """Cross-solver comparison of one per-mode modal-mass quantity, for one (geom, order).

    ``quantity`` is a key of :data:`MODAL_MASS_QUANTITIES`: ``"meff"`` for the effective modal mass
    [kg], ``"pf"`` for the mass-normalised participation factor. Rows are ``(Mode, Direction)`` over
    the global X/Y/Z directions; columns are the matching cases (the ``solver[_tag][R]`` convention
    of the frequency tables). Cases whose reader reported none of it are left out.

    Never empty: with no case to compare, one row says so. The report references every table key
    statically and paradoc errors on an unresolved one, and whether a configuration has data
    depends on which solvers ran and which snapshots are committed.
    """
    value_of, decimals = MODAL_MASS_QUANTITIES[quantity]
    df_main = None
    for res in results:
        geo = res.metadata["geo"]
        elo = res.metadata["elo"]
        if geom_repr != geo or el_order != elo:
            continue
        modes = res.eig_data.modes if res.eig_data is not None else []
        rows = [(m.no, d, value_of(m, d)) for m in modes for d in _DIRECTIONS]
        if all(v is None for *_, v in rows):
            continue
        value_col = _case_label(res).replace(f"_{geo}_o{elo}", "")  # solver[_tag][R]
        df_current = pd.DataFrame(rows, columns=["Mode", "Direction", value_col]).set_index(["Mode", "Direction"])
        df_main = df_current if df_main is None else df_main.join(df_current, how="outer")

    if df_main is None:
        return pd.DataFrame([{"Mode": "-", "Direction": "-", "Note": "No solver reported it for this mesh."}])
    return df_main.round(decimals).reset_index().sort_values(["Mode", "Direction"]).reset_index(drop=True)


def create_eff_mass_summary_df(results: list[FeaVerificationResult]) -> pd.DataFrame | None:
    """Summary of effective modal mass [kg] per case: one row per case,
    summed over its captured modes in the global X/Y/Z directions.

    Only cases whose reader populated effective mass are included (every
    solver's does, but an Abaqus / Sesam snapshot cached before theirs did
    carries none); returns None if none did, so the caller skips registering
    an empty table. Code_Aster and Sesam report translational effective mass
    only — there is no rotational column.
    """
    rows = []
    for res in results:
        modes = res.eig_data.modes if res.eig_data is not None else []
        if not modes or all(m.efx is None for m in modes):
            continue

        def _s(dof: str) -> float:
            return float(sum(getattr(m, dof) or 0.0 for m in modes))

        rows.append(
            {
                "Case": _case_label(res),
                "Modes": len(modes),
                "ΣMeff X": round(_s("efx"), 1),
                "ΣMeff Y": round(_s("efy"), 1),
                "ΣMeff Z": round(_s("efz"), 1),
            }
        )

    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("Case").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# The plate strip case: its own result wrapper and comparison tables
# ---------------------------------------------------------------------------------------------


@dataclass
class PlateStaticResult(FeaCaseResult):
    """One static plate case: the mid-span deflection a solver returned, and its mesh.

    Kept as a scalar rather than a whole field because that is what the comparison is: one number
    per (solver, seed), against each other and against a closed form. The field data stays in the
    solver's own result file for anyone who wants it.
    """

    mid_span_u3: float = None
    mesh_size: float = None
    stiffened: bool = False
    n_shells: int = None

    def _extra_payload(self) -> dict:
        return {
            "mid_span_u3": self.mid_span_u3,
            "mesh_size": self.mesh_size,
            "stiffened": self.stiffened,
            "n_shells": self.n_shells,
        }

    def _hydrate_extras(self, payload: dict) -> None:
        self.mid_span_u3 = payload["mid_span_u3"]
        self.mesh_size = payload["mesh_size"]
        self.stiffened = payload["stiffened"]
        self.n_shells = payload.get("n_shells")


#: What each solver's reader calls the global Z translation.
_U3_COMPONENTS = ("U3", "D3", "DZ", "Z")


def mid_span_u3(result, length: float, width: float) -> float:
    """Mid-span centreline ``u3`` out of an adapy FEAResult, sampled by position.

    By position and not by node id: the id is the mesher's, and the same physical point has a
    different one at every seed and in every solver's renumbering.
    The column is picked by component name too: the solvers disagree on layout (Sesam's derived
    ``sesam.nodes.displacement`` leads with a magnitude column, so position 3 there is Y, not Z).
    """
    import numpy as np

    from ada.fem.results.field_data import NodalFieldType

    if hasattr(result, "to_fea_result"):  # FEAResultV2 (Abaqus via the results SQLite) carries no mesh itself
        result = result.to_fea_result()

    coords = np.asarray(result.mesh.nodes.coords, dtype=float)
    # The last one: a reader that keeps the step's base-state frame (Abaqus' frame 0, all zeros) lists
    # it first, and the loaded increment is the step's final one.
    field = [
        f
        for f in result.results
        if getattr(f, "field_type", None) == NodalFieldType.DISP or f.name in ("DISP", "result__DEPL")
    ][-1]
    u3_col = next((i for i, c in enumerate(field.components) if c.upper() in _U3_COMPONENTS), None)
    if u3_col is None:
        raise ValueError(f"no vertical component among {field.name} components {field.components}")
    values = np.asarray(field.values, dtype=float)
    offset = np.abs(coords[:, 0] - length / 2.0) + np.abs(coords[:, 1] - width / 2.0)
    index = int(np.argmin(offset))
    if offset[index] > 1e-06:
        raise ValueError(f"no node at mid-span on the centreline; closest was {offset[index]!r} away")
    # values rows are [node_id, *components]
    return float(values[index][u3_col + 1])


def _closed_form_last(df: pd.DataFrame, key_col: str) -> pd.DataFrame:
    """Key column first, solvers sorted, closed form pinned last.

    `from_records` orders columns by first appearance across rows, so a solver missing from the
    first row (no result at the coarsest seed, say) would otherwise land after the closed form.
    """
    solvers = sorted(c for c in df.columns if c not in (key_col, "closed form"))
    tail = ["closed form"] if "closed form" in df.columns else []
    return df[[key_col, *solvers, *tail]]


def create_plate_static_df(results, closed_form: float | None) -> "pd.DataFrame | None":
    """Rows are mesh seeds, columns are solvers; the last column is the closed form.

    ``closed_form=None`` leaves the column out -- the stiffened strip has none, and repeating the
    bare strip's value beside it would read as a reference the solvers miss by 99 %.

    The relative error against the closed form is what the report reads, so it is computed here
    rather than left to the reader: a table of raw deflections all near 0.173 hides which solver is
    converging and which is merely close.
    """
    rows: dict = {}
    for res in results:
        if not isinstance(res, PlateStaticResult) or res.mid_span_u3 is None:
            continue
        label = short_name_map.get(res.fem_format, res.fem_format)
        if res.stiffened:
            label += "_st"
        rows.setdefault(res.mesh_size, {})[label] = res.mid_span_u3

    if not rows:
        return None

    records = []
    for mesh_size in sorted(rows, reverse=True):
        record = {"Seed [m]": mesh_size}
        record.update({k: rows[mesh_size][k] for k in sorted(rows[mesh_size])})
        if closed_form is not None:
            record["closed form"] = closed_form
        records.append(record)
    return _closed_form_last(pd.DataFrame.from_records(records), "Seed [m]")


def create_plate_eig_df(results, closed_form: list) -> "pd.DataFrame | None":
    """Rows are modes, columns are solvers; the last column is the closed form.

    Only the cylindrical modes have the closed form beside them, and they are the low ones, so the
    table is as long as the shortest solver's mode list rather than padded.

    The column label carries the ``_st`` suffix of the stiffened variant, exactly as
    :func:`create_plate_static_df` does, and a label that would be written twice raises. That is not
    defensive dressing: a plain ``ca`` key silently took the stiffened plate's frequencies (19.3 Hz
    against 1.54) into the unstiffened table, which looks like a solver disagreement rather than a
    bookkeeping error, and is the one kind of mistake a comparison report must not make quietly.
    """
    columns: dict = {}
    for res in results:
        eig = getattr(res, "eig_data", None)
        if eig is None or not getattr(eig, "modes", None):
            continue
        label = short_name_map.get(res.fem_format, res.fem_format)
        if res.metadata.get("stiffened", False):
            label += "_st"
        if label in columns:
            raise ValueError(
                f"two plate eigen cases both want the column {label!r} ({res.name}). One of them "
                f"would overwrite the other, so the label does not identify the case."
            )
        columns[label] = [m.f_hz for m in eig.modes]

    if not columns:
        return None

    n = min(min(len(v) for v in columns.values()), len(closed_form))
    records = []
    for i in range(n):
        record = {"Mode": i + 1}
        record.update({k: columns[k][i] for k in sorted(columns)})
        record["closed form"] = closed_form[i]
        records.append(record)
    return _closed_form_last(pd.DataFrame.from_records(records), "Mode")
