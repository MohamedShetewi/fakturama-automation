"""Orchestration: image in, validated JSON out, or a named reason why not.

    python -m extraction.cli samples/order.png -o out/order.json

Exit codes are the interface for anything wrapping this:
    0  extracted and reconciled
    2  reconciliation failed - stop for manual review
    3  extraction failed - no usable read of the image
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from . import config, output
from .derive import derive
from .extract import ExtractionError, ExtractionResult, extract
from .reconcile import reconcile
from .schema import Order


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="extraction",
        description="Extract an order image into validated JSON (spec 1.1-1.2).",
    )
    parser.add_argument("image", help="path to the order image")
    parser.add_argument(
        "-o", "--output", help="where to write the JSON (default: out/<stem>.json)"
    )
    parser.add_argument(
        "--model",
        help=f"override the model (default: $EXTRACT_MODEL or {config.DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--from-raw",
        metavar="RAW_JSON",
        help=(
            "skip the API entirely and reconcile a previously saved raw response. "
            "Use to re-check a fixture, or to confirm the gate catches a value you "
            "have deliberately corrupted."
        ),
    )
    return parser.parse_args(argv)


def _load_raw(path: str) -> ExtractionResult:
    """Rebuild an ExtractionResult from a saved raw response, no API call.

    utf-8-sig, not utf-8: these files get hand-edited, and Notepad and
    PowerShell both write UTF-8 with a BOM that json.loads chokes on. The
    codec strips a BOM if present and is identical to utf-8 otherwise.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    order = Order.model_validate(json.loads(text, parse_float=Decimal))
    return ExtractionResult(
        order=order,
        raw_json=text,
        model="(from-raw)",
        attempts=0,
        input_tokens=None,
        output_tokens=None,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    image = Path(args.image)
    out_path = Path(args.output) if args.output else config.OUT_DIR / f"{image.stem}.json"
    raw_path = config.OUT_DIR / f"{image.stem}.raw.json"

    # --- stage 1: extract ----------------------------------------------------
    try:
        if args.from_raw:
            raw_path = Path(args.from_raw)
            result = _load_raw(args.from_raw)
            print(f"reconciling saved response: {args.from_raw}")
        else:
            print(f"extracting {image} with {args.model or config.model()} ...")
            result = extract(image, model=args.model)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(result.raw_json, encoding="utf-8")
            usage = ""
            if result.input_tokens is not None:
                usage = f", {result.input_tokens} in / {result.output_tokens} out tokens"
            print(f"  read ok (attempt {result.attempts}{usage}) -> {raw_path}")
    except ExtractionError as exc:
        print(f"\nEXTRACTION FAILED: {exc}", file=sys.stderr)
        return config.EXIT_EXTRACTION_FAILED
    except (ValidationError, json.JSONDecodeError, OSError) as exc:
        print(f"\nEXTRACTION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return config.EXIT_EXTRACTION_FAILED

    order = result.order

    # --- stage 2: the gate ---------------------------------------------------
    report = reconcile(order)
    if not report.ok:
        print(
            f"\nRECONCILIATION FAILED - {len(report.failures)} problem(s). "
            f"Stopping for manual review; no output written.\n",
            file=sys.stderr,
        )
        for failure in report.failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            f"\nThe raw response is at {raw_path} if you want to inspect what was read.",
            file=sys.stderr,
        )
        return config.EXIT_RECONCILIATION_FAILED

    print(
        f"  reconciled: net {report.computed_net}, VAT {report.computed_vat}, "
        f"gross {report.computed_gross} (tolerance +/-{config.TOLERANCE})"
    )

    # --- stage 3: derive + write --------------------------------------------
    derived = derive(order)
    if derived.unmapped_payment_method:
        # Not fatal here - the data is sound and the JSON is still worth having.
        # But the automation will have to stop at spec 2.10, so say so now.
        print(
            f"  WARNING: payment method {derived.payment_method!r} has no known "
            f"Fakturama payment code; the automation will stop for manual review.",
            file=sys.stderr,
        )

    payload = output.build(
        order,
        report,
        derived,
        source_image=str(image),
        model=result.model,
        attempts=result.attempts,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    written = output.write(payload, out_path)
    print(f"  wrote {written}")
    return config.EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
