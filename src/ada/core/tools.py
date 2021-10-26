import os
import pathlib

from ada.config import Settings
from ada.core.utils import download_to, unzip_it

IFCCONVERT = Settings.tools_dir / "IfcConvert" / "IfcConvert.exe"
CODE_ASTER = Settings.tools_dir / "code_aster"
CALCULIX = Settings.tools_dir / "calculix"
PREPROMAX = Settings.tools_dir / "prepromax"


def download_tool(url, download_path):
    os.makedirs(download_path.parent, exist_ok=True)
    download_to(download_path, url)
    unzip_it(download_path, download_path.parent)


def download_ifc_convert(install_path=IFCCONVERT.parent, ifc_convert_v="v0.6.0-517b819-win64"):
    """
    Download IfcConvert from http://www.ifcopenshell.org/ifcconvert


    :param install_path:
    :param ifc_convert_v:
    :return:
    """
    install_path = pathlib.Path(install_path)

    v_file = install_path / ".version"

    if v_file.exists() is True:
        with open(v_file, "r") as f:
            v = f.read()
            if v == ifc_convert_v:
                print(f'Version "{ifc_convert_v}" is already installed. Skipping downloading')
                return None

    download_path = install_path / "ifc_convert.zip"
    url = f"https://s3.amazonaws.com/ifcopenshell-builds/IfcConvert-{ifc_convert_v}.zip"

    download_tool(url, download_path)

    with open(v_file, "w") as f:
        f.write(ifc_convert_v)


def download_code_aster_win(install_path=CODE_ASTER, code_aster_v="code-aster_v2019_std-win64"):
    download_path = install_path / "code_aster.zip"
    url = f"https://simulease.com/wp-content/uploads/2020/08/{code_aster_v}.zip"
    download_tool(url, download_path)


def download_calculix_win(install_path=CALCULIX, calculix_cae_version="v0.8.0/cae_20200725_windows"):
    download_path = install_path / "calculix.zip"
    url = f"https://github.com/calculix/cae/releases/download/{calculix_cae_version}.zip"
    download_tool(url, download_path)


def download_prepromax_win(install_path=PREPROMAX):
    download_path = install_path / "prepromax.zip"
    url = "https://prepomax.fs.um.si/Files/Downloads/PrePoMax%20v1.1.1.zip"
    download_tool(url, download_path)


if __name__ == "__main__":
    # download_calculix_win()
    # download_code_aster_win()
    download_prepromax_win()
