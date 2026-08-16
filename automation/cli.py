"""Drive Fakturama from the extracted JSON (spec 1.3-1.7).

    python -m automation.cli out\\invoice.json

Exit codes mirror the extraction half, so a wrapper can treat both the same:
    0  every step done and verified
    2  a step did not land - stop for manual review
    3  the UI was not in a state we could act on
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import config
from .order_form import fill_header
from .ui import UIError


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="automation",
        description="Fill a Fakturama New Order header from extracted JSON (spec 1.3-1.7).",
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
        "--dry-run",
        action="store_true",
        help="report what would be typed, touch nothing.",
    )
    p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help=(
            "log every control resolution and which fallback layer found it. "
            "A control resolving by layer 3 (pixel geometry) means the catalog "
            "is drifting from the UI."
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
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return config.EXIT_UI_FAILED

    order = payload.get("order", payload)
    recon = payload.get("reconciliation", {}).get("status")
    if recon and recon != "passed":
        # The extraction half writes no file on failure, so this should be
        # unreachable - but never type unreconciled numbers into an accounting
        # system on the strength of "should".
        print(f"refusing to run: reconciliation status is {recon!r}", file=sys.stderr)
        return config.EXIT_VERIFICATION_FAILED

    print(f"source: {path}")
    print(f"  order date         : {order.get('order_date')}")
    print(f"  external reference : {order.get('external_reference')!r}")

    if args.dry_run:
        print("\n--dry-run: nothing was typed.")
        return config.EXIT_OK

    print("\nrunning spec 1.3-1.7 against Fakturama ...")
    try:
        result = fill_header(order, allow_existing=args.allow_existing)
    except UIError as exc:
        print(f"\nUI FAILED: {exc}", file=sys.stderr)
        return config.EXIT_UI_FAILED

    print()
    for step in result.steps:
        print(step)

    if not result.ok:
        print(
            f"\n{len(result.failures)} step(s) did not land. Stopping for manual review.",
            file=sys.stderr,
        )
        return config.EXIT_VERIFICATION_FAILED

    unverified = [s for s in result.steps if not s.verified]
    print(f"\nheader complete ({len(result.steps)} steps)")
    if unverified:
        print(f"  note: {len(unverified)} step(s) could not be verified from the UI:")
        for s in unverified:
            print(f"    {s.ref} {s.what}")
    return config.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
