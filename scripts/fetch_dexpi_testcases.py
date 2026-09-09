"""Fetch the official DEXPI TrainingTestCases corpus for local, offline-safe testing.

adapy vendors two of those files under ``files/dexpi_files/vendor/`` (CC BY 4.0, see
``files/dexpi_files/vendor/NOTICE.md``) so the core DEXPI test suite has real, third-party
documents to run against without any network access. This script downloads the *rest* of the
corpus -- currently ~220 official DEXPI 1.2/1.3 example P&IDs -- into a git-ignored directory so
you can exercise the reader/writer against a much wider set of real-world files.

::

    python scripts/fetch_dexpi_testcases.py            # clone into files/dexpi_files/_external/
    python scripts/fetch_dexpi_testcases.py --force     # re-fetch, discarding what is there
    pixi run -e tests pytest tests/core/cadit/dexpi/test_external_corpus.py

This is a manual, opt-in local tool -- it is never imported or invoked by the test suite.
``tests/core/cadit/dexpi/test_external_corpus.py`` is skipped entirely when the target directory
does not exist, so ``pixi run -e tests test-core`` stays hermetic without it.

The corpus is fetched read-only and nothing is ever pushed anywhere. The preferred method is a
single shallow ``git clone``, with its ``.git`` metadata removed afterwards so what is left is a
plain directory of XML files; where ``git`` is unavailable, or its TLS stack cannot complete (some
locked-down environments fail schannel's certificate-revocation check for git specifically while
plain HTTPS still works), it falls back to downloading the repository's HTTPS archive (a zip, via
the stdlib ``urllib``) and extracting that instead. Either way, the download itself and every file
in it is Creative Commons Attribution 4.0 International (CC BY 4.0), copyright DEXPI e.V.
(https://dexpi.org/); see the source repository for the licence text.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

#: The upstream corpus. Read-only: this script only ever clones/downloads from it, never pushes.
REPO_URL = "https://gitlab.com/dexpi/TrainingTestCases.git"
_PROJECT_PATH = "dexpi/TrainingTestCases"

#: Repo-relative target, resolved from this script's own location so nothing here embeds a local
#: machine path. Matches the directory ``.gitignore`` already excludes.
_DEFAULT_TARGET = pathlib.Path(__file__).resolve().parent.parent / "files" / "dexpi_files" / "_external"


def _clone(target: pathlib.Path) -> bool:
    if shutil.which("git") is None:
        print("git is not on PATH -- trying a plain HTTPS archive download instead")
        return False
    print(f"cloning {REPO_URL}\n  -> {target}")
    result = subprocess.run(["git", "clone", "--depth", "1", REPO_URL, str(target)])
    if result.returncode != 0:
        print("git clone failed -- trying a plain HTTPS archive download instead", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return False
    # Leave a plain directory of files, not a live nested git checkout.
    shutil.rmtree(target / ".git", ignore_errors=True)
    return True


def _download_archive(target: pathlib.Path) -> bool:
    """Fall back for environments where ``git`` itself cannot reach the host (seen with some
    schannel/TLS-inspecting proxies) even though plain HTTPS works fine. No new dependency: just
    ``urllib`` and ``zipfile`` from the standard library."""
    for ref in ("main", "master"):
        url = f"https://gitlab.com/{_PROJECT_PATH}/-/archive/{ref}/TrainingTestCases-{ref}.zip"
        try:
            print(f"downloading archive ({ref}): {url}")
            with tempfile.TemporaryDirectory() as tmp_str:
                tmp = pathlib.Path(tmp_str)
                zip_path = tmp / "corpus.zip"
                urllib.request.urlretrieve(url, zip_path)  # noqa: S310 -- fixed https gitlab.com URL
                with zipfile.ZipFile(zip_path) as zf:
                    names = zf.namelist()
                    if not names:
                        continue
                    root = names[0].split("/", 1)[0]
                    zf.extractall(tmp)
                extracted = tmp / root
                if not extracted.is_dir():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(extracted), str(target))
                return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, zipfile.BadZipFile):
            continue
    return False


def fetch(target: pathlib.Path, *, force: bool = False) -> int:
    if target.exists():
        if not force:
            print(f"{target} already exists -- skipping (pass --force to re-fetch)")
            return 0
        print(f"--force: removing existing {target}")
        shutil.rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    ok = _clone(target) or _download_archive(target)
    if not ok:
        print(
            "could not fetch the corpus via git or HTTPS archive download -- check network access, "
            f"or download it manually from {REPO_URL} and extract it to {target}",
            file=sys.stderr,
        )
        return 1

    xml_files = sorted(target.rglob("*.xml"))
    print(
        f"fetched {len(xml_files)} XML file(s) into {target}\n(CC BY 4.0, DEXPI e.V. -- see the source repository for licence terms)"
    )
    print("run `pixi run -e tests pytest tests/core/cadit/dexpi/test_external_corpus.py` to exercise them")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--target",
        type=pathlib.Path,
        default=_DEFAULT_TARGET,
        help="where to place the corpus (default: files/dexpi_files/_external, repo-relative)",
    )
    parser.add_argument("--force", action="store_true", help="remove an existing target directory first")
    args = parser.parse_args(argv)
    return fetch(args.target, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
