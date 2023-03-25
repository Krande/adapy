import os
import pathlib

import requests
import tomllib

# https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html#supported-version-strings
RELEASE_TAG = os.environ.get("RELEASE_TAG", "alpha")
ROOT_DIR = pathlib.Path(__file__).parent.parent.parent
SETUP_FILE = ROOT_DIR / "pyproject.toml"


def get_local_version() -> str:
    with open(SETUP_FILE, mode="rb") as fp:
        cfg = tomllib.load(fp)
    return cfg["project"]["version"]


def get_latest_conda_version():
    url = "https://api.anaconda.org/package/krande/ada-py"

    # Make a GET request to the URL
    response = requests.get(url)
    data = response.json()
    return data["releases"][-1]["version"]


def main():
    local_version = get_local_version()
    latest_conda_version = get_latest_conda_version()
    if latest_conda_version == local_version:
        raise ValueError(
            f"Latest release version '{latest_conda_version}' is the same as the current version '{local_version}' on {url}. "
            "Please use bumpversion to update the version."
        )
    print(
        f"Latest release version '{latest_conda_version}' is different from the current version '{local_version}' on {url}.")


if __name__ == "__main__":
    main()
