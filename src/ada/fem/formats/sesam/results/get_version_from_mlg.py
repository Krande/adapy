import pathlib
import re


def extract_sestra_version(file_path: str | pathlib.Path) -> str:
    """
    Parse the given SESTRA.MLG file for the Sestra version.
    Looks for a line containing 'Sestra version X.Y.Z...' and returns the version string,
    or None if not found.
    """
    # Allow both str and pathlib.Path
    file_path = pathlib.Path(file_path)

    # Match 'Sestra version ' followed by digits and dots (e.g. 10.17.2.60)
    version_re = re.compile(r"\s*Sestra version ([\d]+(?:\.[\d]+)*)")
    with file_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            match = version_re.search(line)
            if match:
                return match.group(1)
    return "N/A"
