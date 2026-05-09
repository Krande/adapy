_INSTALL_HINT = (
    "Install the meshio-bridge extras to enable this format: "
    "`pixi install -e meshio-bridge` or `pip install meshio`."
)


class MeshioNotAvailable(ImportError):
    """Raised when an adapy meshio-bridge format is invoked without
    meshio installed. Subclass of ImportError so callers that catch
    the import error see a helpful install hint instead of a bare
    `No module named 'meshio'`.
    """

    def __init__(self, target: str = "meshio bridge") -> None:
        super().__init__(f"{target} requires meshio. {_INSTALL_HINT}")
