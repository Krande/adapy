from __future__ import annotations

from ada.sat.readers.bsplinesurface import create_bsplinesurface_from_sat


class SatReader:
    def __init__(self, sat_file):
        self.f = open(sat_file, "r")
        self.lineno = 0
        self.header = ""

    def _read_line(self):
        try:
            return next(self.f)
        except StopIteration:
            self.f.close()
            raise StopIteration

    def __next__(self):
        self.lineno += 1
        line = self._read_line()

        if self.lineno <= 3:
            self.header += line
            return line

        if "#" in line:
            return line

        while True:
            self.lineno += 1
            line += self._read_line()
            if "#" in line:
                break

        return line

    def __iter__(self):
        return self


class SatReaderFactory:
    def __init__(self, sat_file):
        self.sat_file = sat_file
        self.entities = dict()

    def interpret_sat_object_data(self, sat_object_data: str):
        geom_id = sat_object_data.split()[0].replace("-", "")
        if "spline-surface" in sat_object_data:
            self.entities[geom_id] = create_bsplinesurface_from_sat(sat_object_data)

    def read_data(self):
        for sat_object in SatReader(self.sat_file):
            self.interpret_sat_object_data(sat_object)
