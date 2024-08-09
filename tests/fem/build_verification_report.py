import json
import logging
import os
import pathlib

import fem.build_report_utils as ru
from conftest import beam
from dotenv import load_dotenv
from paradoc import OneDoc
from paradoc.common import TableFormat
from test_fem_eig_cantilever import test_fem_eig

import ada
from ada.config import logger
from ada.fem.formats.abaqus.config import AbaqusSetup

load_dotenv()


def simulate(
    bm, el_order, geom_repr, analysis_software, use_hex_quad, use_reduced_int, eig_modes, overwrite, execute
) -> list[ru.FeaVerificationResult]:
    results = []
    for elo in el_order:
        for geo in geom_repr:
            for soft in analysis_software:
                for hexquad in use_hex_quad:
                    for uri in use_reduced_int:
                        try:
                            result = test_fem_eig(
                                bm,
                                soft,
                                geo,
                                elo,
                                hexquad,
                                reduced_integration=uri,
                                short_name_map=ru.short_name_map,
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
                        metadata["reduced_integration"] = uri

                        fvr = ru.postprocess_result(result, metadata)

                        results.append(fvr)

    if len(results) == 0:
        raise ValueError("No results are located")

    return results


def build_fea_report(bm: ada.Beam, results, eig_modes, cache_dir=None):
    version_cache = cache_dir / "software_versions.json"

    # Hardcoded calculix and code aster versions for now
    ccx_ver = "2.21"
    ca_ver = "17.1.0"

    version_dict = dict()
    if version_cache.exists():
        version_dict = json.loads(version_cache.read_text())

    if ru.ABAQUS_EXE is not None:
        aba_version = ru.get_abaqus_version()
    else:
        aba_version = version_dict.get("abaqus", "2021")

    if ru.SESTRA_EXE is not None:
        ses_version = ru.get_sesam_version()
    else:
        ses_version = version_dict.get("sesam", "10")

    # save version dict to cache
    with open(version_cache, "w") as f:
        json.dump(version_dict, f, indent=4)

    one = OneDoc("report")
    one.variables = dict(
        geom_specifics=str(bm),
        ca_version=ca_ver,
        ccx_version=ccx_ver,
        aba_version=aba_version,
        ses_version=ses_version,
        num_modes=eig_modes,
    )

    table_format = TableFormat(font_size=6, float_fmt=".2f")

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

        df = ru.create_df_of_data(results, geo, o, None)
        if df is None:
            continue

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
        df = ru.create_df_of_data(results, geo, o, hq)
        if df is None:
            continue

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
        df = ru.create_df_of_data(results, geo, o, hq)
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
            if cache_dir is not None:
                res.save_results_to_json(cache_dir / res.name)
        one.add_table(
            res.name,
            ru.eig_data_to_df(res.eig_data, ["Mode", "Eigenvalue (real)"]),
            res.name,
        )

    one.compile("ADA-FEA-verification")


def main(overwrite, execute):
    if ru.ODB_DUMP_EXE is not None:
        AbaqusSetup.set_default_post_processor(ru.post_processing_abaqus)

    software = ru.get_available_software()

    el_order = [1, 2]
    geom_repr = ["line", "shell", "solid"]
    eig_modes = 11
    uhq = [False, True]  # use hex or quad elements instead of tet or tri respectively
    uri = [False, True]  # use reduced integration

    bm = beam()

    results = simulate(bm, el_order, geom_repr, software, uhq, uri, eig_modes, overwrite, execute)

    cache_dir = pathlib.Path("").resolve().absolute() / ".cache"
    os.makedirs(cache_dir, exist_ok=True)

    ru.retrieve_cached_results(results, cache_dir)

    build_fea_report(bm, results, eig_modes, cache_dir)


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    main(overwrite=False, execute=False)
