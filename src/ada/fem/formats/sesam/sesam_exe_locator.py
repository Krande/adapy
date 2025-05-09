import os
import pathlib
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
