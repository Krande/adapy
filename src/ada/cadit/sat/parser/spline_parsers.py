from __future__ import annotations

from typing import List, Optional

from ada.cadit.sat.parser.acis_entities import (
    AcisSplineCurveData,
    ClosureType,
    NurbsType,
)


def _to_floats_safe(tokens: List[str]) -> List[float]:
    vals: List[float] = []
    for t in tokens:
        try:
            vals.append(float(t))
        except Exception:
            # skip non-numeric tokens encountered in mixed lines
            continue
    return vals


def _find_token(tokens: List[str], candidates: List[str]) -> int:
    for i, t in enumerate(tokens):
        if t.lower() in candidates:
            return i
    return -1


def _next_number(tokens: List[str], start_idx: int) -> Optional[float]:
    for i in range(start_idx, len(tokens)):
        try:
            return float(tokens[i])
        except Exception:
            continue
    return None


def parse_spline_curve_data(spline_str: str) -> Optional[AcisSplineCurveData]:
    """
    Robustly parse ACIS spline-curve blocks embedded in entities like `intcurve-curve` and `pcurve`.

    Supports variants observed in SAT exports such as:
      - exppc 1 nubs 1 open 2 ...
      - lawintcur 5 full nubs 3 open 4 ...

    The parser tolerates optional integers and differing keyword order by scanning
    for key tokens instead of relying on fixed positions.
    """
    # Normalize block into logical lines (either newline or tab separated)
    lines = [ln.strip() for ln in spline_str.split("\n") if ln.strip()]
    if len(lines) == 1 and "\t" in lines[0]:
        lines = [ln.strip() for ln in lines[0].split("\t") if ln.strip()]

    if not lines:
        return None

    header_tokens = lines[0].split()
    if not header_tokens:
        return None

    subtype = header_tokens[0].lower()

    if subtype == "exppc":
        # exppc [opt_int] nubs|nurbs <degree> [open|periodic|closed] <n_knots> ...
        type_idx = _find_token(header_tokens, ["nubs", "nurbs"])  # can appear after an optional integer
        if type_idx == -1:
            curve_type = NurbsType.NURBS
            deg_val = _next_number(header_tokens, 1)
            degree = int(deg_val) if deg_val is not None else 1
        else:
            curve_type = NurbsType.NURBS if header_tokens[type_idx] == "nurbs" else NurbsType.NUBS
            deg_num = _next_number(header_tokens, type_idx + 1)
            degree = int(deg_num) if deg_num is not None else 1

        # Optional closure keyword
        closure = ClosureType.OPEN
        clos_idx = _find_token(header_tokens, ["open", "periodic", "closed"])  # choose first occurrence
        if clos_idx != -1:
            val = header_tokens[clos_idx].lower()
            if val == "open":
                closure = ClosureType.OPEN
            elif val == "periodic":
                closure = ClosureType.PERIODIC
            elif val == "closed":
                closure = ClosureType.CLOSED

        # Knots usually on the following line(s) as alternating value/multiplicity pairs
        knots: List[float] = []
        mults: List[int] = []
        if len(lines) > 1:
            nums = _to_floats_safe(lines[1].split())
            # Some exporters continue knot list further
            if len(lines) > 2:
                extra_nums = _to_floats_safe(lines[2].split())
                # Heuristic: if the next line does not look like a 3-value CP row, extend
                if not (_looks_like_cp_row(lines[2])):
                    nums.extend(extra_nums)
            for i in range(0, len(nums) - 1, 2):
                knots.append(nums[i])
                mults.append(int(round(nums[i + 1])))

        return AcisSplineCurveData(
            subtype=subtype,
            curve_type=curve_type,
            degree=degree,
            rational=(curve_type == NurbsType.NURBS),
            closure_u=closure,
            knots=knots,
            knot_multiplicities=mults,
            control_points=[],  # exppc in pcurves often doesn't include 3D points we need here
        )

    if subtype == "lawintcur":
        # lawintcur [opt_int] full nubs|nurbs <degree> open|periodic <n_knots>
        # Identify curve type token first
        type_idx = _find_token(header_tokens, ["nubs", "nurbs"])
        if type_idx == -1:
            # Sometimes the second token is the type if 'full' is omitted; fallback scan
            type_idx = _find_token(header_tokens[1:], ["nubs", "nurbs"]) + 1 if len(header_tokens) > 1 else -1
        curve_type = NurbsType.NURBS if type_idx != -1 and header_tokens[type_idx] == "nurbs" else NurbsType.NUBS
        deg_num = _next_number(header_tokens, (type_idx + 1) if type_idx != -1 else 1)
        degree = int(deg_num) if deg_num is not None else 3

        # Closure
        closure = ClosureType.OPEN
        clos_idx = _find_token(header_tokens, ["open", "periodic", "closed"])  # ACIS uses open/periodic
        num_knots = None
        if clos_idx != -1:
            val = header_tokens[clos_idx].lower()
            if val == "open":
                closure = ClosureType.OPEN
            elif val == "periodic":
                closure = ClosureType.PERIODIC
            elif val == "closed":
                closure = ClosureType.CLOSED

            # Try to read number of knots (usually follows closure keyword)
            nk_val = _next_number(header_tokens, clos_idx + 1)
            if nk_val is not None:
                num_knots = int(nk_val)

        # Gather knot pairs from subsequent lines (may span multiple lines)
        knots: List[float] = []
        mults: List[int] = []
        nums: List[float] = []

        expected_values = num_knots * 2 if num_knots is not None else None

        # Track where we stopped reading knots
        knot_end_idx = 1

        for i in range(1, len(lines)):
            knot_end_idx = i
            # If we have collected enough values, stop
            if expected_values is not None and len(nums) >= expected_values:
                break

            # Stop at the first line that looks like a 3-value control point row
            # ONLY if we don't have a specific expected count
            if expected_values is None and _looks_like_cp_row(lines[i]):
                break
            nums.extend(_to_floats_safe(lines[i].split()))

        # If we broke because we had enough values, we haven't consumed the current line 'i'
        # UNLESS we consumed it in the previous iteration.
        # Actually, let's simplify: verify if we consumed line 'i' or not.
        # If len(nums) >= expected BEFORE extend, we break and don't consume i.
        # If len(nums) < expected, we consume i.
        # So knot_end_idx should point to the first line NOT consumed for knots.

        # Re-implementing loop for clarity and correctness
        nums = []
        knot_end_idx = 1
        for i in range(1, len(lines)):
            if expected_values is not None and len(nums) >= expected_values:
                knot_end_idx = i
                break

            # Check heuristic only if count unknown
            if expected_values is None and _looks_like_cp_row(lines[i]):
                knot_end_idx = i
                break

            nums.extend(_to_floats_safe(lines[i].split()))
            knot_end_idx = i + 1

        for i in range(0, len(nums) - 1, 2):
            knots.append(nums[i])
            mults.append(int(round(nums[i + 1])))

        # Number of poles from multiplicities and degree if available
        # ACIS 'open' curves often imply multiplicity = degree + 1 at ends,
        # even if the file stores multiplicity = degree.
        # We must account for this to calculate the correct number of poles.
        current_sum_mults = sum(mults) if mults else 0
        if closure == ClosureType.OPEN and mults:
            if mults[0] == degree:
                current_sum_mults += 1
            if mults[-1] == degree:
                current_sum_mults += 1

        n_poles = (current_sum_mults - degree - 1) if mults else 0

        # Control points follow after knots; collect exactly n_poles rows
        cps: List[List[float]] = []
        # Find starting line index for CPs - start search from where knots ended
        cp_start = knot_end_idx
        for i in range(knot_end_idx, len(lines)):
            if _looks_like_cp_row(lines[i]):
                cp_start = i
                break

        if n_poles <= 0:
            # Fallback: try to parse all remaining rows with 3 or 4 floats
            for i in range(cp_start, len(lines)):
                row_vals = _to_floats_safe(lines[i].split())
                if len(row_vals) >= 3:
                    cps.append(row_vals[:4])
        else:
            i = cp_start
            while i < len(lines) and len(cps) < n_poles:
                row_vals = _to_floats_safe(lines[i].split())
                if len(row_vals) >= 3:
                    cps.append(row_vals[:4])
                i += 1

        return AcisSplineCurveData(
            subtype=subtype,
            curve_type=curve_type,
            degree=degree,
            rational=(curve_type == NurbsType.NURBS),
            closure_u=closure,
            knots=knots,
            knot_multiplicities=mults,
            control_points=cps,
        )

    # Unsupported subtype; try a generic numeric scan to avoid hard failures
    knots: List[float] = []
    mults: List[int] = []
    if len(lines) > 1:
        nums = _to_floats_safe(" ".join(lines[1:]).split())
        for i in range(0, len(nums) - 1, 2):
            knots.append(nums[i])
            mults.append(int(round(nums[i + 1])))

    return AcisSplineCurveData(
        subtype=subtype,
        curve_type=NurbsType.NURBS,
        degree=int(_next_number(header_tokens, 1) or 1),
        rational=True,
        closure_u=ClosureType.OPEN,
        knots=knots,
        knot_multiplicities=mults,
        control_points=[],
    )


def _looks_like_cp_row(line: str) -> bool:
    toks = line.split()
    if len(toks) < 3:
        return False
    cnt = 0
    for i in range(min(4, len(toks))):
        try:
            float(toks[i])
            cnt += 1
        except Exception:
            break
    return cnt >= 3
