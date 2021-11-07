import json
import logging
import os
import pathlib
from typing import Dict, List, Union

import pandas as pd
from paradoc import OneDoc
from paradoc.common import TableFormat
from test_fem_eig_cantilever import beam, test_fem_eig

from ada.fem.results import EigenDataSummary, Results, results_from_cache


def append_df(new_df, old_df):
    return new_df if old_df is None else pd.concat([old_df, new_df], axis=1)


def eig_data_to_df(eig_data: EigenDataSummary, columns):
    return pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=columns)


def shorten_name(name, fem_format, geom_repr) -> str:
    short_name = name.replace("cantilever_EIG_", "")
    short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")
    geom_repr_map = dict(solid="so", line="li", shell="sh")
    short_name = short_name.replace(fem_format, short_name_map[fem_format])
    short_name = short_name.replace(geom_repr, geom_repr_map[geom_repr])

    return short_name


def make_comparison_data_set(results: List[Results]) -> Dict[str, Union[pd.DataFrame, None]]:
    merged_line_df = None
    merged_shell_df = None
    merged_solid_df = None

    df_write_map: Dict[str, Union[pd.DataFrame, None]] = dict(
        line=merged_line_df, shell=merged_shell_df, solid=merged_solid_df
    )

    for res in results:
        soft = res.fem_format
        geo = res.metadata["geo"]
        short_name = shorten_name(res.name, soft, geo)
        df = eig_data_to_df(res.eigen_mode_data, ["Mode", short_name])
        df_current = df_write_map.get(geo)
        if df_current is not None:
            df = df[short_name]
        df_write_map[geo] = append_df(df, df_current)

    return df_write_map


def retrieve_cached_results(results, cache_dir):
    from ada.core.utils import get_list_of_files

    res_names = [r.name for r in results]
    res_elo = [r.metadata["elo"] for r in results]
    for res_file in get_list_of_files(cache_dir, ".json"):
        with open(res_file, "r") as f:
            res = json.load(f)
            if res["name"] in res_names:
                continue
        cached_results = results_from_cache(res)
        cache_elo = cached_results.metadata["elo"]
        index_insert = res_elo.index(cache_elo)
        results.insert(index_insert, cached_results)


def simulate(bm, el_order, geom_repr, analysis_software, eig_modes, overwrite, execute) -> List[Results]:
    results = []

    for elo in el_order:
        for geo in geom_repr:
            for soft in analysis_software:
                result = test_fem_eig(bm, soft, geo, elo, overwrite=overwrite, execute=execute, eigen_modes=eig_modes)
                if result is None or result.eigen_mode_data is None:
                    logging.error("No result file is located")
                    continue
                result.metadata["geo"] = geo
                result.metadata["elo"] = elo
                results.append(result)

    return results


def main(overwrite, execute):
    analysis_software = ["calculix", "code_aster"]
    el_order = [1, 2]
    geom_repr = ["line", "shell", "solid"]
    eig_modes = 11

    bm = beam()

    one = OneDoc("report")
    one.variables = dict(
        geom_specifics=str(bm),
        ca_version=14.2,
        ccx_version=2.16,
        aba_version=2021,
        ses_version=10,
        num_modes=eig_modes,
    )

    table_format = TableFormat(font_size=8, float_fmt=".3f")

    results = simulate(bm, el_order, geom_repr, analysis_software, eig_modes, overwrite, execute)

    cache_dir = pathlib.Path("").resolve().absolute() / ".cache"
    os.makedirs(cache_dir, exist_ok=True)

    retrieve_cached_results(results, cache_dir)
    df_write_map = make_comparison_data_set(results)

    for geo in geom_repr:
        one.add_table(
            f"eig_compare_{geo}",
            df_write_map[geo],
            f"Comparison of all Eigenvalue analysis using {geo} elements",
            tbl_format=table_format,
        )

    for res in results:
        res.save_results_to_json(cache_dir / res.name)
        one.add_table(
            res.name,
            eig_data_to_df(res.eigen_mode_data, ["Mode", "Eigenvalue (real)"]),
            res.name,
        )

    one.compile("ADA-FEA-verification")


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    main(overwrite=True, execute=True)
