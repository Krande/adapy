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
    for cached_result in cached:
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
