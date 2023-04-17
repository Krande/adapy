import argparse
import os
import pathlib
import subprocess

import requests
import semver
import tomlkit

# https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html#supported-version-strings
RELEASE_TAG = os.environ.get("RELEASE_TAG", "alpha")
ROOT_DIR = pathlib.Path(__file__).parent
SETUP_FILE = ROOT_DIR / "pyproject.toml"
CONDA_URL = "https://api.anaconda.org/package/krande/ada-py"
PYPI_URL = "https://pypi.org/pypi/ada-py/json"


class Project:
    TOML_DATA = None
    CURR_VERSION = None

    @staticmethod
    def load():
        with open(SETUP_FILE, mode="r") as fp:
            Project.TOML_DATA = tomlkit.load(fp)
        Project.CURR_VERSION = Project.TOML_DATA["project"]["version"]


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


def compare_versions(ver_a: str, ver_b: str):
    return semver.compare(ver_a, ver_b)


def check_git_state():
    # Check for unstaged commits
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if status.stdout.strip():
        raise Exception("There are unstaged commits!")


def commit_and_tag(old_version, new_version):
    commit_message = f"bump {old_version} --> {new_version}"
    subprocess.run(["git", "commit", "-am", commit_message])
    subprocess.run(["git", "tag", "-a", new_version, "-m", commit_message])


def local_version_can_be_bumped():
    local_version = Project.CURR_VERSION
    latest_conda_version = get_latest_conda_version()
    latest_pypi_version = get_latest_pypi_version()

    # Make conda package numbering compatible with semver pre-release
    if "alpha" in latest_conda_version:
        latest_conda_version = latest_conda_version.replace("alpha", "-alpha")
    if "a" in latest_pypi_version:
        latest_pypi_version = latest_pypi_version.replace("a", "-alpha.")
    conda_compare = compare_versions(latest_conda_version, local_version)
    compare_versions(latest_pypi_version, local_version)
    # if conda_compare != pypi_compare:
    #     raise ValueError(
    #         f"{latest_conda_version=} is NOT the same as {latest_pypi_version=} from {PYPI_URL}. "
    #         "This needs to be fixed manually."
    #     )
    if conda_compare == -1:
        print(f"{latest_conda_version=} < {local_version=}. No need to bump.")
        return False
    elif conda_compare == 0:
        print(f"{latest_conda_version=} == {local_version=}. OK to bump.")
        return True
    else:
        raise ValueError(
            f"{latest_conda_version=} > {local_version=} from {CONDA_URL}. "
            "You might be working on an outdated branch. Please investigate"
        )


def check_formatting():
    args = "black --config pyproject.toml . && isort . && ruff . --fix"
    subprocess.check_output(args.split())


def bump_project_version(bump_level: str, skip_checks: bool = False, commit: bool = False):
    if skip_checks is False:
        check_formatting()
        check_git_state()

    if args.bump_level:
        print(f"Bumping version at {bump_level} level.")
    else:
        print("No bump level provided.")

    if local_version_can_be_bumped() is False and bump_level != BumpLevel.PRE_RELEASE:
        return None

    version = Project.CURR_VERSION
    toml_data = Project.TOML_DATA.copy()
    new_version = bump_version(version, bump_level)
    toml_data["project"]["version"] = new_version
    with open(SETUP_FILE, "w") as f:
        f.write(tomlkit.dumps(toml_data))

    if commit:
        commit_and_tag(version, new_version)


def bump_ci_pre_release_conda_formatted_only():
    version = Project.CURR_VERSION
    next_release = bump_version(version, BumpLevel.PRE_RELEASE)
    next_release_conda = next_release.replace("-", "")
    env_file = os.environ.get("GITHUB_OUTPUT", None)
    if env_file is not None:
        with open(env_file, "a") as myfile:
            myfile.write(f"VERSION={next_release_conda}")

    print(f"The next pre-release version of ada-py will be '{next_release}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bump version")
    parser.add_argument(
        "--bump-level",
        choices=["major", "minor", "patch", "pre-release"],
        help="Bump level (major, minor, patch or pre-release)",
    )
    parser.add_argument("--version-check-only", default=False, help="Only check versions.", action="store_true")
    parser.add_argument("--bump-ci-only", default=False, help="Only bump version.", action="store_true")
    parser.add_argument("--skip-checks", default=False, help="Skip checks.", action="store_true")
    parser.add_argument("--commit", default=False, help="Commit changes.", action="store_true")

    args = parser.parse_args()
    Project.load()

    if args.version_check_only:
        local_version_can_be_bumped()
    elif args.bump_ci_only:
        bump_ci_pre_release_conda_formatted_only()
    else:
        bump_project_version(args.bump_level, skip_checks=args.skip_checks)
