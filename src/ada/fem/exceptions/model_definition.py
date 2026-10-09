class NoBoundaryConditionsApplied(Exception):
    pass


class NoLoadsApplied(Exception):
    pass


class UnsupportedLoadType(Exception):
    pass


class FemSetNameExists(Exception):
    def __init__(self, name):
        self.name = name
        self.message = f"FemSet {name} already exists"
        super(FemSetNameExists, self).__init__(self.message)


class DoesNotSupportMultiPart(Exception):
    pass


class ConflictingBoundaryConditions(ValueError):
    """A node's dof given two conditions a writer cannot merge: prescribed by one boundary condition and held (at 0),
    or prescribed through another node set, by another."""

    #: Why each writer cannot take the two conditions.
    WHY = {
        "code_aster": "Code_Aster stops on a dof held by two charges (<ASSEMBLA_26>) and one value would have to win",
        "calculix": "CalculiX lets the step's prescribed value replace the hold (*BOUNDARY in a step modifies the "
        "model's), so one value would win without a word",
    }

    def __init__(self, node: int, dof: str, bc: str, other: str, how: str, writer: str = "code_aster"):
        self.node, self.dof, self.bc, self.other, self.writer = node, dof, bc, other, writer
        super().__init__(
            f"{writer} writer: node {node} {dof} is prescribed by '{bc}' and '{other}' {how}. {self.WHY[writer]}; "
            "give the node's dof one boundary condition (take the node out of one of the two node sets)."
        )
