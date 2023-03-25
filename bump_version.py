import os
import pathlib

import tomlkit
import argparse
import semver

# https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html#supported-version-strings
RELEASE_TAG = os.environ.get("RELEASE_TAG", "alpha")
ROOT_DIR = pathlib.Path(__file__).parent
SETUP_FILE = ROOT_DIR / "pyproject.toml"


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


def bump_version(current_version, bump_level) -> str:
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


def main(bump_level: str):
    if args.bump_level:
        print(f"Bumping version at {bump_level} level.")
    else:
        print("No bump level provided.")

    bump_level = BumpLevel.from_string(bump_level)

    with open(SETUP_FILE, mode="r") as fp:
        toml_data = tomlkit.load(fp)
    version = toml_data["project"]["version"]

    new_version = bump_version(version, bump_level)
    toml_data["project"]["version"] = new_version
    with open(SETUP_FILE, "w") as f:
        f.write(tomlkit.dumps(toml_data))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bump version")
    parser.add_argument(
        "--bump-level",
        choices=["major", "minor", "patch", "pre-release"],
        help="Bump level (major, minor, patch or pre-release)",
    )

    args = parser.parse_args()

    main(args.bump_level)
