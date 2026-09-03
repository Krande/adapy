"""Compare adapy's Xtract-style fields with Xtract ``result save`` listings.

Usage:
    python scripts/validate_sesam_xtract_oracle.py MODEL.SIN XTRACT_RESULTS_DIR
    python scripts/validate_sesam_xtract_oracle.py MODEL.SIN PROBE_DIR --allow-partial

The directory is expected to contain one tab-separated file per
``<position>__<attribute>`` pair, named that way -- for example
``element-average__D-STRESS.txt`` -- as written by Xtract's ``result save``
command. A journal that loops over the positions and attributes produces the
whole set in one run.

Files are streamed one result case at a time, so an oracle of a few hundred
megabytes is never loaded at once.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import math
import pathlib
import re
from dataclasses import dataclass

import numpy as np

from ada.fem.formats.sesam.results.read_sin import SinStreamReader, open_sin
from ada.fem.formats.sesam.results.xtract_catalog import semantic_name
from ada.fem.results.field_data import ElementFieldData, NodalFieldData


_HEADER_COMPONENT = re.compile(r"^(.*?)(?:\((\d+)\))?$")
_POSITION = {
    "nodes": "nodes",
    "elements": "elements",
    "element-average": "element_average",
    "resultpoints": "resultpoints",
}


@dataclass
class Listing:
    path: pathlib.Path
    handle: object
    reader: object
    header: list[str]

    @classmethod
    def open(cls, path: pathlib.Path) -> "Listing":
        handle = path.open("r", newline="", encoding="utf-8-sig")
        reader = csv.reader(handle, delimiter="\t")
        header = _clean_row(next(reader))
        return cls(path, handle, reader, header)

    def close(self) -> None:
        self.handle.close()

    def case_groups(self):
        rows = (_clean_row(row) for row in self.reader)
        rows = (row for row in rows if row)
        for case, group in itertools.groupby(rows, key=lambda row: row[1]):
            yield case, list(group)


def _clean_row(row) -> list[str]:
    clean = [value.strip() for value in row]
    while clean and not clean[-1]:
        clean.pop()
    return clean


def _field_name(path: pathlib.Path) -> str:
    position_text, attribute_text = path.stem.split("__", 1)
    position = _POSITION[position_text]
    attribute = attribute_text.upper()
    if position == "nodes" and attribute == "REACTION-FORCE":
        return "REACTION-FORCE"
    return semantic_name(position, attribute)


def _actual_values(result, field_name: str):
    fields = [field for field in result.results if field.name == field_name]
    if not fields:
        raise KeyError(f"reader did not emit {field_name!r}")
    first = fields[0]
    values: dict[tuple[int, str, int], float] = {}
    if isinstance(first, NodalFieldData):
        for field in fields:
            for row in np.asarray(field.values):
                entity = int(row[0])
                for ci, component in enumerate(field.components, start=1):
                    values[(entity, component, 1)] = float(row[ci])
        return values
    if isinstance(first, ElementFieldData):
        for field in fields:
            rows = np.asarray(field.values)
            positions = getattr(field, "int_positions", None) or []
            surface = getattr(getattr(field, "presentation", None), "surface", "")
            if surface == "selectable" and positions:
                top_slots = [i + 1 for i, position in enumerate(positions) if len(position) >= 3 and position[2] > 0]
                slot_map = {slot: i + 1 for i, slot in enumerate(top_slots)}
                rows = rows[np.isin(rows[:, 1].astype(int), top_slots)]
            else:
                slot_map = {}
            for row in rows:
                entity, slot = int(row[0]), int(row[1])
                slot = slot_map.get(slot, slot)
                for ci, component in enumerate(field.components, start=2):
                    values[(entity, component, slot)] = float(row[ci])
        return values
    raise TypeError(type(first))


def printed_half_ulp(text: str) -> float:
    """Half the value of the last digit Xtract actually printed.

    The listings are text, and Xtract does not print a fixed number of
    significant digits: ``1.10191e+07`` carries six, ``-96.8`` carries three.
    Comparing either against a float64 computation with one fixed relative
    tolerance is therefore meaningless -- too tight for the short prints, too
    loose for the long ones. A value that agrees with Xtract to every digit
    Xtract chose to write down is not a difference, and counting it as one was
    inflating the residual by more than an order of magnitude.

    So the tolerance comes from the text: the last printed digit's place value,
    halved. ``-96.8`` gives 0.05; ``1.10191e+07`` gives 50. Returns 0.0 when the
    text carries no decimal information to bound, leaving the caller's own
    rtol/atol to decide.
    """

    body = text.strip()
    if not body:
        return 0.0
    exponent = 0
    for marker in ("e", "E"):
        if marker in body:
            body, _, exp_text = body.partition(marker)
            try:
                exponent = int(exp_text)
            except ValueError:
                return 0.0
            break
    _, _, fraction = body.partition(".")
    # Digits after the point shift the last place right; the exponent shifts it
    # back left. A value printed with no point ("657") is bounded at its ones
    # digit, which is what a zero fraction length gives.
    return 0.5 * 10.0 ** (exponent - len(fraction))

def _compare_listing(listing: Listing, rows, result, *, rtol: float, atol: float):
    actual = _actual_values(result, _field_name(listing.path))
    entity_column = 4
    data_start = 5 if listing.path.name.startswith("nodes__") else 6
    checked = 0
    mismatches = []
    expected_keys = set()
    for row in rows:
        entity = int(row[entity_column])
        for column, expected_text in zip(listing.header[data_start:], row[data_start:]):
            match = _HEADER_COMPONENT.match(column)
            component = match.group(1)
            slot = int(match.group(2) or 1)
            key = (entity, component, slot)
            expected_keys.add(key)
            got = actual.get(key, math.nan)
            if not expected_text:
                if math.isfinite(got):
                    mismatches.append((entity, component, slot, math.nan, got, math.inf))
                continue
            expected = float(expected_text)
            checked += 1
            error = abs(got - expected)
            # The printed precision is the floor: agreeing to every digit Xtract
            # wrote down is agreement, whatever the caller asked for.
            limit = max(atol + rtol * abs(expected), printed_half_ulp(expected_text))
            if not math.isfinite(got) or error > limit:
                mismatches.append((entity, component, slot, expected, got, error))

    # A field must not contain an extra finite slot/entity that Xtract listed
    # as nonexistent. This catches accidental use of the raw lower surface.
    for key, got in actual.items():
        if math.isfinite(got) and key not in expected_keys:
            mismatches.append((*key, math.nan, got, math.inf))
    mismatches.sort(key=lambda item: item[-1], reverse=True)
    return checked, mismatches


def validate(
    sin_path: pathlib.Path,
    oracle_dir: pathlib.Path,
    *,
    rtol: float,
    atol: float,
    require_complete: bool = True,
) -> int:
    paths = sorted(oracle_dir.glob("*__*.txt"))
    if not paths:
        raise ValueError(f"found no generated listings in {oracle_dir}")
    if require_complete and len(paths) != 26:
        raise ValueError(f"expected 26 generated listings, found {len(paths)} in {oracle_dir}")
    listings = [Listing.open(path) for path in paths]
    groups = [listing.case_groups() for listing in listings]
    total_checked = 0
    total_mismatches = 0
    try:
        with SinStreamReader(open_sin(sin_path)) as reader:
            case_names = reader.try_step_names() or {}
            for step in reader._steps:
                expected_case = case_names.get(step, str(step))
                result = reader._load_step(step)
                print(f"case {step}: {expected_case}")
                for listing, group_iter in zip(listings, groups):
                    case, rows = next(group_iter)
                    if case != expected_case:
                        raise ValueError(f"{listing.path.name}: expected case {expected_case!r}, got {case!r}")
                    checked, mismatches = _compare_listing(listing, rows, result, rtol=rtol, atol=atol)
                    total_checked += checked
                    total_mismatches += len(mismatches)
                    if mismatches:
                        print(f"  FAIL {listing.path.name}: {len(mismatches)} mismatches; worst={mismatches[0]}")
                    else:
                        print(f"  ok   {listing.path.name}: {checked} values")
    finally:
        for listing in listings:
            listing.close()
    print(f"checked={total_checked} mismatches={total_mismatches}")
    return 1 if total_mismatches else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sin", type=pathlib.Path)
    parser.add_argument("oracle_dir", type=pathlib.Path)
    parser.add_argument("--rtol", type=float, default=5e-6)
    parser.add_argument("--atol", type=float, default=5e-6)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="compare any non-empty subset of <position>__<attribute>.txt listings",
    )
    args = parser.parse_args()
    return validate(
        args.sin,
        args.oracle_dir,
        rtol=args.rtol,
        atol=args.atol,
        require_complete=not args.allow_partial,
    )


if __name__ == "__main__":
    raise SystemExit(main())
