"""Put simulation runs that were queued before batches existed into one batch.

Reads the scope's run records straight from the viewer's storage (configured the way the API is,
by its ``ADA_VIEWER_*`` environment), picks the runs of one provider whose label starts with a
prefix, and writes the batch on each record and into the batch index
(``ada.comms.rest.simulation_backfill``). The blob routes refuse writes under ``_simulations/``;
runs whose ids are known are moved through ``POST /api/scopes/{scope}/simulations/batches/{id}/runs``
(as their owner), and this is for picking them by label and date as an operator.

    python scripts/simulation/backfill_batch.py --scope users/<sub> --provider robot \\
        --label-prefix "frame " --exclude-label-prefix "frame v2 " \\
        --batch-id frame-welds --batch-label "Frame welds" --dry

``--dry`` reads and reports, and writes nothing: run it first. Running it again is harmless --
what is already in the batch is left as it is -- and sets the batch on the records of runs that
were running the first time (a worker may have written those again without it).
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from ada.comms.rest.config import load_settings  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.simulation_backfill import backfill_batch  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scope", required=True, help="the scope's storage prefix: users/<sub>, projects/<id> or shared")
    ap.add_argument("--provider", required=True, help="the simulation provider id the runs ran on")
    ap.add_argument("--label-prefix", required=True, help="the start of the runs' parameters.label")
    ap.add_argument("--exclude-label-prefix", action="append", default=[], help="leave out labels starting so")
    ap.add_argument("--from", dest="created_from", default=None, help="only runs created at or after (ISO)")
    ap.add_argument("--to", dest="created_to", default=None, help="only runs created at or before (ISO)")
    ap.add_argument("--batch-id", required=True)
    ap.add_argument("--batch-label", default=None)
    ap.add_argument("--source-kind", choices=("group", "node"), default=None)
    ap.add_argument("--source-key", default=None)
    ap.add_argument("--replace", action="store_true", help="move runs that are in another batch into this one")
    ap.add_argument("--dry", action="store_true", help="report what would be written, write nothing")
    args = ap.parse_args(argv)

    batch: dict = {"id": args.batch_id, "label": args.batch_label or args.batch_id}
    if args.source_kind or args.source_key:
        if not (args.source_kind and args.source_key):
            ap.error("--source-kind and --source-key go together")
        batch["source"] = {"kind": args.source_kind, "key": args.source_key}

    storage = Storage.from_settings(load_settings())
    report = asyncio.run(
        backfill_batch(
            storage,
            Scope.from_prefix(args.scope),
            batch=batch,
            provider=args.provider,
            label_prefix=args.label_prefix,
            exclude_label_prefixes=args.exclude_label_prefix,
            created_from=args.created_from,
            created_to=args.created_to,
            replace=args.replace,
            dry=args.dry,
        )
    )
    print("\n".join(report.lines()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
