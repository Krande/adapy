from .common import FemBase


class FemSet(FemBase):
    """

    :param name: Name of Set
    :param members: Set Members
    :param set_type: Type of set (either 'nset' or 'elset')
    :param metadata: Metadata for object
    :param parent: Parent object
    """

    _valid_types = ["nset", "elset"]

    def __init__(self, name, members, set_type, metadata=None, parent=None):
        super().__init__(name, metadata, parent)
        self._set_type = set_type
        if self.type not in FemSet._valid_types:
            raise ValueError(f'set type "{set_type}" is not valid')
        self._members = members

    def __len__(self):
        return len(self._members)

    def __contains__(self, item):
        return item.id in self._members

    def __getitem__(self, index):
        return self._members[index]

    def __add__(self, other):
        """

        :param other:
        :type other: FemSet
        :return:
        """
        self.add_members(other.members)
        return self

    def add_members(self, members):
        """

        :param members:
        :type members: list
        """

        self._members += members

    @property
    def type(self):
        """

        :return: Type of set
        """
        return self._set_type.lower()

    @property
    def members(self):
        """

        :return: Members of set
        """
        return self._members

    @property
    def instance_num(self):
        """

        :return:
        """
        if self.on_assembly_level is True:
            return ",".join([f"{m}" for m in self.members])
        else:
            return ",".join(["{}.{}".format(self.parent.instance_name, m) for m in self.members])

    def __repr__(self):
        return f'FemSet({self.name}, type: "{self.type}", members: "{len(self.members)}")'
