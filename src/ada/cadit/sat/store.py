from __future__ import annotations

import traceback
from typing import Iterable

import ada.geom.surfaces as geo_su
from ada.cadit.sat.exceptions import ACISReferenceDataError, ACISUnsupportedCurveType
from ada.cadit.sat.read.advanced_face import (
    create_advanced_face_from_sat,
    create_planar_face_from_sat,
)
from ada.cadit.sat.read.faces import PlateFactory
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import Config, logger
from ada.core.guid import create_guid
from ada.geom import Geometry
from ada.visit.colors import Color, color_dict


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
        self.sat_records: dict[int, AcisRecord] = dict()
        self._ref_store = None

    def add(self, sat_object_data: str):
        record = AcisRecord.from_string(sat_object_data)
        record.sat_store = self
        self.sat_records[record.index] = record

    def get(self, sat_id: int | str) -> AcisRecord | None:
        if isinstance(sat_id, str):
            if sat_id.startswith("$"):
                sat_id = sat_id.replace("$", "")
            sat_id = int(sat_id)
        if sat_id == -1:
            return None
        return self.sat_records[sat_id]

    def get_name(self, sat_id: int | str) -> str:
        string_attrib_record = self.get(sat_id)
        if string_attrib_record is None:
            return ""
        if string_attrib_record.type.startswith("string"):
            return string_attrib_record.chunks[-2]
        elif string_attrib_record.type.startswith("position"):
            return self.get_name(string_attrib_record.chunks[4])
        elif string_attrib_record.type.startswith("rgb_color-st-attrib"):
            return self.get_name(string_attrib_record.chunks[4])
        elif string_attrib_record.type == "CachedPlaneAttribute-DNV-attrib":
            return self.get_name(string_attrib_record.chunks[5])
        else:
            raise NotImplementedError(f"Unknown reference type: {string_attrib_record.type}")

    def iter(self) -> Iterable[AcisRecord]:
        for sat_record in self.sat_records.values():
            yield sat_record

    def _create_ref_store(self):
        ref_store = dict()
        i = 1
        for record in self.sat_records.values():
            rstr = record.get_as_string()
            if "{" in rstr and "}" in rstr:
                ref_store[i] = record.get_sub_type()
                i += 1

        return ref_store

    def get_ref(self, ref_id):
        if self._ref_store is None:
            self._ref_store = self._create_ref_store()

        if isinstance(ref_id, str):
            if ref_id.startswith("$"):
                ref_id = ref_id.replace("$", "")
            ref_id = int(ref_id)

        return self._ref_store[ref_id]


class SatReaderFactory:
    def __init__(self, sat_file):
        self.sat_file = sat_file
        self.sat_store = SatStore()
        self.plate_factory = PlateFactory(self.sat_store)
        self.header = ""
        self.failed_faces = []

    def load_sat_data_from_file(self):
        sat_reader = SatReader(self.sat_file)
        self.header = next(sat_reader)
        for sat_object_str in sat_reader:
            if sat_object_str.startswith("T @"):
                continue
            self.sat_store.add(sat_object_str)

    def iter_faces(self) -> Iterable[AcisRecord]:
        if len(self.sat_store.sat_records) == 0:
            self.load_sat_data_from_file()

        for sat_record in self.sat_store.iter():
            if sat_record.type == "face":
                yield sat_record

    def iter_flat_plates(self) -> Iterable[tuple[str, list[tuple[float, float, float]]]]:
        for face_record in self.iter_faces():
            # face_surface = self.sat_store.get(face_record.chunks[10])
            # if face_surface.type == "spline-surface":
            #     continue

            # face_bound = create_planar_face_from_sat(face_record)
            # todo: add support for face_bound
            pl = self.plate_factory.get_face_name_and_points(face_record)
            if pl is None:
                continue
            yield pl

    def iter_advanced_faces(self) -> Iterable[tuple[AcisRecord, geo_su.AdvancedFace]]:
        conf = Config()
        for face_record in self.iter_faces():
            face_surface = self.sat_store.get(face_record.chunks[10])
            if face_surface.type != "spline-surface":
                continue
            try:
                yield face_record, create_advanced_face_from_sat(face_record)
            except (ACISReferenceDataError, ACISUnsupportedCurveType) as e:
                name = face_record.get_name()
                err_msg = f"Unable to create face record {name}. Fallback to flat Plate due to: {e}"
                if conf.general_add_trace_to_exception:
                    trace_msg = traceback.format_exc()
                    err_msg += f"\n{trace_msg}"
                logger.debug(err_msg)
                if Config().sat_import_raise_exception_on_failed_advanced_face:
                    raise e
            except BaseException as e:  # Let's catch ALL other exceptions here for now
                name = face_record.get_name()
                err_msg = f"Unable to create face record {name}. Fallback to flat Plate due to: {e}"
                if conf.general_add_trace_to_exception:
                    trace_msg = traceback.format_exc()
                    err_msg += f"\n{trace_msg}"
                logger.debug(err_msg)
                if Config().sat_import_raise_exception_on_failed_advanced_face:
                    raise e

    def iter_curved_face(self) -> Iterable[tuple[AcisRecord, Geometry]]:
        for i, (record, advanced_face) in enumerate(self.iter_advanced_faces()):
            if advanced_face is None:
                continue
            face_name = self.sat_store.get_name(record.chunks[2])
            yield face_name, Geometry(create_guid(), advanced_face, Color(*color_dict["light-gray"]))

    def iter_all_faces(self) -> Iterable[tuple[AcisRecord, geo_su.SURFACE_GEOM_TYPES]]:
        conf = Config()
        failed_faces = []
        for face_record in self.iter_faces():
            name = face_record.get_name()
            face_surface = self.sat_store.get(face_record.chunks[10])
            if face_surface.type == "spline-surface":
                try:
                    yield face_record, create_advanced_face_from_sat(face_record)
                except (ACISReferenceDataError, ACISUnsupportedCurveType) as e:
                    err_msg = f"Unable to create face record {name}. Fallback to flat Plate due to: {e}"
                    if conf.general_add_trace_to_exception:
                        trace_msg = traceback.format_exc()
                        err_msg += f"\n{trace_msg}"
                    logger.debug(err_msg)
                    failed_faces.append(name)
                    if Config().sat_import_raise_exception_on_failed_advanced_face:
                        raise e
            elif face_surface.type == "plane-surface":
                try:
                    yield face_record, create_planar_face_from_sat(face_record)
                except (ACISReferenceDataError, ACISUnsupportedCurveType) as e:
                    err_msg = f"Unable to create face record {name}. Fallback to flat Plate due to: {e}"
                    if conf.general_add_trace_to_exception:
                        trace_msg = traceback.format_exc()
                        err_msg += f"\n{trace_msg}"
                    logger.debug(err_msg)
                    failed_faces.append(name)
                    if Config().sat_import_raise_exception_on_failed_advanced_face:
                        raise e
            else:
                raise NotImplementedError(f"Unsupported face type: {face_surface.type}")

        self.failed_faces = failed_faces
