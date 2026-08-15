"""Stage 3b - serialize the result. This file is the contract with the UIA half."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import config
from .derive import Derived
from .reconcile import ReconciliationReport
from .schema import Order


def _jsonable(value: Any) -> Any:
    """Decimal -> float, recursively.

    Safe because every monetary value has already been quantized to the cent:
    Python's float repr is the shortest string that round-trips, so a 2 dp
    Decimal prints as that same 2 dp number. The 19.989999999999998 problem
    comes from float *arithmetic*, and there is none here - all the maths ran
    in Decimal upstream.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def build(
    order: Order,
    report: ReconciliationReport,
    derived: Derived,
    *,
    source_image: str,
    model: str,
    attempts: int = 1,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    return _jsonable(
        {
            "meta": {
                "source_image": source_image,
                "model": model,
                "extraction_attempts": attempts,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
            },
            # The faithful read: the document in its own vocabulary.
            "order": order.model_dump(mode="python"),
            # Deterministic values the automation needs, so it never does maths.
            "derived": {
                "is_paid": derived.is_paid,
                "payment_method": derived.payment_method,
                "payment_code": derived.payment_code,
                "items": [
                    {
                        "position": item.position,
                        "sku": item.sku,
                        "vat_name": item.vat_name,
                        "vat_percent": item.vat_percent,
                        "product_gross_price": item.product_gross_price,
                    }
                    for item in derived.items
                ],
            },
            "reconciliation": {
                "status": "passed" if report.ok else "failed",
                "tolerance": config.TOLERANCE,
                "rounded_lines_before_sum": config.ROUND_LINES_BEFORE_SUM,
                "computed": {
                    "net_total": report.computed_net,
                    "vat_total": report.computed_vat,
                    "gross_total": report.computed_gross,
                },
                "line_nets": list(report.line_nets),
            },
        }
    )


def write(payload: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out
