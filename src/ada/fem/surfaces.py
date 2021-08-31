from .common import FemBase


class Surface(FemBase):
    """
    Documentation

        https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-surface.htm#simakey-r-surface__simakey-r-surface-s-datadesc5


    Parameters.

    :param name: Unique name of surface
    :param surf_type: Type of surface
    :param fem_set:
    :param weight_factor:
    :param id_refs: Explicitly defined by list of tuple [(elid/nid,spos), ..]
    :param parent:
    :param metadata:
    :type fem_set: FemSet
    """

    _valid_surf_types = ["ELEMENT", "NODE"]

    def __init__(
        self,
        name,
        surf_type,
        fem_set,
        weight_factor=None,
        face_id_label=None,
        id_refs=None,
        parent=None,
        metadata=None,
    ):
        super().__init__(name, metadata, parent)

        if surf_type not in self._valid_surf_types:
            raise ValueError(f'Surface type "{surf_type}" is currently not supported\\implemented')

        self._surf_type = surf_type
        self._fem_set = fem_set
        self._weight_factor = weight_factor
        self._face_id_label = face_id_label
        self._id_refs = id_refs

    @property
    def type(self):
        return self._surf_type

    @property
    def fem_set(self):
        return self._fem_set

    @property
    def weight_factor(self):
        return self._weight_factor

    @property
    def face_id_label(self):
        return self._face_id_label

    @property
    def id_refs(self):
        return self._id_refs
