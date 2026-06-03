import os
import pathlib
import re
import xml.etree.ElementTree as ET


def _get_versions_xml_root() -> ET.Element | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None

    xml_path = pathlib.Path(appdata) / "DNVGL" / "ApplicationVersionManager" / "ApplicationVersions.xml"
    if not xml_path.exists():
        return None

    tree = ET.parse(xml_path)
    return tree.getroot()


def _get_default_exe_path(app_name: str) -> str | None:
    root = _get_versions_xml_root()
    if root is None:
        return None

    applications = root.find("Applications")
    if applications is None:
        return None

    for app in applications:
        if app.attrib.get("Name") == app_name:
            for version in app.findall("Version"):
                if version.attrib.get("IsDefault") == "True":
                    return version.attrib.get("ExeFilePath")
    return None


def get_genie_default_exe_path() -> str | None:
    genie_exe_env_var = os.getenv("ADA_GENIE_EXE")
    if genie_exe_env_var:
        return genie_exe_env_var

    return _get_default_exe_path("GeniE")


def get_sestra_default_exe_path() -> str | None:
    sestra_exe_env_var = os.getenv("ADA_SESTRA_EXE")
    if sestra_exe_env_var:
        return sestra_exe_env_var
    return _get_default_exe_path("Sestra")


def get_prepost_default_exe_path() -> str | None:
    prepost_exe_env_var = os.getenv("ADA_prepost_exe")
    if prepost_exe_env_var:
        return prepost_exe_env_var
    return _get_default_exe_path("Prepost")


_SESTRA_VERSION_RE = re.compile(r"V(?P<version>\d+\.\d+-\d+)")


def get_sestra_version(exe_path: str | None = None) -> str:
    """Extract the Sestra version from the executable path.

    Sestra's installation layout embeds the version in the path
    (eg `.../Sestra V10.16-02/sestra.exe`), so a path-string parse
    is cheaper than spawning the process.

    Raises FileNotFoundError if the path can't be resolved, ValueError
    if the path doesn't match the expected `Vxx.yy-zz` shape.
    """
    if exe_path is None:
        exe_path = get_sestra_default_exe_path()
    if exe_path is None:
        raise FileNotFoundError("ADA_SESTRA_EXE is unset and no default install found")
    match = _SESTRA_VERSION_RE.search(exe_path)
    if match is None:
        raise ValueError(f"could not parse sestra version from path: {exe_path!r}")
    return match.group("version")
