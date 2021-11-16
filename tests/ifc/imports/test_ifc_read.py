from ada import Assembly
from ada.core.utils import download_to


def test_import_arcboundary(ifc_test_dir):
    url_root = "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/"
    url = url_root + "master/IFC%204.0/NURBS/Bentley%20Building%20Designer/SolidsAndSheets/WithArcBoundary.ifc"
    dest = ifc_test_dir / "WithArcBoundary.ifc"
    download_to(dest, url)

    a = Assembly("MyAssembly")
    a.read_ifc(dest)
