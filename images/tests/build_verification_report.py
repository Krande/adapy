import logging
from typing import Dict, Union

import pandas as pd
from common import append_df, eig_data_to_df, make_eig_fem
from paradoc import OneDoc
from paradoc.common import TableFormat

from ada import Beam, Material
from ada.materials.metals import CarbonSteel


def shorten_name(name, fem_format, geom_repr) -> str:
    short_name = name.replace("cantilever_EIG_", "")
    short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")
    geom_repr_map = dict(solid="so", line="li", shell="sh")
    short_name = short_name.replace(fem_format, short_name_map[fem_format])
    short_name = short_name.replace(geom_repr, geom_repr_map[geom_repr])

    return short_name


def run_and_postprocess(bm, soft, geo, elo, df_write_map, results, overwrite, execute, eig_modes):
    res = make_eig_fem(bm, soft, geo, elo, overwrite=overwrite, execute=execute, eigen_modes=eig_modes)
    if res is None or res.eigen_mode_data is None:
        logging.error("No result file is located")
        return None
    else:
        short_name = shorten_name(res.name, soft, geo)
        df = eig_data_to_df(res.eigen_mode_data, ["Mode", short_name])
        df_current = df_write_map.get(geo)
        if df_current is not None:
            df = df[short_name]
        df_write_map[geo] = append_df(df, df_current)
    results.append(res)


def simulate(bm, el_order, geom_repr, analysis_software, eig_modes, overwrite, execute):
    results = []
    merged_line_df = None
    merged_shell_df = None
    merged_solid_df = None

    df_write_map: Dict[str, Union[pd.DataFrame, None]] = dict(
        line=merged_line_df, shell=merged_shell_df, solid=merged_solid_df
    )

    for elo in el_order:
        for geo in geom_repr:
            for soft in analysis_software:
                try:
                    run_and_postprocess(bm, soft, geo, elo, df_write_map, results, overwrite, execute, eig_modes)
                except IOError as e:
                    logging.error(e)
    return results, df_write_map


def main(overwrite, execute):
    analysis_software = ["calculix", "code_aster"]
    el_order = [1, 2]
    geom_repr = ["line", "shell", "solid"]
    eig_modes = 11

    bm = Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        Material("S420", CarbonSteel("S420")),
    )

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

    results, df_write_map = simulate(bm, el_order, geom_repr, analysis_software, eig_modes, overwrite, execute)

    for geo in geom_repr:
        one.add_table(
            f"eig_compare_{geo}",
            df_write_map[geo],
            f"Comparison of all Eigenvalue analysis using {geo} elements",
            tbl_format=table_format,
        )

    for res in results:
        one.add_table(
            res.name,
            eig_data_to_df(res.eigen_mode_data, ["Mode", "Eigenvalue (real)"]),
            res.name,
        )

    one.compile("ADA-FEA-verification")


if __name__ == "__main__":
    main(overwrite=False, execute=False)
