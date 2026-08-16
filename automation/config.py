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

# The order's own Items grid, measured from live Ctrl+C output:
#   1.00  CHR-ERG-01  null  Ergonomic Desk Chair  ''  <VAT blob>  USD 250  0.0  USD 250
ITEM_COL = {"quantity": 0, "sku": 1, "name": 3, "vat": 5,
            "unit_price": 6, "discount": 7, "total": 8}

# Where to click to select something in a NatTable. A data cell is enough in
# the read-only chooser and list views - they select whole rows. The order's
# Items grid is editable and therefore cell-selecting: a data-cell click plus
# Ctrl+A yields *one cell*, and only a click on the row header to its left
# turns the selection into rows that Ctrl+A can extend across the table.
GRID_DATA_DX = 80
GRID_ROW_HEADER_DX = 12

# Inside the Items grid, how far in the first data column starts. Used only to
# put the active cell on a known column - every column after it is reached with
# arrow keys, and the cell that opens is checked against the value the row was
# copied with before anything is typed into it. So this is a starting point,
# not an identity claim: if the layout shifts, the check fails and the write is
# refused rather than landing in the wrong column.
GRID_FIRST_COLUMN_DX = 60

# --- product chooser (spec 3.2-3.3) -----------------------------------------

PRODUCT_DIALOG_TITLE = "Select a product"

# Tooltip hovers are intermittent; retry before falling back to a structural
# match, because guarded icons refuse to be clicked without confirmation.
TOOLTIP_ATTEMPTS = 3

VAT_EDITOR_TITLE = 'New TAX Rate'
VAT_CODE_STANDARD = 'S (Standard rate)'

# --- product creation (spec 3.7) ---------------------------------------------

PRODUCT_EDITOR_TITLE = 'New product'

# Which price the editor asks for is a workspace preference, and the label is
# the only place it is stated. Measured here as gross, but the form reads it
# rather than assuming, because entering a net price into a gross field books
# the wrong amount and nothing downstream would notice.
PRICE_LABEL_GROSS = 'Price (gross)'
PRICE_LABEL_NET = 'Price (net)'

# --- error dialogs -----------------------------------------------------------

# Fakturama's error boxes are plain Win32 dialogs parented to the desktop, not
# to the shell, so they have to be looked for there.
DIALOG_CLASS = "#32770"

# Which of those are safe to dismiss unattended, decided by what the dialog
# offers rather than by its title. A box that only *reports* gives you one way
# out; a box that *asks* gives you a choice, and choosing is not the
# automation's call. Titles were tried first and are too weak: 'Error
# importing data from web shop' is a plain OK box, while 'Confirm' might not
# be - the buttons say which is which, the wording does not.
TITLE_BAR_BUTTONS = frozenset({"Close", "Minimize", "Maximize", "Restore", "System"})
EXPANDER_BUTTONS = frozenset({"Details >>", "Details <<"})
ACKNOWLEDGE_BUTTONS = frozenset({"OK"})

# The buttons such a box offers, in the order we would rather press them.
ERROR_DISMISS_BUTTONS = ("OK", "Close")

# How many times to re-check after clearing one. Eclipse queues error boxes and
# reveals the next as the current one closes, so a single pass is not enough -
# but an unbounded loop against a dialog that keeps reappearing is worse.
ERROR_DIALOG_SWEEPS = 8
