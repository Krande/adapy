import os
import pathlib

import tomlkit
import requests
import subprocess
import argparse
import semver

# https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html#supported-version-strings
RELEASE_TAG = os.environ.get("RELEASE_TAG", "alpha")
ROOT_DIR = pathlib.Path(__file__).parent
SETUP_FILE = ROOT_DIR / "pyproject.toml"
CONDA_URL = "https://api.anaconda.org/package/krande/ada-py"
PYPI_URL = "https://pypi.org/pypi/ada-py/json"


class BumpLevel:
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    PRE_RELEASE = "pre-release"

    @classmethod
    def from_string(cls, s: str):
        if s == cls.MAJOR:
            return cls.MAJOR
        elif s == cls.MINOR:
            return cls.MINOR
        elif s == cls.PATCH:
            return cls.PATCH
        elif s == cls.PRE_RELEASE:
            return cls.PRE_RELEASE
        else:
            raise ValueError(f"Invalid bump level '{s}'")


def get_latest_conda_version():
    # Make a GET request to the URL
    response = requests.get(CONDA_URL)
    data = response.json()
    return data["files"][-1]["version"]


def get_latest_pypi_version():
    # Make a GET request to the URL
    response = requests.get(PYPI_URL)
    data = response.json()
    return data["info"]["version"]


def bump_version(current_version: str, bump_level: str) -> str:
    bump_level = BumpLevel.from_string(bump_level)
    ver = semver.VersionInfo.parse(current_version)
    if bump_level == BumpLevel.MAJOR:
        ver = ver.bump_major()
    elif bump_level == BumpLevel.MINOR:
        ver = ver.bump_minor()
    elif bump_level == BumpLevel.PATCH:
        ver = ver.bump_patch()
    elif bump_level == BumpLevel.PRE_RELEASE:
        ver = ver.bump_prerelease(RELEASE_TAG)
    else:
        raise ValueError(f"Invalid bump level '{bump_level}'")

    return str(ver)


def check_git_state():
    # Check for unstaged commits
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if status.stdout.strip():
        raise Exception("There are unstaged commits!")


def commit_and_tag(old_version, new_version):
    commit_message = f"bump {old_version} --> {new_version}"
    subprocess.run(["git", "commit", "-am", commit_message])
    subprocess.run(["git", "tag", "-a", new_version, "-m", commit_message])


def check_versions_across_distro(local_version: str):
    latest_conda = get_latest_conda_version()
    latest_pypi = get_latest_pypi_version()
    if local_version != latest_conda:
        raise ValueError(
            f"{latest_conda=} is NOT the same as {local_version=} from {CONDA_URL}. "
            "Please use bump_version.py to update the version."
        )
    if local_version != latest_pypi:
        raise ValueError(
            f"{latest_pypi=} is NOT the same as {local_version=} from {PYPI_URL}. "
            "Please use bump_version.py to update the version."
        )


def main(bump_level: str):
    check_git_state()
    if args.bump_level:
        print(f"Bumping version at {bump_level} level.")
    else:
        print("No bump level provided.")

    with open(SETUP_FILE, mode="r") as fp:
        toml_data = tomlkit.load(fp)
    version = toml_data["project"]["version"]

    check_versions_across_distro(version)

    new_version = bump_version(version, bump_level)
    toml_data["project"]["version"] = new_version
    with open(SETUP_FILE, "w") as f:
        f.write(tomlkit.dumps(toml_data))

    commit_and_tag(version, new_version)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bump version")
    parser.add_argument(
        "--bump-level",
        choices=["major", "minor", "patch", "pre-release"],
        help="Bump level (major, minor, patch or pre-release)",
    )

    args = parser.parse_args()

    main(args.bump_level)
