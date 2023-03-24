import os
import pathlib

import requests
import tomli

# https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html#supported-version-strings
RELEASE_TAG = os.environ.get("RELEASE_TAG", "alpha")
ROOT_DIR = pathlib.Path(__file__).parent.parent.parent
SETUP_FILE = ROOT_DIR / "pyproject.toml"


def main():
    url = "https://api.anaconda.org/package/krande/ada-py"
    with open(SETUP_FILE, mode="rb") as fp:
        cfg = tomli.load(fp)
    version = cfg["project"]["version"]

    # Make a GET request to the URL
    response = requests.get(url)
    data = response.json()
    latest = data["releases"][-1]["version"]
    if latest == version:
        raise ValueError(
            f"Latest release version '{latest}' is the same as the current version '{version}' on {url}. "
            "Please use bumpversion to update the version."
        )
    print(f"Latest release version '{latest}' is different from the current version '{version}' on {url}.")


if __name__ == "__main__":
    main()
