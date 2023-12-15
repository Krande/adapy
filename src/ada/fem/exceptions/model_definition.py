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
