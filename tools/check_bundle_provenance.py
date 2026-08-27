"""Assert the committed viewer bundle was built from this repository, and only this repository.

``src/ada/visit/rendering/resources/index.zip`` is a compiled frontend bundle
committed as a binary and shipped as package data, so it goes out inside every
published wheel. It is also the one file in the tree that nobody reads in review.

Those two facts met: a bundle rebuilt on a machine with plugin overlays enabled
(``ADA_PLUGINS_EXTRA`` set, out-of-tree packages dropped into
``src/frontend/packages/plugins/``) silently absorbed the compiled source of
packages that live in other repositories — their ids, their design system, their
user-visible strings — and a text diff of the pull request showed nothing but
``Bin 776265 -> 1002877 bytes``.

The check
---------
Extract the bundle, collect every UI-looking string literal in it, and assert
each one occurs somewhere in this repository's frontend inputs. A string with no
source here did not come from here.

The corpus deliberately includes ``node_modules``: vendored strings from
dependencies are legitimately in the bundle and absent from ``src``. Without it
a clean bundle reports hundreds of false orphans, which is the difference
between a check people trust and one they learn to skip. That is why this must
run after ``npm ci``.

Measured on the two real blobs — the committed bundle and the polluted one, with
the same corpus (a full ``npm ci`` tree, 10,338 files):

    committed  5,606 strings      0 orphans  (14 baselined, see below)
    polluted   7,631 strings  1,694 orphans

The threshold is zero new orphans, not a count. Fourteen strings in the clean
bundle are ASSEMBLED by the bundler and so appear nowhere in the tree verbatim;
they are pinned by exact text in ``tools/bundle_provenance_baseline.txt``, each
with the source line it is assembled from. Pinning by text rather than by number
keeps the gate at "nothing new" instead of letting it decay into a budget.

The orphan list is the diagnostic: it names the panels, buttons and tooltips
that came from outside, which is exactly what a reviewer needs in order to act.

Rejected alternative, recorded so it is not re-proposed: gating on
``window.FRONTEND_SHA`` ending in ``-dirty``. Both blobs are ``-dirty`` —
unavoidably, since writing ``index.zip`` dirties the very tree the build is
measuring — so that gate would fail every legitimate bundle.

Note this is NOT what a ``.gitignore`` on the overlay directory would catch. In
the real incident no overlay directory was ever committed; the tracked bundle
was rebuilt around one. Ignoring the overlay is worth doing, but it addresses a
different mistake.

Usage:
    python tools/check_bundle_provenance.py
    python tools/check_bundle_provenance.py --bundle path/to/index.zip -v
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "src" / "ada" / "visit" / "rendering" / "resources" / "index.zip"
BASELINE = Path(__file__).resolve().parent / "bundle_provenance_baseline.txt"
FRONTEND = ROOT / "src" / "frontend"

CORPUS_ROOTS = ("src", "packages", "index.html", "node_modules")
CORPUS_SUFFIXES = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".css",
    ".html",
    ".json",
    ".md",
}

# A UI-looking string: starts with a letter, made of characters a label, tooltip
# or message would use. Narrow on purpose — minified identifiers, base64 and hex
# would swamp the signal and every one of them would be a false orphan.
STRING_RE = re.compile(r"""["'`]([A-Za-z][A-Za-z0-9 ._:/()\-+%#'&,?!]{3,119})["'`]""")
HEXISH_RE = re.compile(r"^[0-9a-fA-F]+$")
_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def bundle_strings(bundle: Path) -> set[str]:
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        if names != ["index.html"]:
            raise SystemExit(
                f"error: {bundle} should contain exactly one member named index.html, "
                f"found {names}.\nA changed shape means the build changed; look before "
                f"relaxing this."
            )
        text = z.read("index.html").decode("utf-8", "replace")
    return {s for s in STRING_RE.findall(text) if not HEXISH_RE.match(s)}


def corpus_files() -> list[Path]:
    out: list[Path] = []
    for rel in CORPUS_ROOTS:
        p = FRONTEND / rel
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += [f for f in p.rglob("*") if f.is_file() and f.suffix in CORPUS_SUFFIXES]
    return out


def find_orphans(wanted: set[str], files: list[Path]) -> set[str]:
    """Strings with no occurrence anywhere in the corpus.

    Streams the corpus and discards matches as they are found, rather than
    concatenating it — node_modules is large, and the remaining set collapses
    quickly, so this stays fast without holding the corpus in memory.
    """
    # Whitespace is normalised on both sides. The source wraps long class lists
    # and long prose across several lines, so a verbatim comparison would report
    # strings that ARE in the tree merely because the tree wrapped them
    # differently.
    remaining = {s: _norm(s) for s in wanted}
    for f in files:
        if not remaining:
            break
        try:
            text = _norm(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        remaining = {s: n for s, n in remaining.items() if n not in text}
    return set(remaining)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", type=Path, default=BUNDLE)
    ap.add_argument(
        "--frontend", type=Path, default=None, help="src/frontend to use as the corpus (default: this checkout)"
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--limit", type=int, default=40, help="how many orphans to print")
    args = ap.parse_args()

    global FRONTEND
    if args.frontend:
        FRONTEND = args.frontend.resolve()

    if not args.bundle.is_file():
        print(f"error: {args.bundle} not found", file=sys.stderr)
        return 1
    if not (FRONTEND / "node_modules").is_dir():
        print(
            "error: src/frontend/node_modules is missing. The corpus needs it, or every "
            "vendored string reads as an orphan. Run `npm ci` in src/frontend first.",
            file=sys.stderr,
        )
        return 1

    strings = bundle_strings(args.bundle)
    files = corpus_files()
    if args.verbose:
        print(f"bundle strings: {len(strings)}   corpus files: {len(files)}")

    orphans = find_orphans(strings, files)

    # A handful of strings are ASSEMBLED by the bundler and so never appear
    # verbatim in the tree: `+`-concatenated class lists and prose, JSX text
    # nodes split over lines, HTML entities the bundler decodes. They are
    # baselined by exact text rather than by count, so the gate stays "no NEW
    # orphan" instead of degrading into a threshold nobody revisits. See the
    # header of the baseline file for the bar an entry has to clear.
    #
    # Compared whitespace-normalised, like the corpus scan above: several of these
    # entries end in a significant trailing space (a class fragment meant to be
    # concatenated), and a text file whose meaning depends on invisible trailing
    # whitespace is a file the next editor silently breaks.
    baseline = set()
    if BASELINE.is_file():
        baseline = {
            _norm(ln)
            for ln in BASELINE.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        }
    matched = {_norm(s) for s in orphans} & baseline
    stale = baseline - matched
    orphans = {s for s in orphans if _norm(s) not in baseline}
    if stale and args.verbose:
        print(f"note: {len(stale)} baseline entries no longer orphaned; prune them")

    if not orphans:
        print(
            f"OK: all {len(strings)} bundle strings trace to this repository "
            f"({len(baseline)} baselined as bundler-assembled)."
        )
        return 0

    shown = sorted(orphans)[: args.limit]
    print(
        f"\nFAIL: {len(orphans)} of {len(strings)} strings in {args.bundle.name} do not "
        f"occur anywhere in src/frontend.\n",
        file=sys.stderr,
    )
    for s in shown:
        print(f"  {s!r}", file=sys.stderr)
    if len(orphans) > len(shown):
        print(f"  … and {len(orphans) - len(shown)} more", file=sys.stderr)
    print(
        "\nThe bundle was almost certainly rebuilt with out-of-tree plugins overlaid.\n"
        "Rebuild it from a clean checkout with no plugin environment variables set,\n"
        "or restore the committed blob:\n"
        "    git checkout origin/main -- src/ada/visit/rendering/resources/index.zip",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
