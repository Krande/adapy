import os
import pathlib
import re
import xml.etree.ElementTree as ET

_VERSION_DIR_RE = re.compile(r"V(?P<major>\d+)\.(?P<minor>\d+)-(?P<patch>\d+)")


def _find_latest_dnv_exe(product_prefix: str, exe_name: str, subdir: str = "Program") -> str | None:
    """Newest ``<program files>/DNV/<product_prefix> Vxx.yy-zz/<subdir>/<exe_name>``.

    A fallback for when ApplicationVersions.xml points at an uninstalled default
    (the manager leaves stale ``IsDefault`` entries): walk the DNV install root,
    keep the version dirs whose exe actually exists, and return the highest
    ``Vmajor.minor-patch``. ``product_prefix`` is e.g. ``"GeniE"``.

    Both program-files roots are walked: the 64-bit products live under ``Program Files``,
    while Presel is a 32-bit install under ``Program Files (x86)`` with its exe in ``Bin``.
    """
    candidates: list[tuple[tuple[int, int, int], str]] = []
    roots = {
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        os.environ.get("ProgramFiles(x86)"),
        r"C:\Program Files",
        r"C:\Program Files (x86)",
    }
    for root in roots:
        if not root:
            continue
        dnv = pathlib.Path(root) / "DNV"
        if not dnv.is_dir():
            continue
        for ver_dir in dnv.glob(f"{product_prefix} V*"):
            m = _VERSION_DIR_RE.search(ver_dir.name)
            if m is None:
                continue
            exe = ver_dir / subdir / exe_name
            if exe.is_file():
                key = (int(m.group("major")), int(m.group("minor")), int(m.group("patch")))
                candidates.append((key, str(exe)))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


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


def get_genie_runtime_default_exe_path() -> str | None:
    """Path to GenieRuntime.exe, the headless (no-GUI) Genie driver.

    Prefers the ``ADA_GENIE_RUNTIME_EXE`` override, else the default version
    registered under ``GenieRuntime`` in DNV's ApplicationVersions.xml. Used to
    import/verify a concept XML without opening the GUI (see
    ``ada.cadit.gxml.open_in_genie``).
    """
    genie_runtime_exe_env_var = os.getenv("ADA_GENIE_RUNTIME_EXE")
    if genie_runtime_exe_env_var:
        return genie_runtime_exe_env_var
    registered = _get_default_exe_path("GenieRuntime")
    if registered and pathlib.Path(registered).is_file():
        return registered
    # Registered default is stale/uninstalled — walk the DNV install root.
    return _find_latest_dnv_exe("GeniE", "GenieRuntime.exe")


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


def get_presel_default_exe_path() -> str | None:
    """Presel, the superelement assembler -- the program that checks a T-file's number.

    ``ADA_PRESEL_EXE`` wins; then the version manager's default; then the newest install
    found on disk. Presel ships 32-bit, under ``Program Files (x86)``, with the exe in ``Bin``.
    """
    presel_exe_env_var = os.getenv("ADA_PRESEL_EXE")
    if presel_exe_env_var:
        return presel_exe_env_var
    return _get_default_exe_path("Presel") or _find_latest_dnv_exe("Presel", "Presel.exe", subdir="Bin")


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
