"""Drive Fakturama from the extracted JSON - the whole spec, one command.

    python -m automation.cli out\\invoice.json

Runs 1.3 through 5.6 in order and stops at the first stage that does not
verify. Exit codes mirror the extraction half, so a wrapper can treat both the
same:

    0  every step done and verified
    2  a step did not land - stop for manual review
    3  the UI was not in a state we could act on

Re-running after a halt is safe: every writing step checks what is already
there first, so the stages that already succeeded report "untouched" rather
than doing their work twice.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config, flow
from .ui import UIError

STAGES = ("1.3-1.7", "2.1-2.13", "3.1-3.7", "3.13-3.17", "4.1-4.7", "5.1-5.6")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="automation",
        description="Book an extracted order into Fakturama (spec 1.3-5.7).",
    )
    p.add_argument(
        "input",
        nargs="?",
        default=str(config.DEFAULT_INPUT),
        help=f"the extraction output (default: {config.DEFAULT_INPUT})",
    )
    p.add_argument(
        "--allow-existing",
        action="store_true",
        help=(
            "proceed even if a New Order editor is already open with unsaved "
            "changes. Off by default: clicking Order reuses that editor, so the "
            "run would write into a half-filled form."
        ),
    )
    p.add_argument(
        "--stop-after",
        choices=STAGES,
        help="run up to this stage and no further. For working on one stage "
             "without booking a document.",
    )
    p.add_argument(
        "--no-invoice",
        action="store_true",
        help="stop after the Order is saved; do not create the follow-up "
             "Invoice (spec 4.6).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be booked, touch nothing.",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=(
            "log every control resolution and which fallback layer found it. "
            "A control resolving by layer 3 (tree-relative) means its Name or "
            "tooltip has drifted from the catalog."
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="  . %(name)s: %(message)s",
    )
    path = Path(args.input)

    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return config.EXIT_UI_FAILED

    if "order" not in doc or "reconciliation" not in doc:
        print(f"{path} is not an extraction result - expected 'order' and "
              "'reconciliation' keys", file=sys.stderr)
        return config.EXIT_UI_FAILED

    order = doc["order"]
    status = doc["reconciliation"].get("status")
    if status != "passed":
        # The extraction half writes no file on failure, so this should be
        # unreachable - but never type unreconciled numbers into an accounting
        # system on the strength of "should".
        print(f"refusing to run: reconciliation status is {status!r}", file=sys.stderr)
        return config.EXIT_VERIFICATION_FAILED

    totals = doc["reconciliation"]["computed"]
    print(f"source: {path}")
    print(f"  order date         : {order.get('order_date')}")
    print(f"  external reference : {order.get('external_reference')!r}")
    print(f"  debtor             : {(order.get('debtor') or {}).get('company')!r}")
    print(f"  items              : {len(order.get('items') or [])}")
    print(f"  totals             : net {totals['net_total']}, VAT {totals['vat_total']}, "
          f"gross {totals['gross_total']}")

    if args.dry_run:
        print("\n--dry-run: nothing was typed.")
        return config.EXIT_OK

    print("\nbooking into Fakturama ...\n")
    try:
        stages = flow.run(
            doc,
            allow_existing=args.allow_existing,
            follow_up=not args.no_invoice,
            stop_after=args.stop_after,
        )
    except UIError as exc:
        print(f"\nUI FAILED: {exc}", file=sys.stderr)
        return config.EXIT_UI_FAILED

    print(flow.report(stages))

    failed = [s for s in stages if not s.ok]
    if failed:
        stage = failed[0]
        print(f"\nstage {stage.ref} ({stage.what}) did not verify:", file=sys.stderr)
        for step in stage.result.failures:
            print(f"  {step.ref} {step.what}: {step.detail}", file=sys.stderr)
        print("\nStopping for manual review. Nothing after this stage was run; "
              "re-running is safe once the cause is fixed.", file=sys.stderr)
        return config.EXIT_VERIFICATION_FAILED

    unverified = [s for stage in stages for s in stage.result.steps if not s.verified]
    done = [s for s in stages if not s.skipped]
    print(f"\nbooked: {len(done)} stage(s), "
          f"{sum(len(s.result.steps) for s in done)} steps")
    if unverified:
        print(f"  note: {len(unverified)} step(s) the UI offers no way to confirm:")
        for step in unverified:
            print(f"    {step.ref} {step.what}: {step.detail}")
    return config.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
