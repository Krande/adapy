import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from conftest import beam
from paradoc import OneDoc
from paradoc.common import TableFormat
from test_fem_eig_cantilever import test_fem_eig

from ada.config import logger
from ada.fem.results import EigenDataSummary
from ada.fem.results.common import FEAResult


@dataclass
class FeaVerificationResult:
    name: str
    fem_format: str
    eig_data: EigenDataSummary = None
    results: FEAResult = None
    metadata: dict = field(default_factory=dict)
    last_modified: datetime = field(default_factory=datetime.now)

    def save_results_to_json(self, cache_filepath):
        if isinstance(cache_filepath, str):
            cache_filepath = pathlib.Path(cache_filepath)

        res_dict = dict()
        res_dict["name"] = self.name
        res_dict["fem_format"] = self.fem_format
        res_dict["metadata"] = self.metadata
        res_dict["eigen_mode_data"] = self.eig_data.to_dict()
        res_dict["last_modified"] = self.last_modified.timestamp()

        with open(cache_filepath.with_suffix(".json"), "w") as f:
            json.dump(res_dict, f, indent=4)


def append_df(old_df, new_df):
    return new_df if old_df is None else pd.concat([old_df, new_df], axis=1)


def eig_data_to_df(eig_data: EigenDataSummary, columns):
    return pd.DataFrame([(e.no, e.f_hz) for e in eig_data.modes], columns=columns)


short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")


def shorten_name(name, fem_format, geom_repr) -> str:
    short_name = name.replace("cantilever_EIG_", "")

    geom_repr_map = dict(solid="so", line="li", shell="sh")
    short_name = short_name.replace(fem_format, short_name_map[fem_format])
    short_name = short_name.replace(geom_repr, geom_repr_map[geom_repr])

    return short_name


def create_df_of_data(results: list[FeaVerificationResult], geom_repr, el_order, hexquad):
    df_main = None

    for res in results:
        soft = res.fem_format
        geo = res.metadata["geo"]
        elo = res.metadata["elo"]
        hq = res.metadata["hexquad"]

        if geom_repr != geo or elo != el_order:
            continue

        if geo.upper() == "SOLID":
            s_str = "_"
            s_str += "TET" if hq is False else "HEX"
        elif geo.upper() == "SHELL":
            s_str = "_"
            s_str += "TRI" if hq is False else "QUAD"
        else:
            s_str = ""

        short_name = soft.replace(soft, short_name_map[soft])
        value_col = f"{short_name}{s_str}"
        df_current = eig_data_to_df(res.eig_data, ["Mode", value_col])
        new_col = df_current[value_col] if df_main is not None else df_current
        df_main = append_df(df_main, new_col)

    return df_main


def retrieve_cached_results(results: list[FeaVerificationResult], cache_dir):
    from ada.core.file_system import get_list_of_files

    res_names = [r.name for r in results]
    res_elo = [r.metadata["elo"] for r in results]
    for res_file in get_list_of_files(cache_dir, ".json"):
        with open(res_file, "r") as f:
            try:
                res = json.load(f)
            except json.decoder.JSONDecodeError as e:
                logging.error((res_file, e))
                continue
            if res["name"] in res_names:
                continue
        cached_results = results_from_cache(res)
        cache_elo = cached_results.metadata["elo"]
        index_insert = res_elo.index(cache_elo)
        results.insert(index_insert, cached_results)


def results_from_cache(results_dict: dict) -> FeaVerificationResult:
    res = FeaVerificationResult(
        name=results_dict["name"], fem_format=results_dict["fem_format"], metadata=results_dict["metadata"]
    )
    eig_data = EigenDataSummary([])
    eig_data.from_dict(results_dict["eigen_mode_data"])
    res.eig_data = eig_data
    res.last_modified = datetime.fromtimestamp(results_dict["last_modified"])
    return res


