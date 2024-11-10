import json
import logging
import os
import pathlib
from typing import Any, Dict, List, NamedTuple, Optional, Type, Union

_filename = "ada_config.toml"
_env_prefix = "ADA_"
_env_config_file = "CONFIG_FILE"
_cwd = os.getcwd()
_tmp_dir = pathlib.Path(_cwd) / "temp"
_home_dir = pathlib.Path.home() / "ADA"


class ConfigError(Exception):
    pass


def strtobool(val: str) -> int:
    """
    Convert a string representation of truth to 1 or 0.

    True values are 'y', 'yes', 't', 'true', 'on', and '1';
    False values are 'n', 'no', 'f', 'false', 'off', and '0'.
    Raises ValueError if 'val' is anything else.
    """
    val = val.lower()
    if val in {"y", "yes", "t", "true", "on", "1"}:
        return 1
    elif val in {"n", "no", "f", "false", "off", "0"}:
        return 0
    else:
        raise ValueError(f"invalid truth value {val!r}")


class ConfigEntry(NamedTuple):
    name: str
    cast: Type
    default: Any = None
    required: bool = True

    def full_name(self, section=""):
        if section:
            section += "_"
        return f"{section}{self.name}"

    def env_var(self, section=""):
        return f"{_env_prefix}{self.full_name(section).upper()}"

    def casted(self, value):
        if self.cast is bool:
            try:
                value = strtobool(str(value))
            except ValueError as e:
                raise ConfigError(f"{self.name}: {e}")

        return self.cast(value)


class ConfigSection(NamedTuple):
    name: str
    entries: List[ConfigEntry]
    required: bool = True


