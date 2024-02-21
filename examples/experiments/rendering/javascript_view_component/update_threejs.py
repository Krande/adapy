import os

import requests


def download_file(url: str, file_path: str) -> None:
    """
    Downloads a file from a given URL and saves it to a specified path.
    """
    response = requests.get(url)
    response.raise_for_status()  # Raises an HTTPError if the HTTP request returned an unsuccessful status code

    with open(file_path, "wb") as file:
        file.write(response.content)


def update_file(version: int, file_name: str, base_url: str) -> None:
    """
    Updates a local file to the latest version from a GitHub repository.
    """
    # Construct the full URL
    url = f"{base_url}/r{version}/{file_name}"

    # Delete the file if it exists
    if os.path.exists(file_name):
        os.remove(file_name)

    # Download the file
    download_file(url, file_name)


def main() -> None:
    # Version of three.js to pull files from
    version = 159

    # Base URLs for different types of files
    build_base_url = "https://raw.githubusercontent.com/mrdoob/three.js"
    examples_base_url = f"{build_base_url}/examples/js"

    # Update files
    update_file(version, "three.min.js", f"{build_base_url}/build")
    update_file(version, "controls/TrackballControls.js", f"{examples_base_url}/controls")
    update_file(version, "loaders/GLTFLoader.js", f"{examples_base_url}/loaders")


if __name__ == "__main__":
    main()
