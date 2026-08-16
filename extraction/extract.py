"""Stage 1 - read the order image into a validated ``Order`` (spec 1.1-1.2).

One vision call against the Responses API. The model is asked for a *faithful*
read: the document in its own vocabulary, with locale normalized but nothing
mapped onto Fakturama's field names yet. Mapping and arithmetic happen later,
deterministically, in derive.py.
"""

from __future__ import annotations

import base64
import json
import mimetypes
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import config
from .schema import Order


class ExtractionError(RuntimeError):
    """The model could not produce a usable read of this image."""


EXTRACTION_PROMPT = """\
You extract structured data from a single order document image (a scanned or \
photographed purchase order).

Read the document faithfully. Report what is printed on the page - do not infer, \
correct, or complete values that are not there, and do not map anything onto the \
vocabulary of any accounting system. If the document does not state something, \
return null for it. Never invent a value to fill a field.

LOCALE
Determine the document's conventions from the document itself. Do not assume any \
particular country.
  * Currency: read the symbol or code printed on the page and report the ISO-4217 \
code (EUR, USD, GBP, ...). Where a symbol is ambiguous, use other evidence on the \
page - VAT wording, address country, IBAN prefix, language.
  * Dates: work out the printed pattern, then emit every date as ISO-8601 \
YYYY-MM-DD. A date like 01/02/2026 is ambiguous on its own; resolve it using the \
document's own evidence (other dates on the page that disambiguate, the language, \
the address country, a printed format hint).
  * Numbers: the page may use '.' or ',' as the decimal separator, and may group \
thousands. Emit every number in plain form: '.' as the decimal separator, no \
thousands separators, no currency symbol.
Record in locale_evidence, in one short sentence, which signals on the page you \
used to fix the currency and the date format.

FIELDS
  * order_date, external_reference, currency.
  * Debtor: company, first and last name of the contact person, salutation, alias, \
and any customer identifier printed on the document.
  * Addresses: billing and delivery. If the document shows only one address, put \
it in billing and return null for delivery. Do not copy billing into delivery.
  * Payment: the method exactly as printed (e.g. "Bank Transfer"), the paid status \
exactly as printed (e.g. "PAID"), and the payment date if one is given.
  * Every line item, in the order they appear, numbered from 1: SKU, description, \
quantity, unit net price, VAT percentage, discount percentage, and the line total \
as printed.
  * Totals: net total, VAT total, gross total, as printed.

NUMBERS TO REPORT AS PRINTED
Report unit_net_price, vat_percent, discount_percent, source_total and the three \
totals exactly as they appear. Do not recompute them, do not fix an apparent \
arithmetic error, and do not round. Downstream checks compare your read against \
the document's own printed totals; silently correcting a value defeats that check.

Report percentages as plain numbers: 19% is 19, not 0.19. If a line shows no \
discount, return null rather than 0.

THE DISCOUNT COLUMN
The line items table often carries a discount column, headed Disc., Discount, \
Allowance, Rabatt, %, or similar, and it is easy to skip when most rows leave it \
blank. Read it for every row. A cell that is blank, or shows a dash, is null - \
not 0.

Use it as a cross-check before you answer: if a row's printed line total is less \
than quantity x unit net price, that difference is a discount and it is stated \
somewhere on the row - go back and read it. Report the figure the page prints; \
do not calculate one to make the arithmetic work, and do not adjust any other \
number to compensate.

IDENTIFIERS TO COPY CHARACTER FOR CHARACTER
external_reference, every SKU, the customer identifier and the alias are opaque \
strings, not data to be tidied. Transcribe each one exactly: same characters, \
same case, same hyphens, in the same positions. Do not normalise a date-like \
run of digits inside them, do not insert or remove separators, and do not make \
one identifier look more like another. 'WEB-2026-0714-A17' is not \
'WEB-2026-07-14-A17'; a single moved hyphen makes it a different order.

Nothing downstream can catch this. The arithmetic checks compare numbers, and a \
mistyped reference is still a perfectly valid string - it just points at \
nothing.\
"""


@dataclass(frozen=True)
class ExtractionResult:
    order: Order
    raw_json: str
    model: str
    attempts: int
    input_tokens: int | None
    output_tokens: int | None


def _data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime is None or not mime.startswith("image/"):
        # Be permissive about odd extensions but never mislabel the payload.
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _refusal(resp: Any) -> str | None:
    for item in getattr(resp, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            if getattr(block, "type", None) == "refusal":
                return getattr(block, "refusal", "(no reason given)")
    return None


def _raw_json(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return text
    # output_text is a convenience accessor; fall back to walking the blocks.
    parts = [
        block.text
        for item in (getattr(resp, "output", None) or [])
        for block in (getattr(item, "content", None) or [])
        if getattr(block, "type", None) == "output_text"
    ]
    if not parts:
        raise ExtractionError("model returned no text content")
    return "".join(parts)


def _client():
    from openai import OpenAI  # imported lazily so tests need no SDK/key

    return OpenAI()


def extract(image_path: str | Path, *, client: Any = None, model: str | None = None) -> ExtractionResult:
    """Extract one order image. Raises ExtractionError if it cannot."""
    path = Path(image_path)
    if not path.is_file():
        raise ExtractionError(f"no such image: {path}")

    client = client or _client()
    model = model or config.model()
    image_uri = _data_uri(path)

    correction = ""
    last_error: Exception | None = None

    for attempt in range(1, config.MAX_EXTRACTION_ATTEMPTS + 1):
        try:
            resp = client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": EXTRACTION_PROMPT + correction},
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Extract this order."},
                            {
                                "type": "input_image",
                                "image_url": image_uri,
                                "detail": config.IMAGE_DETAIL,
                            },
                        ],
                    },
                ],
                text_format=Order,
            )
        except Exception as exc:  # network, auth, unknown model, bad schema
            raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc

        refused = _refusal(resp)
        if refused:
            raise ExtractionError(f"model refused: {refused}")
        if getattr(resp, "status", None) == "incomplete":
            detail = getattr(resp, "incomplete_details", None)
            raise ExtractionError(f"response incomplete: {detail}")

        raw = _raw_json(resp)
        try:
            # Parse the raw text ourselves with parse_float=Decimal rather than
            # reading resp.output_parsed. The SDK would hand us binary floats,
            # and 0.1 + 0.2 style drift is exactly what the reconciliation gate
            # downstream is trying to detect in the *document*, not in us.
            order = Order.model_validate(json.loads(raw, parse_float=Decimal))
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < config.MAX_EXTRACTION_ATTEMPTS:
                correction = (
                    "\n\nYour previous response was rejected by schema validation:\n"
                    f"{exc}\n"
                    "Return the same extraction, corrected to satisfy the schema."
                )
                continue
            raise ExtractionError(
                f"response failed schema validation after "
                f"{config.MAX_EXTRACTION_ATTEMPTS} attempts: {exc}"
            ) from exc

        usage = getattr(resp, "usage", None)
        return ExtractionResult(
            order=order,
            raw_json=raw,
            model=model,
            attempts=attempt,
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
        )

    raise ExtractionError(f"extraction failed: {last_error}")  # pragma: no cover
