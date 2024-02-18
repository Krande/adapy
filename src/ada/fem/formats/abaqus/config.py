import json
import os
import pathlib

from ada.fem.formats.abaqus.execute import run_abaqus
from ada.fem.formats.abaqus.results.read_odb import read_odb_pckle_file
from ada.fem.formats.abaqus.write.writer import to_fem as to_fem_abaqus
from ada.fem.formats.fea_config import FrameworkConfig


def get_existing_path(paths) -> pathlib.Path:
    for p in map(pathlib.Path, paths):
        if p.exists():
            return p

    raise FileNotFoundError(f"Unable to find any of the paths: {paths}")


class AbaqusPaths:
    _instance = None
    _VS_PATHS = None
    _INTEL_FORT_PATHS = None
    _ABAQUS_PATH_MAP = None
    """
    Takes a config file with all relevant paths for Abaqus analyses using subroutines.

    {
        "VS_PATHS": [
            "C:\\Program Files\\Microsoft Visual Studio\\2022\\Professional\\VC\\Auxiliary\\Build",
            "C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\VC\\Auxiliary\\Build"
        ],
        "INTEL_FORT_PATHS": ["C:\\Program Files (x86)\\Intel\\oneAPI\\compiler\\2024.0\\env"],
        "ABAQUS_PATH_MAP": {
            "2024": "C:\\SIMULIA\\EstProducts\\2024\\win_b64\\code\\bin\\SMALauncher.exe",
            "2021": "C:\\SIMULIA\\EstProducts\\2021\\win_b64\\code\\bin\\ABQLauncher.exe"
        }
    }
    """

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(AbaqusPaths, cls).__new__(cls)
        return cls._instance

    @classmethod
    def update_paths(cls, config_file):
        with open(config_file, "r") as f:
            config = json.load(f)
        cls._VS_PATHS = config["VS_PATHS"]
        cls._INTEL_FORT_PATHS = config["INTEL_FORT_PATHS"]
        cls._ABAQUS_PATH_MAP = config["ABAQUS_PATH_MAP"]

    @classmethod
    def update_paths_from_env(cls):
        config_file = os.getenv("ADA_ABAQUS_CONFIG_FILE")
        if config_file is not None:
            cls.update_paths(config_file)

    @classmethod
    def vs_paths(cls) -> pathlib.Path:
        if cls._VS_PATHS is None:
            cls.update_paths_from_env()
        return get_existing_path(cls._VS_PATHS)

    @classmethod
    def intel_fort_path(cls) -> pathlib.Path:
        if cls._INTEL_FORT_PATHS is None:
            cls.update_paths_from_env()
        return get_existing_path(cls._INTEL_FORT_PATHS)

    @classmethod
    def abaqus_path_map(cls, version):
        if cls._ABAQUS_PATH_MAP is None:
            cls.update_paths_from_env()
        return cls._ABAQUS_PATH_MAP[str(version)]


class AbaqusSetup(FrameworkConfig):
    default_pre_processor = to_fem_abaqus
    default_executor = run_abaqus
    default_post_processor = read_odb_pckle_file
