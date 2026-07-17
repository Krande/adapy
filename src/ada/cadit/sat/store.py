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
        """Build the SAT subtype-reference table.

        Per the ACIS SAT Format v4.0 spec (Chapter 4 "Subtypes and
        References" + Chapter 9 example): subtype definitions are
        numbered sequentially starting at 0 as they appear in the save
        file. A subtype definition is any ``{ ... }`` pair that is
        *not* a back-reference (``{ ref n }``). **Nested subtypes get
        their own indices** — the example in Chapter 9 explicitly
        shows a ``surfintcur`` (#8) containing a nested ``exactsur``
        (#9) inside the same record.

        The previous implementation counted *records* with any braces
        (starting at 1) and assigned one index per record. That broke
        in two ways:

          * Off-by-one (started at 1 instead of 0).
          * Records with nested or multiple subtypes only contributed
            one index, so every reference past the first nested case
            in the file resolved to the wrong subtype.

        On large DNV/Genie-style models this mis-resolved thousands
        of ``ref N`` lookups onto neighbouring or completely unrelated
        records — the source of the "exppc surface peel landed on a
        different geometric region" symptom we tried to paper over
        with bbox-disjoint guards. Walking ``{`` / ``}`` brace pairs
        per spec resolves the same refs to the correct exactsurs.
        """
        from ada.cadit.sat.read.sat_entities import AcisSubType

        ref_store: dict[int, AcisSubType] = {}
        next_idx = 0
        for record in self.sat_records.values():
            s = record.get_as_string()
            i, n = 0, len(s)
            while i < n:
                c = s[i]
                if c != "{":
                    i += 1
                    continue
                # Inspect what follows the brace: skip whitespace and
                # check for the ``ref`` keyword (back-reference, not a
                # new definition).
                j = i + 1
                while j < n and s[j] in " \t\n\r":
                    j += 1
                if s[j : j + 4] == "ref ":
                    # Skip past the closing brace of this reference.
                    end = s.find("}", j)
                    if end < 0:
                        break
                    i = end + 1
                    continue
                # New subtype definition. Find matching ``}`` while
                # respecting nested braces — those become further
                # subtype definitions on subsequent loop iterations.
                depth = 1
                k = i + 1
                while k < n and depth > 0:
                    ck = s[k]
                    if ck == "{":
                        depth += 1
                    elif ck == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if depth != 0:
                    # Unbalanced — bail on this record.
                    break
                inner = s[i + 1 : k].strip()
                # AcisSubType.from_string expects a trailing ``}``;
                # historic ``get_sub_type_str`` produces ``<content> }``.
                ref_store[next_idx] = AcisSubType.from_string(inner + " }", record)
                next_idx += 1
                # Step into the subtype so nested definitions get
                # registered with their own (later) indices.
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
        # name (``EDGE00001234``) -> edge AcisRecord, built lazily. A curved
        # beam's XML guide carries only its two endpoints; the arc it swept
        # lives solely in the named ACIS edge, so resolving the name is the only
        # way to recover a curved beam instead of a straight chord.
        self._edge_name_map: dict[str, AcisRecord] | None = None

    def get_named_edge_curve(self, edge_name: str):
        """The geometry of a named SAT edge, or ``None``.

        Returns an :class:`ada.geom.curves.Circle` / ``Ellipse`` for an
        ``ellipse-curve`` edge (what a Genie curved beam's axis becomes), a
        :class:`ada.geom.curves.Line` for a ``straight-curve`` one, and a
        :class:`ada.geom.curves.BSplineCurveWithKnots` for an ``intcurve-curve``
        (spline-arc) axis so the caller can carry it as a :class:`BeamCurved`
        instead of collapsing the arc to its chord.
        """
        from ada.cadit.sat.read.bsplinecurves import create_bspline_curve_from_sat
        from ada.cadit.sat.read.curves import (
            create_line_from_sat,
            get_ellipse_curve,
        )

        if len(self.sat_store.sat_records) == 0:
            self.load_sat_data_from_file()

        if self._edge_name_map is None:
            name_map: dict[str, AcisRecord] = {}
            for rec in self.sat_store.iter():
                if rec.type != "edge":
                    continue
                try:
                    nm = rec.get_name()
                except Exception:
                    nm = ""
                if nm:
                    name_map[nm] = rec
            self._edge_name_map = name_map

        edge_rec = self._edge_name_map.get(edge_name)
        if edge_rec is None:
            return None
        # Edge record layout: ``-id edge $attrib -1 -1 $-1 $vstart t $vend t
        # $coedge $curve ...`` — the curve pointer sits at index 11.
        curve_rec = self.sat_store.get(edge_rec.chunks[11])
        if curve_rec is None:
            return None
        try:
            if curve_rec.type == "ellipse-curve":
                return get_ellipse_curve(curve_rec)
            if curve_rec.type == "straight-curve":
                return create_line_from_sat(curve_rec)
            if curve_rec.type == "intcurve-curve":
                return create_bspline_curve_from_sat(curve_rec)
        except Exception as e:  # noqa: BLE001 - a malformed record must not abort the whole read
            logger.debug(f"Failed reading curve of named edge {edge_name!r}: {e}")
        return None

    def load_sat_data_from_file(self):
        # Always reset store before loading
        # self.sat_store.clear()

        sat_reader = SatReader(self.sat_file)

        try:
            self.header = next(sat_reader)
        except StopIteration:
            # Empty SAT file → no header, no records
            self.header = None
            return

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

    def face_has_curved_edge(self, face_record: AcisRecord) -> bool:
        """Does any of the face's edges run on something other than a line?

        A planar face is only a polygon if its edges are straight. A flat plate
        bounded by a b-spline or an arc cannot be carried by ``ada.Plate``, whose
        outline is a point list — the edge would survive as a polyline, which
        silently changes the plate's area and stops it sharing an edge with the
        curved neighbour it was cut against.

        Walks loop -> coedge ring -> edge -> curve. Chunk indices are the record
        body offset by the leading id and type tokens.
        """
        loop = self.sat_store.get(face_record.chunks[7])
        seen_loops = set()
        while loop is not None and loop.type == "loop" and id(loop) not in seen_loops:
            seen_loops.add(id(loop))
            first = self.sat_store.get(loop.chunks[7])
            coedge, seen = first, set()
            while coedge is not None and coedge.type == "coedge" and id(coedge) not in seen:
                seen.add(id(coedge))
                edge = self.sat_store.get(coedge.chunks[9])
                if edge is not None and edge.type == "edge":
                    curve = self.sat_store.get(edge.chunks[11])
                    if curve is not None and curve.type != "straight-curve":
                        return True
                coedge = self.sat_store.get(coedge.chunks[6])
                if coedge is first:
                    break
            loop = self.sat_store.get(loop.chunks[6])
        return False

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
        # Aggregate failure stats per call. Counts and a small set of
        # example face names per (exception_type, message_head) are kept
        # so the caller can surface a single summary log at the end of
        # iteration instead of one debug-line per face (which gets
        # buried — historically the user perception was "everything
        # converted fine" while >50% of spline-surface faces silently
        # fell back to flat polygons).
        attempted = 0
        succeeded = 0
        # key: (exc_type_name, short_message) → (count, [example names])
        fail_stats: dict[tuple[str, str], tuple[int, list[str]]] = {}

        def _record_failure(name: str, e: BaseException) -> None:
            short = str(e).split(". ")[0][:80]
            key = (type(e).__name__, short)
            cnt, examples = fail_stats.get(key, (0, []))
            if len(examples) < 5:
                examples = examples + [name]
            fail_stats[key] = (cnt + 1, examples)

        # When ``gxml.curved_fallback_via_fill`` is on, surface-peel
        # failures fall through to a wire-only ``WireFilledFace`` —
        # OCC's BRepOffsetAPI_MakeFilling builds a smooth surface from
        # the boundary edges. The wire data (loop's coedges + 3D
        # BSpline edge curves) is independent of the surface peel, so
        # it's available even when ``get_face_surface`` raises. This
        # eliminates the rotated-flat fallback for the ~2700 plates
        # whose ACIS spline-surface records use the
        # ``{ ref → exppc → ... }`` procedural form we don't synthesise.
        use_fill = Config().gxml_curved_fallback_via_fill

        def _try_wire_filled(face_record):
            """Build a WireFilledFace from the loop's coedges. Returns
            None on any failure — caller treats that as "fallback path
            also failed, log + skip" rather than re-raising. The bounds
            extraction is the same primitive ``create_advanced_face_from_sat``
            uses, so any wire that succeeds for a *successful* face also
            succeeds here."""
            try:
                from ada.cadit.sat.read.advanced_face import get_face_bound

                bounds = get_face_bound(face_record)
                if not bounds:
                    return None
                return geo_su.WireFilledFace(bounds=bounds)
            except Exception as ex:
                logger.debug("WireFilledFace fallback failed: %s", ex)
                return None

        try:
            for face_record in self.iter_faces():
                face_surface = self.sat_store.get(face_record.chunks[10])
                if face_surface.type == "plane-surface":
                    # A flat face is not necessarily a polygon. Take it as an
                    # advanced face when any edge is curved, so the boundary
                    # survives as the curves it is; leave a genuinely
                    # straight-edged one to the flat path, where ada.Plate
                    # represents it exactly and more cheaply.
                    if not self.face_has_curved_edge(face_record):
                        continue
                elif face_surface.type != "spline-surface":
                    continue
                attempted += 1
                try:
                    advanced = create_advanced_face_from_sat(face_record)
                except (ACISReferenceDataError, ACISUnsupportedCurveType) as e:
                    name = face_record.get_name()
                    _record_failure(name, e)
                    err_msg = f"Unable to create face record {name}. Fallback to flat Plate due to: {e}"
                    if conf.general_add_trace_to_exception:
                        err_msg += f"\n{traceback.format_exc()}"
                    logger.debug(err_msg)
                    if Config().sat_import_raise_exception_on_failed_advanced_face:
                        raise e
                    if use_fill:
                        wff = _try_wire_filled(face_record)
                        if wff is not None:
                            succeeded += 1
                            yield face_record, wff
                    continue
                except BaseException as e:  # Let's catch ALL other exceptions here for now
                    name = face_record.get_name()
                    _record_failure(name, e)
                    err_msg = f"Unable to create face record {name}. Fallback to flat Plate due to: {e}"
                    if conf.general_add_trace_to_exception:
                        err_msg += f"\n{traceback.format_exc()}"
                    logger.debug(err_msg)
                    if Config().sat_import_raise_exception_on_failed_advanced_face:
                        raise e
                    if use_fill:
                        wff = _try_wire_filled(face_record)
                        if wff is not None:
                            succeeded += 1
                            yield face_record, wff
                    continue
                succeeded += 1
                yield face_record, advanced
        finally:
            # Persist + log even if the consumer aborts iteration early.
            self.advanced_face_stats = {
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": attempted - succeeded,
                "by_reason": {
                    f"{etype}: {msg}": (cnt, examples) for (etype, msg), (cnt, examples) in fail_stats.items()
                },
            }
            failed = attempted - succeeded
            if attempted > 0 and failed > 0:
                pct = 100.0 * failed / attempted
                top_lines = []
                for (etype, msg), (cnt, examples) in sorted(fail_stats.items(), key=lambda kv: -kv[1][0])[:5]:
                    sample = ", ".join(examples[:3])
                    top_lines.append(f"    [{etype}] {msg} — {cnt} faces (e.g. {sample})")
                top_str = "\n".join(top_lines) if top_lines else ""
                logger.warning(
                    "SAT advanced-face conversion: %d/%d (%.1f%%) failed and fell "
                    "back to flat polygons. Top failure modes:\n%s",
                    failed,
                    attempted,
                    pct,
                    top_str,
                )

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
