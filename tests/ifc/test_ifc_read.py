from ada import Assembly
from ada.config import Settings
from ada.core.utils import download_to

test_folder = Settings.test_dir / "ifc_basics"


def test_import_arcboundary():
    url_root = "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/"
    url = url_root + "master/IFC%204.0/NURBS/Bentley%20Building%20Designer/SolidsAndSheets/WithArcBoundary.ifc"
    dest = test_folder / "WithArcBoundary.ifc"
    download_to(dest, url)

    a = Assembly("MyAssembly")
    a.read_ifc(dest)
