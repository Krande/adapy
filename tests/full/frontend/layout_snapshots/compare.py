"""Compare two capture labels: pixels (tolerance 0) and the DOM snapshot (exact, after normalising build ids).

Usage: python compare.py <shotdir> <a> <b>

Writes <state>__diff_<a>_<b>.png for every pixel difference and prints a table.
Exits non-zero if any state differs.
"""

import re
import sys
from pathlib import Path

from PIL import Image, ImageChops

# Build-identity values that differ between any two builds, and React useId-style ids.
NORMALISE = [
    (re.compile(r"window\.UNIQUE_VERSION_ID = \d+; window\.FRONTEND_SHA = \\\"[^\\]*\\\""), "<build-id>"),
    (re.compile(r"\(([0-9a-f]{9})(-dirty)?\)"), "(<sha>)"),
    (re.compile(r"attr-search-values-[0-9a-z]+"), "attr-search-values-<id>"),
]


def norm(s: str) -> str:
    for rx, rep in NORMALISE:
        s = rx.sub(rep, s)
    return s


def compare_pixels(pa: Path, pb: Path, diff_path: Path) -> str:
    ia, ib = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
    if ia.size != ib.size:
        return f"size {ia.size} vs {ib.size}"
    diff = ImageChops.difference(ia, ib)
    bbox = diff.getbbox()
    if bbox is None:
        return "identical"
    r, g, b = diff.split()
    n = ia.width * ia.height - ImageChops.lighter(ImageChops.lighter(r, g), b).histogram()[0]
    diff.point(lambda v: 255 if v else 0).save(diff_path)
    return f"{n} px differ, bbox {bbox}"


def compare_dom(da: str, db: str) -> str:
    if da == db:
        return "identical"
    la, lb = da.splitlines(), db.splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return f"DIFFERS (first at line {i + 1}: {x.strip()[:80]!r} vs {y.strip()[:80]!r})"
    return f"DIFFERS (length {len(la)} vs {len(lb)})"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    shots, a, b = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    rows = []
    for pa in sorted(shots.glob(f"*__{a}.png")):
        state = pa.name[: -len(f"__{a}.png")]
        pb = shots / f"{state}__{b}.png"
        if not pb.exists():
            continue
        px = compare_pixels(pa, pb, shots / f"{state}__diff_{a}_{b}.png")
        da = norm((shots / f"{state}__{a}.dom.json").read_text(encoding="utf-8"))
        db = norm((shots / f"{state}__{b}.dom.json").read_text(encoding="utf-8"))
        rows.append((state, px, compare_dom(da, db)))

    if not rows:
        print(f"no states captured for both {a} and {b}")
        sys.exit(1)
    w = max(len(r[0]) for r in rows)
    print(f"{a} vs {b}")
    for state, px, dom in rows:
        print(f"  {state:<{w}}  pixels: {px:<40}  dom: {dom}")
    sys.exit(0 if all(px == "identical" and dom == "identical" for _, px, dom in rows) else 1)


if __name__ == "__main__":
    main()
