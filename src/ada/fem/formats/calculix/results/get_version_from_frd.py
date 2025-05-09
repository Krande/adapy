import pathlib
import re


def extract_calculix_version(file_path: pathlib.Path | str) -> str:
    """
    Parse the given CalculiX .frd file for the version.
    Looks for a line like:
        1UVERSION           Version 2.22
    and returns '2.22', or None if not found.
    """

    if isinstance(file_path, str):
        file_path = pathlib.Path(file_path)

    version_re = re.compile(r"^\s*1UVERSION\s+Version\s+([\d.]+)")
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = version_re.match(line)
            if m:
                return m.group(1)
    return "N/A"
