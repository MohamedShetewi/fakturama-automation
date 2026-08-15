# Fakturama Image-to-Cash — extraction stage

Turns an order image into validated JSON. This is **spec steps 1.1–1.2**; the UI
automation (1.3 onwards) consumes the JSON this produces.

## Why it's split here

The JSON file is a hard boundary between the two halves, which buys three things:

- The extractor is testable **without Fakturama running**, and the automation is
  developable **without burning API calls** — it runs against a committed fixture.
- A vision misread is caught by arithmetic before it can reach an accounting system.
- When something goes wrong end to end, the JSON says which half to look at.

## The reconciliation gate

A vision model will occasionally misread a digit. A misread digit that reaches an
accounting system is worse than a failed run — so nothing is written until the
extracted arithmetic agrees with the totals the document itself prints:

| Check | Rule | Spec |
|---|---|---|
| per line | `qty × unit_net × (1 − discount/100) ≈ printed line total` | §3.16 |
| net total | `Σ line nets ≈ printed net total` | §4.3 |
| VAT total | `Σ (line net × vat/100) ≈ printed VAT total` | §4.3 |
| gross | `printed net + printed VAT ≈ printed gross` | §4.3 |

Tolerance ±0.01, absolute — the error being absorbed is cent-rounding, which doesn't
scale with magnitude. Any failure **halts**: no output file, non-zero exit, and the
offending row named. That's the spec's "stop for manual review" posture applied one
stage earlier, to the data itself.

The per-line formula is the same identity the automation later confirms inside
Fakturama (§3.16), so a mismatch caught here is a UI round-trip saved.

Every value is a `Decimal`. Never `float` — binary floating point produces spurious
one-cent failures on exactly the sums being checked.

## Setup

```powershell
git clone <repo> && cd fakturama-automation
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "<key>"
```

Requires Python 3.11+ (uses `X | None` syntax).

## Run

```powershell
# extract an image
.\.venv\Scripts\python.exe -m extraction.cli samples\order.png -o out\order.json

# re-run the gate on a saved response, no API call
.\.venv\Scripts\python.exe -m extraction.cli samples\order.png `
    --from-raw tests\fixtures\sample_raw.json -o out\order.json

# tests (no API key needed)
.\.venv\Scripts\python.exe -m pytest
```

Exit codes — the interface for anything wrapping this:

| Code | Meaning |
|---|---|
| `0` | extracted and reconciled |
| `2` | reconciliation failed — stop for manual review |
| `3` | extraction failed — no usable read of the image |

## Model

Default `gpt-5.6-luna` ($0.20 / $1.20 per MTok), the cheapest vision-capable model in
the catalog. A cheap extractor is defensible *because* of the gate: misreads are caught
arithmetically rather than shipped. Override without touching code:

```powershell
$env:EXTRACT_MODEL = "gpt-5.6-terra"   # stronger read on poor scans
```

> `gpt-4o` and `gpt-5-nano` are both absent from the current model catalog. There is no
> nano/mini tier in the GPT-5 family any more; Luna is its replacement.

## Layout

```
extraction/
  config.py     tunables: model, tolerance, rounding policy
  schema.py     Pydantic models -> the strict JSON Schema + the output contract
  extract.py    the vision call, with schema-validation retry
  reconcile.py  the gate: pure arithmetic, no I/O
  derive.py     deterministic values the automation needs
  output.py     serialization
  cli.py        orchestration and exit codes
tests/          64 tests, no API key required
```

## Design notes

**Faithful read, then deterministic mapping.** The model reads the document in the
document's *own* vocabulary and is explicitly told not to recompute or correct
anything. Everything Fakturama-shaped is computed in Python, in `derive.py`, where it
is testable:

- **Product master gross price** (§3.9) — `unit_net × (1 + vat/100)`, 2dp half-up. The
  line discount is deliberately *not* applied: the discount belongs to this order line,
  the master record outlives it.
- **VAT record name** (§3.5–3.6) — `VAT 19%`, with `19.00` normalized to `19` so it
  matches Fakturama's exact-name lookup.
- **Payment code** (§2.10.4) — exact mapping only. An unrecognized method yields `null`
  so the automation stops for review rather than booking against a plausible guess.
- **Paid status** (§5.3) — only a literal `PAID` counts; anything else leaves the
  invoice unpaid with no invented date or value.

**Locale is inferred, never hardcoded.** The prompt asks the model to determine the
currency and date pattern from the page and to record, in `locale_evidence`, which
signals it used — so a misread locale is debuggable rather than mysterious. Dates are
rejected at validation unless already ISO-8601, which turns an ambiguous `01/02/2026`
into a retry rather than a silently wrong booking date.

**Three naming traps the spec sets, and where they land:**

| Trap | Handling |
|---|---|
| Printed "Customer ID" (`CUST-1007`) | captured as `printed_customer_id`; Fakturama's own Customer ID stays auto-proposed (§2.6) |
| Alias vs company name | separate fields, never collapsed |
| Line net price vs product master price | `unit_net_price` on the line; the derived gross is computed separately, without the discount |

**Absent vs missed.** OpenAI strict mode requires every field to be `required`, so
genuinely-absent data is modelled as *nullable*. "The document doesn't state this"
becomes an explicit `null` rather than an absence indistinguishable from an extraction
miss — which is what §2.6's "leave Salutation as `---` when none is supplied" needs.

## Not done

- **Part 1 design doc** — separate deliverable.
- **UIA automation**, spec 1.3–5.7 — consumes `out/order.json`.
- **No end-to-end run against a real order image yet.** No sample image was supplied;
  everything is verified against `tests/fixtures/sample_raw.json`. The gate and the
  derivations are proven; the *prompt* is not until it meets a real scan.
- No OCR cross-check. The spec allows "OCR and/or an LLM"; reconciliation is the safety
  net instead. Worth adding if real scans prove noisy on dense number columns.
- Single image per run; no batching.

### If the gate fails on a real scan

In order of likelihood: raise the model (`EXTRACT_MODEL=gpt-5.6-terra`); check whether
the document sums *unrounded* line values, in which case flip
`ROUND_LINES_BEFORE_SUM` in `config.py`; then inspect `out/<stem>.raw.json`, which is
written before validation precisely so a bad read can be read back.
