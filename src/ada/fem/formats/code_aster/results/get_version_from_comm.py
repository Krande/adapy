import pathlib
import re


def get_code_aster_version_from_mess(file_path: str | pathlib.Path) -> str:
    """
    Parse the given mess file for a line like:
      Version 17.1.0 modifiée le 01/08/2024
    and return '17.1.0'. If not found, returns None.
    """
    if isinstance(file_path, str):
        file_path = pathlib.Path(file_path)

    # matches 1 or more digits + dot + digits + dot + digits
    version_re = re.compile(r"\s*Version\s+(\d+\.\d+\.\d+)")
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = version_re.search(line)
            if m:
                return m.group(1)
    return "N/A"
