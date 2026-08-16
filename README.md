# Fakturama Image-to-Cash

Turns an order image into a saved order and a paid invoice in Fakturama, with nothing
typed by hand. Two halves, with a JSON file between them:

| Half | Spec | What it does |
|---|---|---|
| `extraction/` | 1.1–1.2 | reads the image, checks the arithmetic, writes `out/invoice.json` |
| `automation/` | 1.3–5.7 | drives the Fakturama window from that JSON |

📄 **[DESIGN.md](DESIGN.md)** — the problem, the approach, and what could be better.
Read that first if you want the *why*; this file is the *how to run it*.

---

## Demo

▶️ **[Watch the full run on Google Drive](https://drive.google.com/file/d/17WaOYc3PKzXirXtbOAp3hgXJaQJ7-l_r/view?usp=drive_link)**
— the order image goes in, Fakturama comes out filled and saved. About 12 minutes,
unedited.

> Hosted on **Google Drive** rather than embedded here: GitHub serves files committed
> to a repo as `application/octet-stream`, so a video in the README downloads instead
> of playing. The same recording is in the repo at `media/final.mp4` if you'd rather
> have it locally.

---

## Requirements

| | Need | Why |
|---|---|---|
| **Python** | 3.11 or newer | the code uses `X \| None` syntax |
| **OS** | Windows 10/11 | the automation half talks to Windows UI Automation. The extraction half runs anywhere |
| **Fakturama** | 2.x, installed and **open** | there is no API; the automation drives the real window |
| **API key** | an OpenAI key | only for reading an image. Tests and `--from-raw` need none |

Python packages — all of `requirements.txt`, installed in one step below:

| Package | For |
|---|---|
| `openai>=1.60` | the vision call |
| `pydantic>=2.7` | the strict schema the model must answer in |
| `uiautomation>=2.0.29` | driving Fakturama's window, via `comtypes` |
| `pytest>=8.0` | the test suite |

> **Not pywinauto.** It pulls in `pywin32`, whose `win32ui` needs the MFC runtime,
> which a stock Windows box does not have.

---

## Setup

Four steps, from a clone to a passing test run.

```powershell
# 1. get the code
git clone <repo>
cd fakturama-automation

# 2. make a virtual environment
python -m venv .venv

# 3. install everything
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. add your API key
copy .env.example .env
notepad .env            # put your key after OPENAI_API_KEY=
```

Check it worked — this needs no key, no network and no Fakturama:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

### About the API key

`.env` lives at the repo root, is gitignored, and `.env.example` is the tracked
template. It is read by `extraction/env.py` — no `python-dotenv` dependency, since
the format we need is `KEY=VALUE`.

The real environment wins over the file, so CI and a one-off
`$env:OPENAI_API_KEY = "<key>"` still override without editing anything. The file is
only read on the path that actually calls the API. A missing key exits `3` and says
so, rather than surfacing as a 401 from inside the SDK.

---

## Run it

**Half 1 — image to JSON.** Writes the validated file the second half consumes:

```powershell
.\.venv\Scripts\python.exe -m extraction.cli samples\invoice.png -o out\invoice.json
```

**Half 2 — JSON into Fakturama.** Fakturama must be open on screen first, and the
mouse and keyboard are in use while it runs:

```powershell
.\.venv\Scripts\python.exe -m automation.cli out\invoice.json
```

Useful flags while working on one stage: `--stop-after 3.13-3.17` runs up to that
stage and no further, `--dry-run` touches nothing, `--no-invoice` stops once the
order is saved, and `-v` reports which fallback layer found each control.

Both halves use the same exit codes:

| Code | Meaning |
|---|---|
| `0` | done and verified |
| `2` | a check failed — stop for manual review |
| `3` | could not read the image / could not drive the UI |

---

## What the extraction half prints

Expected output on a document that reconciles:

```
extracting samples\invoice.png with gpt-5.6-luna ...
  read ok (attempt 1, <in> in / <out> out tokens) -> out\invoice.raw.json
  reconciled: net 570.00, VAT 108.30, gross 678.30 (tolerance +/-0.01)
  wrote out\invoice.json
```

The three totals are the ones `samples\invoice.png` actually prints, and are
verified — the gate has been run against that document via `--from-raw`. The
token counts vary per run.

Two files land in `out\`: `invoice.raw.json` is the model's unmodified response,
written *before* validation so a bad read can still be inspected;
`invoice.json` is the contract with the UIA half.

### Running extraction without an API call

```powershell
# no API call: re-run the gate on a saved response. Use to re-check a fixture,
# or to confirm the gate catches a value you have deliberately corrupted.
.\.venv\Scripts\python.exe -m extraction.cli samples\invoice.png `
    --from-raw tests\fixtures\sample_raw.json -o out\invoice.json

# stronger model for one run, without editing anything
.\.venv\Scripts\python.exe -m extraction.cli samples\invoice.png `
    --model gpt-5.6-terra -o out\invoice.json

# tests. No API key, no network, no image.
.\.venv\Scripts\python.exe -m pytest
```

`--from-raw` takes the *image path too*, but only to label `meta.source_image`;
the file is never opened on that path, so it needs neither a key nor a real image.

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
extraction/       image -> JSON (spec 1.1-1.2)
  config.py       tunables: model, tolerance, rounding policy
  env.py          loads the gitignored .env (API key), environment wins
  schema.py       Pydantic models -> the strict JSON Schema + the output contract
  extract.py      the vision call, with schema-validation retry
  reconcile.py    the gate: pure arithmetic, no I/O
  derive.py       deterministic values the automation needs
  output.py       serialization
  cli.py          orchestration and exit codes

automation/       JSON -> Fakturama (spec 1.3-5.7)
  selectors.py    the field catalog: every control named in one place
  resolver.py     finds a control by name, then tooltip, then tree position
  ui.py           window, dialogs, focus, grid reads via the clipboard
  actions.py      write a value and read it back to prove it
  flow.py         the six stages, in order
  cli.py          orchestration and exit codes
  *_form.py       one file per screen: order, debtor, payment, product, VAT, invoice

tests/            273 tests. No API key, no network, no Fakturama.
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

See **[DESIGN.md §3](DESIGN.md#3-what-could-be-improved)** for the list, and why each
one matters. The short version:

- **No OCR alongside the prompt.** The arithmetic gate cannot check a reference
  number, and one has already come back wrong while adding up perfectly.
- **One image per run.** No batching.
- **Fakturama must be open and in front**, so the machine is unusable while the second
  half runs.
- **Currency is not handled** — the workspace renders `$` on a EUR order.
- No OCR cross-check. The spec allows "OCR and/or an LLM"; reconciliation is the safety
  net instead. Worth adding if real scans prove noisy on dense number columns.
- Single image per run; no batching.

### If the gate fails on a real scan

In order of likelihood: raise the model (`EXTRACT_MODEL=gpt-5.6-terra`); check whether
the document sums *unrounded* line values, in which case flip
`ROUND_LINES_BEFORE_SUM` in `config.py`; then inspect `out/<stem>.raw.json`, which is
written before validation precisely so a bad read can be read back.
