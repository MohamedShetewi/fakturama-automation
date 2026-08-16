"""Tunables for the UI automation half (spec 1.3 onwards).

Nothing here touches the UI. The values that hurt when wrong - timeouts and
the date format the app expects - are named here rather than buried inline.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "out" / "invoice.json"

# --- window ------------------------------------------------------------------

# Fakturama is Eclipse RCP/SWT; the shell title carries the workspace path.
WINDOW_RE = r".*Fakturama.*"

# The editor's content Pane is named after the editor. The TabControl is not a
# safe anchor: its Name is the *selected* tab's, and it gains a '*' the moment
# the editor is dirty. The Pane keeps the clean title either way.
ORDER_EDITOR_RE = r"^\*?New Order$"

NEW_ORDER_BUTTON = "Create: New Order"

# The contact editor is titled 'New Debtor' when opened from the New panel.
DEBTOR_EDITOR_RE = r"^\*?New (Debtor|Contact)$"
ADDRESS_DIALOG_TITLE = "Select the address"

# Spec 2.6: Fakturama's own 'no salutation' entry.
SALUTATION_NONE = "---"

# Spec 2.9: the Debtor's own discount, independent of any order line discount.
DEBTOR_DISCOUNT = "0%"

# --- timing ------------------------------------------------------------------

# SWT redraws are not instant and UIA reports the tree mid-update. These are
# deliberately generous: a false "field not found" costs a whole run, and the
# automation is not in a hurry.
FIND_TIMEOUT = 8.0
EDITOR_TIMEOUT = 15.0
SETTLE = 0.4          # after a click, before reading
COMBO_OPEN = 1.0      # dropdown animation

# Tooltips are provoked by hovering, not read as a property. TOOLTIP_PARK is
# how long the cursor waits away from the control so a lingering tip from the
# previous probe is dismissed before the next one is read.
TOOLTIP_TIMEOUT = 4.0
TOOLTIP_PARK = 0.5

# --- date --------------------------------------------------------------------

# The Date widget renders the workspace locale, observed as 'Aug 15, 2026'.
# We write with the first format and accept any of them when reading back -
# verification compares parsed *dates*, never strings, so a locale that pads
# the day differently is not a failure.
DATE_WRITE_FORMAT = "%b %d, %Y"
DATE_READ_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y")

# --- combo values (exact item labels, read from the live dropdowns) ----------

PRICE_MODE_NET = "Net"
PRICE_MODE_GROSS = "Gross"
VAT_WITH = "With VAT"

# Selecting Net/Gross renames the totals field. That rename is the only
# readable proof the combo landed - the combo itself exposes no value.
TOTALS_LABEL = {PRICE_MODE_NET: "Total Net", PRICE_MODE_GROSS: "Total Gross"}

# --- exit codes (same contract as the extraction half) -----------------------

EXIT_OK = 0
EXIT_VERIFICATION_FAILED = 2
EXIT_UI_FAILED = 3


# --- terms of payment (spec 2.10) -------------------------------------------

PAYMENT_EDITOR_TITLE = "New Term of Payment"
PAYMENT_ZERO_DISCOUNT = "0%"
PAYMENT_ZERO_DAYS = "0"

# Spec 2.12: the first data row inside the address grid, measured from the
# grid pane's own top edge. Not an identity claim - the selection is proved
# afterwards by reading the address the Order shows.
GRID_FIRST_ROW_DY = 25
GRID_ROW_HEIGHT = 20

# Column indexes in the copied grid rows, measured from live Ctrl+C output.
#   address: CUST000001 Marta Klein "Northstar Office GmbH" 10117 Berlin BILLING
ADDR_COL = {"number": 0, "first_name": 1, "last_name": 2, "company": 3, "zip": 4, "city": 5}
#   product: 12 juice null null 12.0 <VAT blob>
PRODUCT_COL = {"sku": 0, "name": 1, "description": 2, "stock": 3, "price": 4}

# --- product chooser (spec 3.2-3.3) -----------------------------------------

PRODUCT_DIALOG_TITLE = "Select a product"

# Tooltip hovers are intermittent; retry before falling back to a structural
# match, because guarded icons refuse to be clicked without confirmation.
TOOLTIP_ATTEMPTS = 3

VAT_EDITOR_TITLE = 'New TAX Rate'
VAT_CODE_STANDARD = 'S (Standard rate)'
