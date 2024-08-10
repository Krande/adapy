import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

from ada.fem.formats.general import FEATypes
from ada.fem.results import EigenDataSummary
from ada.fem.results.common import FEAResult
from ada.fem.results.sqlite_store import SQLiteFEAStore

load_dotenv()

short_name_map = dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")

ODB_DUMP_EXE = shutil.which("ODBDump")
if ODB_DUMP_EXE is not None:
    ODB_DUMP_EXE = pathlib.Path(ODB_DUMP_EXE)
else:
    ODB_DUMP_EXE = os.getenv("ODB_DUMP_EXE", None)
    if ODB_DUMP_EXE is not None:
        ODB_DUMP_EXE = pathlib.Path(ODB_DUMP_EXE)

ABAQUS_EXE = os.getenv("ADA_abaqus_exe", None)
if ABAQUS_EXE is not None:
    ABAQUS_EXE = pathlib.Path(ABAQUS_EXE)

SESTRA_EXE = os.getenv("ADA_sestra_exe", None)
if SESTRA_EXE is not None:
    SESTRA_EXE = pathlib.Path(SESTRA_EXE)


def get_package_version(package_names: list[str]) -> list[str]:
    # Construct the command to list packages in the current environment
    command = ["conda", "list"]

    # Execute the command using the current process env and capture the output
    result = subprocess.run(command, text=True, capture_output=True, shell=True)

    # Check if the command was successful
    if result.returncode != 0:
        raise Exception(f"Failed to list packages: {result.stderr}")
    versions = []
    for package_name in package_names:
        # Process the output to find the package version
        for line in result.stdout.splitlines():
            if not line.startswith(package_name):
                continue
            # Extract the version number, which is the second column
            parts = line.split()
            if len(parts) > 1:
                versions.append(parts[1])
                break
    if len(versions) == 0 or len(versions) != len(package_names):
        raise Exception(f"Failed to find versions for packages: {package_names}")

    # If the package is not found, return None
    return versions


def get_abaqus_version():
    command = [ABAQUS_EXE.as_posix(), "information=release"]
    result = subprocess.run(command, text=True, capture_output=True, shell=True)
    if result.returncode != 0:
        raise Exception(f"Failed to get Abaqus version: {result.stderr}")
    re_aba = re.compile(r"(?<=Abaqus\s)(?P<version>\d{4}).*?(?P<release>RELr\d+\s\d+)", re.MULTILINE | re.DOTALL)
    match = re_aba.search(result.stdout)
    version_number = match.group("version")
    release_info = match.group("release")
    return f"{version_number} ({release_info})"


def get_sesam_version():
    ses_path_str = SESTRA_EXE.as_posix()
    re_sestra = re.compile(r"V(?P<version>\d+\.\d+-\d+)", re.MULTILINE | re.DOTALL)
    match = re_sestra.search(ses_path_str)
    version_number = match.group("version")
    return f"{version_number}"


@dataclass
class FEAResultV2:
    name: str
    software: str | FEATypes
    results_db_path: pathlib.Path = None
    results_file_path: pathlib.Path = None

    def get_eig_summary(self) -> EigenDataSummary:
        """If the results are eigenvalue results, this method will return a summary of the eigenvalues and modes"""
        from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

        fea_store = SQLiteFEAStore(self.results_db_path)
        results_freq = fea_store.get_history_data("EIGFREQ")
        results_val = fea_store.get_history_data("EIGVAL")
        modes = []
        for eig_freq, eig_val in zip(results_freq, results_val):
            step = eig_freq[-2]
            freq = eig_freq[-1]
            val = eig_val[-1]
            m = EigenMode(int(step), f_hz=freq, eigenvalue=val)
            modes.append(m)
        if len(modes) == 0:
            raise ValueError(f"No eigenvalues found in the results for {self.name}")
        return EigenDataSummary(modes)


@dataclass
class FeaVerificationResult:
    name: str
    fem_format: str
    eig_data: EigenDataSummary = None
    results: FEAResult | FEAResultV2 = None
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
        uri = res.metadata.get("reduced_integration", False)

        if geom_repr != geo or elo != el_order:
            continue

        uri_str = ""
        if uri is True:
            uri_str = "R"

        if geo.upper() == "SOLID":
            s_str = "_"
            s_str += "TET" if hq is False else "HEX"
        elif geo.upper() == "SHELL":
            s_str = "_"
            s_str += "TRI" if hq is False else "QUAD"
        else:
            s_str = ""

        short_name = soft.replace(soft, short_name_map[soft])
        value_col = f"{short_name}{s_str}{uri_str}"
        df_current = eig_data_to_df(res.eig_data, ["Mode", value_col])
        new_col = df_current[value_col] if df_main is not None else df_current
        df_main = append_df(df_main, new_col)

    return df_main


def retrieve_cached_results(results: list[FeaVerificationResult], cache_dir: pathlib.Path):
    res_names = [r.name for r in results]
    res_elo = [r.metadata["elo"] for r in results]
    for res_file in cache_dir.rglob("*.json"):
        if res_file.stem == "software_versions":
            continue
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


def post_processing_abaqus(odb_file: pathlib.Path, overwrite=False) -> FEAResultV2:
    sqlite_file = odb_file.with_suffix(".sqlite")
    if sqlite_file.exists() is False or overwrite is True:
        result = subprocess.run(
            [ODB_DUMP_EXE, "--odbFile", odb_file, "--sqliteFile", sqlite_file], text=True, check=True
        )
        if result.returncode != 0:
            raise Exception(f"Failed to run ODBDump: {result.stderr}")

    return FEAResultV2(
        name=sqlite_file.stem, software="abaqus", results_db_path=sqlite_file, results_file_path=odb_file
    )


def get_available_software():
    software = []
    software.extend(["calculix", "code_aster"])

    if ABAQUS_EXE is not None:
        if not ABAQUS_EXE.exists():
            raise FileNotFoundError(f"ABAQUS executable not found at {ABAQUS_EXE}")
        software.append("abaqus")

    if SESTRA_EXE is not None:
        if not SESTRA_EXE.exists():
            raise FileNotFoundError(f"SESTRA executable not found at {SESTRA_EXE}")
        software.append("sesam")

    return software


def postprocess_result(result: FEAResult | FEAResultV2, metadata: dict) -> FeaVerificationResult:
    if isinstance(result.software, FEATypes):
        software = result.software.name.lower()
    else:
        software = result.software.lower()

    return FeaVerificationResult(
        name=result.name,
        fem_format=software,
        eig_data=result.get_eig_summary(),
        metadata=metadata,
    )
