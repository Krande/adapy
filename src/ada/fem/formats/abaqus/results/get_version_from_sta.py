import pathlib
import re


def extract_abaqus_version(file_path: pathlib.Path | str) -> str:
    """
    Parse the given .sta file for a line like:
      Abaqus/Standard 2021.HF4                  DATE 17-okt-2022 TIME 17:25:31
    or
      Abaqus/Standard Learning Edition Unofficial Packaging Version
                   DATE 08-mai-2025 TIME 20:05:50
    and return the version substring.
    If not found, returns None.
    """
    if isinstance(file_path, str):
        file_path = pathlib.Path(file_path)

    version_re = re.compile(r"^\s*Abaqus/Standard\s+(.+?)\s+DATE")
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = version_re.search(line)
            if m:
                return m.group(1).strip()
    return "N/A"
