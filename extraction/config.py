"""Tunables for the extraction pipeline.

Everything here is a knob we expect to turn while calibrating against real
scans. Nothing here reads a file or calls an API.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

# --- model -------------------------------------------------------------------

# gpt-5.6-luna is the cost-optimized small model: $0.20/$1.20 per MTok, vision
# capable. Override for a stronger read on poor scans:
#     $env:EXTRACT_MODEL = "gpt-5.6-terra"
DEFAULT_MODEL = "gpt-5.6-luna"


def model() -> str:
    return os.environ.get("EXTRACT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


# Invoice line items are small, dense text. The default "auto" detail may
# downsample enough to turn an 8 into a 3.
IMAGE_DETAIL = "high"

# One retry, so a single malformed response doesn't fail the run but a
# systematically bad prompt doesn't silently burn budget either.
MAX_EXTRACTION_ATTEMPTS = 1

# --- money -------------------------------------------------------------------

CENTS = Decimal("0.01")

# Reconciliation tolerance, per the plan. Absolute, not relative: printed
# invoices are rounded to the cent, so the error we're absorbing is rounding,
# which does not scale with magnitude.
TOLERANCE = Decimal("0.01")

# Printed invoices normally round each line to the cent and then sum the
# rounded values. If a real sample turns out to sum unrounded line values,
# flip this - it is the single knob for that whole class of mismatch.
ROUND_LINES_BEFORE_SUM = True

# --- paths -------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "samples"
OUT_DIR = REPO_ROOT / "out"

# --- exit codes --------------------------------------------------------------

EXIT_OK = 0
EXIT_RECONCILIATION_FAILED = 2
EXIT_EXTRACTION_FAILED = 3
