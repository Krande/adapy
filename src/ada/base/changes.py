from enum import Enum


class ChangeAction(Enum):
    ADDED = "ADDED"
    DELETED = "DELETED"
    MODIFIED = "MODIFIED"
    NOCHANGE = "NOCHANGE"
    NOTDEFINED = "NOTDEFINED"
