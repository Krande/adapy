import logging
import os
import pathlib
from dataclasses import dataclass


@dataclass
class ModelExportOptions:
    export_props: bool = True
    import_props: bool = True
    include_ecc = True


def _get_platform_home():
    """Home location for each platform"""
    # _platform_home = dict(win32="C:/ADA", linux="/home/ADA", linux2="/home/ADA", macos="/home/ADA")
    # return _platform_home[sys.platform]

    return pathlib.Path.home() / "ADA"


class Settings:
    """The Properties object contains all general purpose properties relevant for Parts and Assemblies"""

    point_tol = 1e-4
    precision = 6
    mtol = 1e-3
    mmtol = 1
    valid_units = ["m", "mm"]

    safe_deletion = True

    convert_bad_names = False
    convert_bad_names_for_fem = True
    use_occ_bounding_box_algo = False

    force_param_profiles = True
    silence_display = False
    use_experimental_cache = False

    # IFC export settings
    model_export: ModelExportOptions = ModelExportOptions()

    # FEM analysis settings
    if os.getenv("ADA_execute_dir", None) is not None:
        execute_dir = pathlib.Path(os.getenv("ADA_execute_dir", None))
    else:
        execute_dir = None

    # Visualization Settings
    use_new_visualize_api = False

    # Code Aster conversion specific settings
    ca_experimental_id_numbering = False

    debug = False
    _home = _get_platform_home()
    scratch_dir = pathlib.Path(os.getenv("ADA_scratch_dir", f"{_home}/Analyses"))
    temp_dir = pathlib.Path(os.getenv("ADA_temp_dir", f"{_home}/temp"))
    debug_dir = pathlib.Path(os.getenv("ADA_log_dir", f"{_home}/logs"))
    test_dir = pathlib.Path(os.getenv("ADA_test_dir", f"{_home}/tests"))
    tools_dir = pathlib.Path(os.getenv("ADA_tools_dir", f"{_home}/tools"))

    fem_exe_paths = dict(abaqus=None, ccx=None, sestra=None, usfos=None, code_aster=None)

    use_duplicate_log_filter = True

    @classmethod
    def default_ifc_settings(cls):
        from ada.ifc.utils import default_settings

        return default_settings()


class DuplicateFilter(logging.Filter):
    MAX_NUM = 3

    def __init__(self, name="", logger=None):
        super().__init__(name)
        self.last_log = None
        self.count = 0
        self.logger = logger

    def filter(self, record):
        # add other fields if you need more granular comparison, depends on your app
        if getattr(record, "suppress_filters", False):
            return True

        max_num = self.MAX_NUM
        current_log = (record.module, record.levelno, record.msg)

        if current_log == self.last_log:
            self.count += 1
            if self.count == max_num:
                record.msg = f"The previous message is repeated {self.count} times and will be ignored."
                return True
            elif self.count > max_num:
                return False

            return True

        if self.count > max_num:
            sup_str = f"It was suppressed {self.count - max_num} time(s)."
            self.logger.info(
                f"... The previous log message was suppressed after {max_num} repetitions. {sup_str}",
                extra={"suppress_filters": True},
            )

        self.last_log = current_log
        self.count = 1
        return True


def get_logger():
    _logger = logging.getLogger("ada")
    if Settings.use_duplicate_log_filter:
        _logger.addFilter(DuplicateFilter(logger=_logger))
    return _logger
