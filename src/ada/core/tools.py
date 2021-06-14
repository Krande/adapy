import os
import pathlib

from ada.config import Settings as _Settings

from .utils import download_to, unzip_it

IFCCONVERT = _Settings.tools_dir / "IfcConvert" / "IfcConvert.exe"


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
    os.makedirs(install_path, exist_ok=True)
    url = f"https://s3.amazonaws.com/ifcopenshell-builds/IfcConvert-{ifc_convert_v}.zip"
    download_to(download_path, url)
    unzip_it(download_path, download_path.parent)

    with open(v_file, "w") as f:
        f.write(ifc_convert_v)