class Config:
    # Singleton ("danger danger!" yeah I know).
    # Config class structure based on the wonderful https://github.com/mamba-org/quetz
    _config_map = [
        ConfigSection(
            "general",
            [
                ConfigEntry("point_tol", float, 1e-4),
                ConfigEntry("precision", int, 6),
                ConfigEntry("mtol", float, 1e-3),
                ConfigEntry("mmtol", int, 1),
                ConfigEntry("valid_units", list, ["m", "mm"]),
                ConfigEntry("safe_deletion", bool, True),
                ConfigEntry("convert_bad_names", bool, False),
                ConfigEntry("convert_bad_names_for_fem", bool, True),
                ConfigEntry("use_occ_bounding_box_algo", bool, False),
                ConfigEntry("force_param_profiles", bool, True),
                ConfigEntry("silence_display", bool, False),
                ConfigEntry("use_experimental_cache", bool, False),
                ConfigEntry("use_duplicate_log_filter", bool, True),
                ConfigEntry("debug", bool, False),
                ConfigEntry("debug_dir", pathlib.Path, _tmp_dir / "logs"),
                ConfigEntry("temp_dir", pathlib.Path, _tmp_dir),
                ConfigEntry("home_dir", pathlib.Path, _home_dir),
                ConfigEntry("tools_dir", pathlib.Path, _home_dir / "tools"),
                ConfigEntry("occ_silent_fail", bool, False),
                ConfigEntry("add_trace_to_exception", bool, False),
            ],
        ),
        ConfigSection(
            "ifc",
            [
                ConfigEntry("export_props", bool, True),
                ConfigEntry("import_props", bool, True),
                ConfigEntry("export_include_ecc", bool, True),
                ConfigEntry("import_shape_geom", bool, False),
            ],
        ),
        ConfigSection(
            "gxml",
            [
                ConfigEntry("import_advanced_faces", bool, True),
            ],
        ),
        ConfigSection(
            "sat",
            [
                ConfigEntry("read_curve_ignore_bspline", bool, False),
                ConfigEntry("import_raise_exception_on_failed_advanced_face", bool, False),
            ],
        ),
        ConfigSection(
            "fea",
            [
                ConfigEntry("execute_dir", str, None, False),
                ConfigEntry(
                    "fem_exe_paths", dict, dict(abaqus=None, ccx=None, sestra=None, usfos=None, code_aster=None)
                ),
                ConfigEntry("scratch_dir", pathlib.Path, _tmp_dir / "scratch"),
                ConfigEntry("test_dir", pathlib.Path, _tmp_dir / "tests"),
            ],
        ),
        ConfigSection(
            "meshing",
            [
                ConfigEntry("open_viewer_breakpoint_names", list[str], None, required=False),
            ],
        ),
        ConfigSection(
            "code_aster",
            [ConfigEntry("ca_experimental_id_numbering", bool, False)],
        ),
        ConfigSection(
            "fem_convert_options",
            [
                ConfigEntry("ecc_to_mpc", bool, True),
                ConfigEntry("hinges_to_coupling", bool, True),
                ConfigEntry("fem2concepts_include_ecc", bool, False),
            ],
        ),
        ConfigSection(
            "procedures",
            [
                ConfigEntry("script_dir", pathlib.Path, None, required=False),
                ConfigEntry("use_ifc_convert", bool, False, required=False),
            ],
        ),
        ConfigSection(
            "websockets",
            [
                ConfigEntry("server_temp_dir", pathlib.Path, None, False),
                ConfigEntry("auto_load_temp_files", bool, False, False),
                ConfigEntry("external_files_dirs", list[pathlib.Path], None, required=False),
            ],
        ),
    ]
    _config_dirs = [_cwd]
    _config_files = [os.path.join(d, _filename) for d in _config_dirs]

    _instances: Dict[Optional[str], "Config"] = {}

    def __new__(cls, deployment_config: str = None):
        if not deployment_config and None in cls._instances:
            return cls._instances[None]

        try:
            path = os.path.abspath(cls.find_file(deployment_config))
        except TypeError:
            # if not config path exists, set it to empty string.
            path = ""

        if path not in cls._instances:
            config = super().__new__(cls)
            config.init(path)
            cls._instances[path] = config
            # optimization - for default config path we also store the instance
            # under None key
            if not deployment_config:
                cls._instances[None] = config
        return cls._instances[path]

    @classmethod
    def find_file(cls, deployment_config: str = None):
        env_file_name = f"{_env_prefix}{_env_config_file}"
        config_file_env = os.getenv(env_file_name)
        deployment_config_files = []
        for f in (deployment_config, config_file_env):
            if f and os.path.isfile(f):
                deployment_config_files.append(f)

        # In order, get configuration from:
        # _site_dir, _user_dir, deployment_config, config_file_env
        for f in cls._config_files + deployment_config_files:
            if os.path.isfile(f):
                return f

    def reload_config(self):
        self.config.update(self._get_environ_config())
        self._trigger_update_config()

    def update_config_globally(self, key: str, value: Any):
        """Updates an environment variable and triggers a config reload."""
        key_upper = f"{_env_prefix}{key.upper()}"
        os.environ[key_upper] = str(value)
        self.config.update(self._get_environ_config())
        self._trigger_update_config()

    def init(self, path: str) -> None:
        """Load configurations from various places.

        Order of importance for configuration is:
        host < user profile < deployment < configuration file from env var < value from
        env var

        Parameters
        ----------
        deployment_config : str, optional
            The configuration stored at deployment level
        """

        self.config: Dict[str, Any] = {}

        # only try to get config from config file if it exists.
        if path:
            self.config.update(self._read_config(path))

        self.config.update(self._get_environ_config())
        self._trigger_update_config()

    def _trigger_update_config(self):
        def set_entry_attr(entry, section=""):
            value = self._get_value(entry, section)

            setattr(self, entry.full_name(section), value)

        for item in self._config_map:
            if isinstance(item, ConfigSection) and (item.required or item.name in self.config):
                for entry in item.entries:
                    set_entry_attr(entry, item.name)
            elif isinstance(item, ConfigEntry):
                set_entry_attr(item)

    def _get_value(self, entry: ConfigEntry, section: str = "") -> Union[str, bool, None]:
        """Get an entry value from a configuration mapping.

        Parameters
        ----------
        entry : ConfigEntry
            The entry to search
        section : str
            The section the entry belongs to

        Returns
        -------
        value : Union[str, bool]
            The entry value
        """
        try:
            if section:
                value = self.config[section][entry.name]
            else:
                value = self.config[entry.name]

            return entry.casted(value)

        except KeyError:
            if entry.default is not None:
                if callable(entry.default):
                    return entry.default()
                return entry.default

        msg = f"'{entry.name}' unset but no default specified"
        if section:
            msg += f" for section '{section}'"

        if entry.required:
            raise ConfigError(msg)

        return None

    def _read_config(self, filename: str) -> Dict[str, Any]:
        """Read a configuration file from its path.

        Parameters
        ----------
        filename : str
            The path of the configuration file

        Returns
        -------
        configuration : Dict[str, str]
            The mapping of configuration variables found in the file
        """
        import tomllib as toml

        with open(filename, "rb") as f:
            try:
                return dict(toml.load(f))
            except toml.TOMLDecodeError as e:
                raise ConfigError(f"failed to load config file '{filename}': {e}")

    def _find_first_level_config(self, section_name: str) -> Union[ConfigSection, ConfigEntry, None]:
        """Find the section or entry at first level of config_map.

        Parameters
        ----------
        section_name : str
            The name of the section to find.

        Returns
        -------
        section : Union[ConfigSection, ConfigEntry, None]
            The section or entry found, else None.
        """
        for item in self._config_map:
            if section_name == item.name:
                return item
        return None

    def _correct_environ_config_list_value(self, value: str) -> Union[str, List[str]]:
        """Correct a value from environ that should be a list.

        Parameters
        ----------
        value : str
            The env variable value to correct.
        Returns
        -------
        corrected_value : Union[str, List[str]]
            Original value if no correction needed, else the corrected list of
            strings value.
        """
        corrected_value = value
        if isinstance(value, str):
            if "[" in value:
                if "'" in value:
                    value = value.replace("'", '"')
                corrected_value = json.loads(value)
            elif "," in value and "[" not in value:
                corrected_value = value.split(",")

            # clear all empty strings in list
            if isinstance(corrected_value, list):
                corrected_value = [v for v in corrected_value if v]

        return corrected_value

    def _get_environ_config(self) -> Dict[str, Any]:
        """Looks into environment variables if some matches with config_map.

        Returns
        -------
        configuration : Dict[str, str]
            The mapping of configuration variables found in environment variables.
        """
        config: Dict[str, Any] = {}
        # get QUETZ environment variables.
        quetz_var = {key: value for key, value in os.environ.items() if key.startswith(_env_prefix)}
        for var, value in quetz_var.items():
            value = self._correct_environ_config_list_value(value)
            splitted_key = var.split("_")
            config_key = splitted_key[1].lower()
            idx = 2

            # look for the first level of config_map.
            # It must be done in loop as the key itself can contains '_'.
            first_level = None
            while idx < len(splitted_key):
                first_level = self._find_first_level_config(config_key)
                if first_level:
                    break
                config_key += f"_{splitted_key[idx].lower()}"
                idx += 1

            # no first_level found, the variable is useless.
            if not first_level:
                continue
            # the first level is an entry, add it to the config.
            if isinstance(first_level, ConfigEntry):
                config[first_level.name] = value
            # the first level is a section.
            elif isinstance(first_level, ConfigSection):
                entry = "_".join(splitted_key[idx:]).lower()
                # the entry does not exist in section, the variable is useless.
                if entry not in [section_entry.name for section_entry in first_level.entries]:
                    continue
                # add the entry to the config.
                if first_level.name not in config:
                    config[first_level.name]: Dict[str, Any] = {}
                config[first_level.name]["_".join(splitted_key[idx:]).lower()] = value

        return config


class DuplicateFilter(logging.Filter):
    MAX_NUM = 5

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


def configure_logger():
    # Note to self! Without declaring basicConfig, the logger will not respond to any change in the logging level
    logging.basicConfig(format="[%(asctime)s: %(levelname)s/%(name)s] | %(message)s")
    _logger = logging.getLogger("ada")

    # config = Config()
    # if config.general_use_duplicate_log_filter:
    #     _logger.addFilter(DuplicateFilter(logger=_logger))


def get_logger():
    return logging.getLogger("ada")


logger = get_logger()
