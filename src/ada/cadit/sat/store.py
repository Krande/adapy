from __future__ import annotations

from typing import Iterable

from ada.cadit.sat.read.advanced_face import create_advanced_face_from_sat
from ada.cadit.sat.read.bsplinesurface import create_bsplinesurface_from_sat, ACISReferenceDataError
from ada.cadit.sat.read.curve import create_bspline_curve_from_sat
from ada.cadit.sat.read.face import PlateFactory
from ada.config import logger
from ada.geom.surfaces import (
    AdvancedFace,
    BSplineSurfaceWithKnots,
    RationalBSplineSurfaceWithKnots,
)


class SatReader:
    def __init__(self, sat_file):
        self.f = open(sat_file, "r")
        self.lineno = 0
        self.header = ""

    def _read_line(self):
        try:
            self.lineno += 1
            return next(self.f)
        except StopIteration:
            self.f.close()
            raise StopIteration

    def __next__(self):
        line = self._read_line()

        if self.lineno == 1:
            header = line
            while self.lineno <= 2:
                header += self._read_line()
            return header

        if "#" in line:
            return line

        while True:
            line += self._read_line()
            if "#" in line:
                break

        return line

    def __iter__(self):
        return self


class SatStore:
    def __init__(self):
        self.sat_data = dict()

    def add(self, sat_id: int | str, sat_object_data: str):
        if isinstance(sat_id, str):
            sat_id = int(sat_id)
        self.sat_data[sat_id] = sat_object_data

    def get(self, sat_id: int | str, return_str=False) -> list[str] | str:
        if isinstance(sat_id, str):
            if sat_id.startswith("$"):
                sat_id = sat_id.replace("$", "")
            sat_id = int(sat_id)

        if return_str:
            return self.sat_data[sat_id]

        return self.sat_data[sat_id].split()

    def get_name(self, sat_id: int | str) -> str:
        res = self.get(sat_id)
        ref_type = res[1]
        if ref_type.startswith("string"):
            return res[-2]
        elif ref_type.startswith("position"):
            return self.get_name(res[4])
        elif ref_type.startswith("rgb_color-st-attrib"):
            return self.get_name(res[4])
        else:
            raise NotImplementedError(f"Unknown reference type: {ref_type}")

    def iter(self) -> tuple[int, str]:
        for sat_id in sorted(self.sat_data.keys()):
            yield sat_id, self.sat_data[sat_id]


class SatReaderFactory:
    def __init__(self, sat_file):
        self.sat_file = sat_file
        self.entities = dict()
        self.sat_store = SatStore()
        self.plate_factory = PlateFactory(self.sat_store)
        self.header = ""

    def interpret_sat_object_data(self, sat_object_data: str):
        geom_id, sat_type = sat_object_data.split()[0:2]
        geom_id = geom_id.replace("-", "")

        if sat_type == "spline-surface":
            self.entities[geom_id] = create_bsplinesurface_from_sat(sat_object_data)
        elif sat_type == "face":
            self.entities[geom_id] = self.plate_factory.get_face_name_and_points(sat_object_data)
        else:
            self.entities[geom_id] = sat_object_data

    def store_sat_object_data(self):
        sat_reader = SatReader(self.sat_file)
        self.header = next(sat_reader)
        for sat_object_str in sat_reader:
            if sat_object_str.startswith("T @"):
                continue
            geom_id, sat_type = sat_object_str.split()[0:2]
            geom_id = geom_id.replace("-", "")
            self.sat_store.add(geom_id, sat_object_str)

    def iter_faces(self) -> Iterable[tuple[int, str]]:
        if len(self.sat_store.sat_data) == 0:
            self.store_sat_object_data()

        for sat_id, sat_object_data in self.sat_store.iter():
            geom_id, sat_type = sat_object_data.split()[0:2]
            if "face" == sat_type:
                yield sat_id, sat_object_data

    def iter_flat_plates(self) -> Iterable[tuple[str, list[tuple[float, float, float]]]]:
        for sat_id, face_data in self.iter_faces():
            if "spline-surface" in face_data:
                continue
            pl = self.plate_factory.get_face_name_and_points(face_data)
            if pl is None:
                continue
            yield pl

    def iter_bspline_objects(self) -> Iterable[BSplineSurfaceWithKnots | RationalBSplineSurfaceWithKnots]:
        for sat_id, face_data in self.iter_faces():
            if "spline-surface" not in face_data:
                continue
            yield create_bsplinesurface_from_sat(face_data)

    def iter_advanced_faces(self) -> Iterable[AdvancedFace]:
        for sat_id, face_data in self.iter_faces():
            ref = face_data.split()
            face_type = self.sat_store.get(ref[10])
            if face_type[1] == "spline-surface":
                try:
                    yield create_advanced_face_from_sat(sat_id, self.sat_store)
                except ACISReferenceDataError as e:
                    logger.info(f"Error creating AdvancedFace: {e}")

    def read_data(self):
        self.store_sat_object_data()
        for sat_id, sat_object in self.sat_store.iter():
            self.interpret_sat_object_data(sat_object)
