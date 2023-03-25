import os

import requests

# https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/pkg-specs.html#supported-version-strings
RELEASE_TAG = os.environ.get("RELEASE_TAG", "alpha")


def main():
    url = "https://api.anaconda.org/package/krande/ada-py"

    # Make a GET request to the URL
    response = requests.get(url)
    data = response.json()
    i = -1
    while True:
        latest = data["releases"][i]["version"]
        if "None" in latest:
            i -= 1
            print(f"skipping {latest}")
            continue
        break
    print(f"The latest release version of ada-py is {latest}.")
    release = latest.split(".")
    if len(release) == 3:
        release[2] = str(int(release[2]) + 1) + f"{RELEASE_TAG}.1"
        next_release = ".".join(release)
    elif len(release) == 4:
        release[3] = str(int(release[3]) + 1)
        next_release = ".".join(release)
    else:
        raise ValueError(f"Invalid release version '{latest}'")

    env_file = os.environ.get("GITHUB_OUTPUT", None)
    if env_file is not None:
        with open(env_file, "a") as myfile:
            myfile.write(f"VERSION={next_release}")

    print(f"The next release version of ada-py will be '{next_release}'.")

    with open(SETUP_FILE, mode="rb") as fp:
        cfg = tomllib.load(fp)
    version = cfg["project"]["version"]
    cfg["project"]["version"] = f"{version}{RELEASE_TAG}"
    with open(SETUP_FILE.with_name('pyproject_edited.toml'), mode="wb") as fp:
        cfg = tomli_w.dump(cfg, fp)


if __name__ == "__main__":
    main()
