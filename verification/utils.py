"""Verification-report helpers.

Convention: each paradoc doc keeps its private helpers in `<doc>/utils.py`
alongside `tasks.py` / `filters.py` / `build_hooks.py` / `paradoc.toml`.
Anything that used to live here AND has a natural home in adapy (solver
version probes, ODB → SQLite dumping, "what solvers are available")
moved into `ada.fem.formats.*`; what stays is verification-specific
post-processing + comparison-table conventions.

Surface:

- `FeaVerificationResult`: wrap an adapy `FEAResult` / `FEAResultV2`
  plus metadata + JSON cache I/O.
- `postprocess_result(result, metadata)`: build a `FeaVerificationResult`
  from a freshly-run case.
- `retrieve_cached_results(results, cache_dir)`: in-place augment a
  results list with cached JSON entries for cases that weren't
  re-executed this run.
- `eig_data_to_df` / `append_df`: thin pandas helpers used by the
  comparison-table builder.
- `create_df_of_data(results, geom, order, hexquad)`: build one
  comparison-table DataFrame (eg `eig_compare_solid_o1`).
- `shorten_name` / `short_name_map`: verification's per-solver +
  per-geom column naming convention for the comparison tables.
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Union

import pandas as pd

from ada.fem.formats.abaqus.post_processing import FEAResultV2
from ada.fem.results import EigenDataSummary
from ada.fem.results.common import FEAResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")


@dataclass
class FeaVerificationResult:
    """A single FEA case's result + metadata, with JSON cache I/O.

    Wraps an adapy result (`FEAResult` from a live run or `FEAResultV2`
    from a re-loaded SQLite dump) alongside the case's axis values
    (geom_repr, elem_order, hexquad, reduced_integration) and an
    optional last-modified timestamp.
    """

    name: str
    fem_format: str
    eig_data: EigenDataSummary = None
    results: Union[FEAResult, FEAResultV2] = None
    metadata: dict = field(default_factory=dict)
    last_modified: datetime = field(default_factory=datetime.now)

    def save_results_to_json(self, cache_filepath) -> None:
        """Persist a JSON snapshot (eig data + metadata) for offline replay."""
        if isinstance(cache_filepath, str):
            cache_filepath = pathlib.Path(cache_filepath)

        payload = {
            "name": self.name,
            "fem_format": self.fem_format,
            "metadata": self.metadata,
            "eigen_mode_data": self.eig_data.to_dict(),
            "last_modified": self.last_modified.timestamp(),
        }
        with open(cache_filepath.with_suffix(".json"), "w") as f:
            json.dump(payload, f, indent=4)


def postprocess_result(
    result: Union[FEAResult, FEAResultV2], metadata: dict
) -> FeaVerificationResult:
    """Build a FeaVerificationResult from a freshly-executed FEA case."""
    from ada.fem.formats.general import FEATypes

    if isinstance(result.software, FEATypes):
        software = result.software.name.lower()
    else:
        software = result.software.lower()

    return FeaVerificationResult(
        name=result.name,
        fem_format=software,
        eig_data=result.get_eig_summary(),
        results=result,
        metadata=metadata,
    )


def _results_from_cache(cached: dict) -> FeaVerificationResult:
    """Reconstruct a FeaVerificationResult from its on-disk JSON shape.

    Strips any legacy file extension (`.rmed`, `.frd`) from the cached
    name so it matches the post-fix live-run convention. Without that
    normalization, the loader would treat an old `.rmed`-suffixed
    cache as a distinct case from a clean-named live result and we'd
    end up with duplicate rows / duplicate DataFrame columns.
    """
    raw_name = cached["name"]
    name = pathlib.Path(raw_name).stem if "." in raw_name else raw_name
    res = FeaVerificationResult(
        name=name, fem_format=cached["fem_format"], metadata=cached["metadata"]
    )
    eig_data = EigenDataSummary([])
    eig_data.from_dict(cached["eigen_mode_data"])
    res.eig_data = eig_data
    res.last_modified = datetime.fromtimestamp(cached["last_modified"])
    return res


def retrieve_cached_results(
    results: list[FeaVerificationResult], cache_dir: pathlib.Path
) -> None:
    """Augment `results` in place with cached cases from `cache_dir`.

    Walks `cache_dir/*.json`, skips entries already present in
    `results`, decodes the rest into FeaVerificationResults, and
    inserts them next to live results with the same `el_order` (keeps
    table ordering) — falls back to append if no live result with
    matching el_order exists yet. Covers the empty-seed case where a
    CI build has no live solver output and consumes cached results
    only.
    """
    res_names = [r.name for r in results]
    res_elo = [r.metadata["elo"] for r in results]
    for res_file in cache_dir.rglob("*.json"):
        if res_file.stem in ("software_versions", "debug"):
            continue
        try:
            with open(res_file, "r") as f:
                cached = json.load(f)
        except json.decoder.JSONDecodeError as exc:
            logger.error(f"{res_file}: {exc}")
            continue
        raw_cached_name = cached["name"]
        cached_name = (
            pathlib.Path(raw_cached_name).stem if "." in raw_cached_name else raw_cached_name
        )
        if cached_name in res_names:
            continue
        cached_result = _results_from_cache(cached)
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
