from dataclasses import dataclass


@dataclass
class Point:
    x: float
    y: float
    z: float

    def __iter__(self):
        return iter((self.x, self.y, self.z))
