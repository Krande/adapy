from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..concepts import GmshData, GmshSession


def ibeam(model: "GmshData", gmsh_session: "GmshSession"):
    raise NotImplementedError()