def simulate(
    bm, el_order, geom_repr, analysis_software, use_hex_quad, eig_modes, overwrite, execute
) -> list[FeaVerificationResult]:
    results = []
    short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")
    for elo in el_order:
        for geo in geom_repr:
            for soft in analysis_software:
                for hexquad in use_hex_quad:
                    try:
                        result = test_fem_eig(
                            bm,
                            soft,
                            geo,
                            elo,
                            hexquad,
                            short_name_map=short_name_map,
                            overwrite=overwrite,
                            execute=execute,
                            eigen_modes=eig_modes,
                        )
                    except FileNotFoundError as e:
                        logger.error(e)
                        continue
                    if result is None:
                        logging.error("No result file is located")
                        continue

                    metadata = dict()
                    metadata["geo"] = geo
                    metadata["elo"] = elo
                    metadata["hexquad"] = hexquad
                    fvr = FeaVerificationResult(
                        name=result.name,
                        fem_format=soft,
                        results=result,
                        metadata=metadata,
                        eig_data=result.get_eig_summary(),
                    )
                    results.append(fvr)

    if len(results) == 0:
        raise ValueError("No results are located")

    return results


def main(overwrite, execute):
    analysis_software = ["calculix", "code_aster"]
    el_order = [1, 2]
    geom_repr = ["line", "shell", "solid"]
    eig_modes = 11
    use_hex_quad = [False, True]

    bm = beam()

    one = OneDoc("report")
    one.variables = dict(
        geom_specifics=str(bm),
        ca_version="16.4.2",
        ccx_version="2.21",
        aba_version="2021",
        ses_version="10",
        num_modes=eig_modes,
    )

    table_format = TableFormat(font_size=8, float_fmt=".3f")

    results = simulate(bm, el_order, geom_repr, analysis_software, use_hex_quad, eig_modes, overwrite, execute)

    cache_dir = pathlib.Path("").resolve().absolute() / ".cache"
    os.makedirs(cache_dir, exist_ok=True)

    retrieve_cached_results(results, cache_dir)

    solid_tables = dict(
        eig_compare_solid_o1=dict(o=1),
        eig_compare_solid_o2=dict(o=2),
    )

    shell_tables = dict(
        eig_compare_shell_o1=dict(o=1, hq=True),
        eig_compare_shell_o2=dict(o=2, hq=True),
    )

    line_tables = dict(
        eig_compare_line_o1=dict(o=1, hq=False),
        eig_compare_line_o2=dict(o=2, hq=False),
    )

    for name, props in solid_tables.items():
        geo = "solid"
        o = props["o"]
        order = "1st" if o == 1 else "2nd"

        df = create_df_of_data(results, geo, o, None)

        one.add_table(
            name,
            df,
            f"Comparison of all Eigenvalue analysis using {geo} {order} order elements",
            tbl_format=table_format,
        )

    for name, props in shell_tables.items():
        geo = "shell"
        o, hq = props["o"], props["hq"]
        order = "1st" if o == 1 else "2nd"
        df = create_df_of_data(results, geo, o, hq)

        one.add_table(
            name,
            df,
            f"Comparison of all Eigenvalue analysis using {geo} {order} order elements",
            tbl_format=table_format,
        )

    for name, props in line_tables.items():
        geo = "line"
        o, hq = props["o"], props["hq"]
        order = "1st" if o == 1 else "2nd"
        df = create_df_of_data(results, geo, o, hq)
        if df is None:
            continue
        one.add_table(
            name,
            df,
            f"Comparison of all Eigenvalue analysis using {geo} {order} order elements",
            tbl_format=table_format,
        )

    for res in results:
        if os.environ.get("ADA_FEM_DO_NOT_SAVE_CACHE", None) is None:
            res.save_results_to_json(cache_dir / res.name)
        one.add_table(
            res.name,
            eig_data_to_df(res.eig_data, ["Mode", "Eigenvalue (real)"]),
            res.name,
        )

    one.compile("ADA-FEA-verification")


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    main(overwrite=False, execute=False)
