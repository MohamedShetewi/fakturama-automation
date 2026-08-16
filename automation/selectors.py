"""The selector catalog: one declarative row per control the flow touches.

Nothing in the flow may name a control inline. A step asks for a key, the
resolver reads the row, and the fallback chain in resolver.py does the rest.
That indirection is what makes a Fakturama layout change a one-line edit here
instead of a hunt through the steps.

Recorded from a live UIA walk of Fakturama 2.x. Three measured facts shape
every row:

  * **AutomationId is never used.** It is derived from the SWT widget handle
    and changes on every editor instance - measured three times, e.g. the
    Cust.Ref. field was 67590, then 133234, then 396046. There is deliberately
    no `automation_id` column: it cannot be a locator.

  * **Tooltips are the best identifier this UI has.** SWT does not expose them
    as HelpText (empty everywhere), but it renders them as a ToolTipControl on
    hover, and every meaningful control has one - including the icons that
    carry no Name at all. A tooltip says what a control is *for*, which is
    exactly what a locator should key on.

  * **No pixel geometry.** An earlier draft located unlabeled icons by an
    offset from a nearby label. Those offsets went stale within a single
    session - the address icons moved from x=550 to x=700 when the layout
    reflowed - which is precisely the failure mode that makes coordinates
    unacceptable as a locator. There is no offset column any more.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class Layer(IntEnum):
    """Resolution strategies, tried in this order.

    1 and 2 are both semantic - they key on what a control *is*. 2 comes
    second only because it costs a real mouse hover and up to a few seconds,
    while a Name match is free. 3 is structural and is the last resort.
    """

    UIA_PROPERTY = 1     # ControlType + Name
    TOOLTIP = 2          # ControlType + hover tooltip text
    TREE_RELATIVE = 3    # nearest control of that type to a labeled neighbor


class Input(str, Enum):
    """How a value gets into the control. Dispatched by actions.set_value."""

    NONE = "none"                       # click target or read-only probe
    TEXT = "text"                       # SWT Text: select-all, type, Tab
    SEGMENTED_DATE = "segmented_date"    # SWT CDateTime: per-segment digits
    COMBO = "combo"                     # dropdown: click the ListItem by name
    # A currency field renders what you typed: '297.50' comes back as
    # '$297.50'. Comparing the strings would fail a write that was perfectly
    # correct, so the read-back is compared as a number.
    MONEY = "money"


class Screen(str, Enum):
    MAIN = "main"                    # the application shell and its toolbars
    ORDER_EDITOR = "order_editor"     # the New Order editor pane
    DEBTOR_EDITOR = "debtor_editor"   # the New Debtor (contact) editor pane
    ADDRESS_DIALOG = "address_dialog"  # the modal 'Select the address' chooser
    PAYMENT_EDITOR = "payment_editor"  # the 'New Term of Payment' editor
    VAT_EDITOR = "vat_editor"          # the 'New TAX Rate' editor
    PRODUCT_EDITOR = "product_editor"  # the 'New product' editor
    INVOICE_EDITOR = "invoice_editor"  # the 'New Invoice' editor, opened from an Order


@dataclass(frozen=True)
class Target:
    """One addressable control."""

    key: str
    screen: Screen
    control_type: str

    # --- layer 1
    name: str | None = None

    # --- layer 2: the tooltip text, as measured by hovering the control
    tooltip: str | None = None

    # --- layer 3: nearest `control_type` right of / below this label
    anchor: str | None = None
    anchor_side: str = "right"
    occurrence: int = 0          # when several qualify, take the nth (0-based)

    input: Input = Input.NONE
    read_only: bool = False
    # A multi-line SWT Text swallows Tab as a literal character instead of
    # moving focus, so such fields must not be committed with Tab.
    multiline: bool = False
    # A live filter box is not a form field: it has nothing to commit, and
    # tabbing out of one that matched nothing clears it.
    commit_with_tab: bool = True
    # Deliver the value as one paste rather than a stream of keystrokes.
    # Reserved for filter boxes that re-query on every character: the product
    # chooser rebuilds its table per keystroke and, measured repeatedly,
    # disposes itself on the second character - 'C' lands, 'H' closes the
    # dialog. A single Ctrl+V runs that filter once and survives. Never set
    # this on a field that feeds a calculation: those need real keystrokes so
    # SWT's modify listeners fire the way a human's typing makes them fire.
    paste: bool = False
    # Several controls share this Name *and* they are the same command, so any
    # of them will do. Declared per row, never assumed: the default refusal to
    # choose between same-named controls is what stops a value landing in the
    # wrong field. Only ever correct for buttons - two Edits with one Name are
    # two different fields, and picking either is a coin toss.
    any_of_several: bool = False
    # Press this by focusing it and sending Space, rather than clicking it.
    # For a modal dialog's OK/Cancel that is the only thing that works - a
    # coordinate click on the product chooser's Cancel was repeatedly ignored.
    # For everything else it is the wrong choice and looks like success: the
    # main toolbar's Save and the order's follow-up Invoice both accepted
    # focus, swallowed the Space, and did nothing at all.
    press_with_keyboard: bool = False
    note: str = ""

    @property
    def layers(self) -> tuple[Layer, ...]:
        """Which strategies this row actually supports, in order."""
        out = []
        if self.name:
            out.append(Layer.UIA_PROPERTY)
        if self.tooltip:
            out.append(Layer.TOOLTIP)
        if self.anchor:
            out.append(Layer.TREE_RELATIVE)
        return tuple(out)


# --- the catalog -------------------------------------------------------------
#
# Every `tooltip` below is the exact string read back from a live hover, not a
# guess from the UI or the manual.

CATALOG: tuple[Target, ...] = (
    # --- application shell ---------------------------------------------------
    Target(
        key="toolbar.new_order",
        screen=Screen.MAIN,
        control_type="ButtonControl",
        name="Create: New Order",
        tooltip="Create: New Order",
        note="Spec 1.3. Icon-only; SWT also publishes the tooltip as the Name.",
    ),
    Target(
        key="toolbar.save",
        screen=Screen.MAIN,
        control_type="ButtonControl",
        name="Save the current contents",
        tooltip="Save the current contents",
        note="Not clicked by the flow - read for its enabled state in wait_ready.",
    ),
    # --- New Order header ----------------------------------------------------
    Target(
        key="order.number",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        tooltip=(
            "Reference number of this document. Next document number and the "
            "format can be set unter preferences/number range"
        ),
        anchor="No.",
        input=Input.TEXT,
        read_only=True,
        note="Spec 1.4: read only, never written. Unnamed - identified by tooltip.",
    ),
    Target(
        key="order.date",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        tooltip="The document's date",
        anchor="Date",
        input=Input.SEGMENTED_DATE,
        note="Spec 1.5. SWT CDateTime - segmented, not a text field.",
    ),
    Target(
        key="order.price_mode",
        screen=Screen.ORDER_EDITOR,
        control_type="ComboBoxControl",
        tooltip="Specify whether the prices should be rounded to net or gross values",
        anchor="Date",
        input=Input.COMBO,
        note="Spec 1.7. Unnamed. Options: ---/Net/Gross.",
    ),
    Target(
        key="order.cust_ref",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="Cust.Ref.",
        tooltip="Customer's reference. E.g.: Your order No.0001",
        anchor="Cust.Ref.",
        input=Input.TEXT,
        note="Spec 1.6.",
    ),
    Target(
        key="order.vat_mode",
        screen=Screen.ORDER_EDITOR,
        control_type="ComboBoxControl",
        name="VAT",
        tooltip=(
            "If this document is set to a tax rate with 0%, all the items of the "
            "document are calculated with 0% tax."
        ),
        input=Input.COMBO,
        note=(
            "Spec 1.7. 'VAT' also names the read-only totals field, so the "
            "ControlType in this row is what disambiguates layer 1."
        ),
    ),
    Target(
        key="order.consultant",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="Consultant",
        tooltip="Consultant, e.g.: Heinz Mueller",
        input=Input.TEXT,
        note="Not used by 1.3-1.7; catalogued from the same pass.",
    ),
    # --- totals: read-only probes, used to verify the price mode landed ------
    Target(
        key="order.total_net",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="Total Net",
        read_only=True,
        note="Exists only while price mode is Net - the oracle for spec 1.7a.",
    ),
    Target(
        key="order.total_gross",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="Total Gross",
        read_only=True,
        note="Exists only while price mode is Gross.",
    ),
    # The document totals. Independent of anything this automation computes,
    # which is what makes them worth reading: they are Fakturama's own answer
    # to the same sum the extraction reconciled against the image, so agreeing
    # with them is real corroboration rather than a restated assumption.
    Target(
        key="order.vat_amount",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="VAT",
        read_only=True,
        note="The document's VAT total. Distinguished from the 'VAT' combo "
             "(order.vat_mode) by control type, which layer 1 already keys on.",
    ),
    Target(
        key="order.total",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="Total",
        read_only=True,
        note="The document's gross total - net plus VAT plus shipping.",
    ),
    # --- 4.1-4.7: completing and following up the order ----------------------
    Target(
        key="order.address_text",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        anchor="Invoice address",
        anchor_side="below",
        read_only=True,
        note="Spec 4.1. The address block the Debtor selection filled in; read "
             "to confirm it against the extracted one, never written here.",
    ),
    Target(
        key="order.discount",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        name="Discount",
        read_only=True,
        note="Spec 4.2: the *document* discount, distinct from a line's. The "
             "extraction has no order-level discount field, so this is only "
             "ever confirmed to be 0%.",
    ),
    Target(
        key="order.shipping",
        screen=Screen.ORDER_EDITOR,
        control_type="ComboBoxControl",
        name="Shipping",
        read_only=True,
        note="Spec 4.2: confirmed to be 'Free of shipping costs', never set.",
    ),
    Target(
        key="order.shipping_amount",
        screen=Screen.ORDER_EDITOR,
        control_type="EditControl",
        anchor="Shipping",
        anchor_side="right",
        occurrence=1,
        read_only=True,
        note="Spec 4.2. Occurrence 1 because the Shipping combo publishes its "
             "own inner Edit first; the amount sits to its right.",
    ),
    Target(
        key="order.followup_invoice",
        screen=Screen.ORDER_EDITOR,
        control_type="ButtonControl",
        name="Invoice",
        note="Spec 4.6. Inside the 'Create a follow-up document' group, which "
             "is what preserves the Order relationship. Distinct from the "
             "toolbar's 'Create: New Invoice' - different Name, so the two "
             "cannot be confused.",
    ),
    Target(
        key="nav.documents",
        screen=Screen.MAIN,
        control_type="TextControl",
        name="Documents",
        note="Spec 4.5. Data panel entry.",
    ),
    # --- 5.1-5.6: the linked Invoice -----------------------------------------
    # Most of this screen is confirmed, not filled: the follow-up copies the
    # Order's values across, and 5.1's job is to check that it really did.
    Target(
        key="invoice.number",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        anchor="No.",
        read_only=True,
        note="Spec 5.1: left exactly as proposed.",
    ),
    Target(
        key="invoice.date",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        anchor="Date",
        read_only=True,
        note="Spec 5.1: left as proposed - this is the invoice's own date, not "
             "the order's.",
    ),
    Target(
        key="invoice.service_date",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        anchor="Service date",
        read_only=True,
        note="Spec 5.1: left as proposed.",
    ),
    Target(
        key="invoice.order_date",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        anchor="Order Date",
        read_only=True,
        note="Spec 5.1: must equal the extracted Order Date, carried over.",
    ),
    Target(
        key="invoice.cust_ref",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        name="Cust.Ref.",
        read_only=True,
        note="Spec 5.1: copied from the Order.",
    ),
    Target(
        key="invoice.vat_mode",
        screen=Screen.INVOICE_EDITOR,
        control_type="ComboBoxControl",
        name="VAT",
        read_only=True,
        note="Spec 5.1: copied from the Order; must still read 'With VAT'.",
    ),
    Target(
        key="invoice.address_text",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        anchor="Invoice address",
        anchor_side="below",
        read_only=True,
        note="Spec 5.1. Anchored on the tab's own label - the field has neither "
             "a Name nor a tooltip.",
    ),
    Target(
        key="invoice.total_net",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        name="Total Net",
        read_only=True,
    ),
    Target(
        key="invoice.vat_amount",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        name="VAT",
        read_only=True,
        note="Distinguished from the 'VAT' combo by control type.",
    ),
    Target(
        key="invoice.total",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        name="Total",
        read_only=True,
    ),
    Target(
        key="invoice.paid",
        screen=Screen.INVOICE_EDITOR,
        control_type="CheckBoxControl",
        name="paid",
        note="Spec 5.3. Ticking it swaps the row beside it: 'Due Days'/'Pay "
             "Until' are replaced by 'at <date>' and 'Value'.",
    ),
    Target(
        key="invoice.payment_method",
        screen=Screen.INVOICE_EDITOR,
        control_type="ComboBoxControl",
        anchor="paid",
        anchor_side="right",
        input=Input.COMBO,
        note="Spec 5.2. Unnamed; the 'paid' checkbox beside it is the only "
             "labelled neighbour, which is why a checkbox counts as an anchor.",
    ),
    Target(
        key="invoice.payment_date",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        anchor="at",
        anchor_side="right",
        input=Input.SEGMENTED_DATE,
        note="Spec 5.3. Exists only once 'paid' is ticked. Same segmented "
             "CDateTime as the order's Date - not a text field.",
    ),
    Target(
        key="invoice.paid_value",
        screen=Screen.INVOICE_EDITOR,
        control_type="EditControl",
        name="Value",
        input=Input.MONEY,
        note="Spec 5.3: the amount paid, which must be the full Invoice Total. "
             "Fakturama prefills it with exactly that.",
    ),
    # --- unlabeled icons: no Name at all, resolved purely by tooltip ---------
    Target(
        key="order.address_pick_contact",
        screen=Screen.ORDER_EDITOR,
        control_type="ImageControl",
        tooltip="Pick an address from the list of all contacts",
        anchor="Addresses",
        anchor_side="below",
        occurrence=0,
        note=(
            "Spec 2.1, the UPPER icon: select an existing contact. Catalogued "
            "for 2.x; not yet exercised by a flow step."
        ),
    ),
    Target(
        key="order.address_new_contact",
        screen=Screen.ORDER_EDITOR,
        control_type="ImageControl",
        tooltip="Open the contact editor to enter a new address",
        anchor="Addresses",
        anchor_side="below",
        occurrence=1,
        note=(
            "Spec 2.1, the LOWER green + icon: starts a NEW debtor. The spec "
            "warns against hitting this one by mistake - the tooltip makes the "
            "two icons unmistakable, which position alone never did."
        ),
    ),
    Target(
        key="order.item_pick_product",
        screen=Screen.ORDER_EDITOR,
        control_type="ImageControl",
        tooltip="Pick an item from the list of all products",
        anchor="Items",
        anchor_side="below",
        occurrence=0,
        note="Spec 3.x. Catalogued from the tooltip pass; not yet used.",
    ),
    Target(
        key="order.item_add_blank",
        screen=Screen.ORDER_EDITOR,
        control_type="ImageControl",
        tooltip="Add a new item with default name and quantity '1'",
        anchor="Items",
        anchor_side="below",
        occurrence=1,
        note="Spec 3.x. Adds an empty row rather than picking a product.",
    ),
    Target(
        key="order.item_delete",
        screen=Screen.ORDER_EDITOR,
        control_type="ImageControl",
        tooltip="Delete the selected item from the list of items",
        anchor="Items",
        anchor_side="below",
        occurrence=2,
        note="Spec 3.x. Destructive - kept distinct from the add icons by tooltip.",
    ),
    # --- 'Select the address' chooser (spec 2.2-2.3) ------------------------
    #
    # The results grid itself is deliberately absent from this catalog: it is a
    # canvas-drawn NatTable that publishes no rows to UIA and copies nothing to
    # the clipboard. Its contents are pixels. Selection is therefore verified
    # by what the *Order* shows afterwards, never by reading the grid.
    Target(
        key="addr_dialog.search",
        screen=Screen.ADDRESS_DIALOG,
        control_type="EditControl",
        anchor="Search:",
        input=Input.TEXT,
        commit_with_tab=False,
        note="Spec 2.2. The only Edit in the dialog.",
    ),
    Target(
        key="addr_dialog.ok",
        press_with_keyboard=True,
        screen=Screen.ADDRESS_DIALOG,
        control_type="ButtonControl",
        name="OK",
        note="Spec 2.3.",
    ),
    Target(
        key="addr_dialog.cancel",
        press_with_keyboard=True,
        screen=Screen.ADDRESS_DIALOG,
        control_type="ButtonControl",
        name="Cancel",
        note="Spec 2.3: the no-exact-row exit into the creation branch.",
    ),
    # --- New Debtor editor (spec 2.5-2.7) -----------------------------------
    Target(
        key="nav.new_contact",
        screen=Screen.MAIN,
        control_type="TextControl",
        name="New Contact",
        note="Spec 2.5. A link in the left New panel, not a toolbar button.",
    ),
    Target(
        key="debtor.customer_id",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Customer ID",
        input=Input.TEXT,
        read_only=True,
        note="Spec 2.6: Fakturama proposes it; read to report, never written.",
    ),
    Target(
        key="debtor.company",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Company",
        input=Input.TEXT,
        multiline=True,
        note=(
            "Spec 2.6. A two-line Text: committing with Tab appends a literal "
            "tab to the value ('Northstar Office GmbH\\t')."
        ),
    ),
    Target(
        key="debtor.salutation",
        screen=Screen.DEBTOR_EDITOR,
        control_type="ComboBoxControl",
        anchor="Salutation",
        input=Input.COMBO,
        note="Spec 2.6: left at '---' when the document supplies none.",
    ),
    Target(
        key="debtor.first_name",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        anchor="First Name Last Name",
        occurrence=0,
        input=Input.TEXT,
        note="Spec 2.6. One label serves both name fields; occurrence splits them.",
    ),
    Target(
        key="debtor.last_name",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        anchor="First Name Last Name",
        occurrence=1,
        input=Input.TEXT,
        note="Spec 2.6.",
    ),
    Target(
        key="debtor.street",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Street",
        input=Input.TEXT,
        note="Spec 2.7.",
    ),
    Target(
        key="debtor.zip",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        anchor="ZIP - City",
        occurrence=0,
        input=Input.TEXT,
        note="Spec 2.7. 'ZIP - City' is one label for two fields.",
    ),
    Target(
        key="debtor.city",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        anchor="ZIP - City",
        occurrence=1,
        input=Input.TEXT,
        note="Spec 2.7.",
    ),
    Target(
        key="debtor.country",
        screen=Screen.DEBTOR_EDITOR,
        control_type="ComboBoxControl",
        name="Country",
        input=Input.COMBO,
        note="Spec 2.7. Defaults to 'United States'; must be set explicitly.",
    ),
    Target(
        key="debtor.email",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="E-Mail",
        input=Input.TEXT,
        note="Spec 2.7.",
    ),
    Target(
        key="debtor.telephone",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Telephone",
        input=Input.TEXT,
        note="Spec 2.7.",
    ),
    Target(
        key="debtor.additional_name",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="additional name",
        input=Input.TEXT,
        note="Spec 2.7: filled only when the source supplies it.",
    ),
    Target(
        key="debtor.address_specification",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Address specification",
        input=Input.TEXT,
        note="Spec 2.7: filled only when the source supplies it.",
    ),
    # --- 2.8: address roles -------------------------------------------------
    Target(
        key="debtor.tab_addresses",
        screen=Screen.DEBTOR_EDITOR,
        control_type="TabItemControl",
        name="Addresses",
        note="Spec 2.7-2.8.",
    ),
    Target(
        key="debtor.tab_miscellaneous",
        screen=Screen.DEBTOR_EDITOR,
        control_type="TabItemControl",
        name="Miscellaneous",
        note="Spec 2.9-2.10: Alias, Discount, Net or Gross and Payment all live here.",
    ),
    Target(
        key="debtor.address_type_open",
        screen=Screen.DEBTOR_EDITOR,
        control_type="ButtonControl",
        anchor="address type",
        note=(
            "Spec 2.8. The small '>' beside 'address type'; opens the popup "
            "holding the two role checkboxes. Unnamed and untooltipped."
        ),
    ),
    Target(
        key="debtor.role_invoice",
        screen=Screen.MAIN,
        control_type="CheckBoxControl",
        name="Invoice address",
        note="Spec 2.8. In a popup outside the editor pane, hence MAIN scope.",
    ),
    Target(
        key="debtor.role_delivery",
        screen=Screen.MAIN,
        control_type="CheckBoxControl",
        name="Delivery address",
        note="Spec 2.8: only when billing and delivery are identical.",
    ),
    # --- 2.9-2.10: Miscellaneous -------------------------------------------
    Target(
        key="debtor.alias",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Alias name",
        input=Input.TEXT,
        note="Spec 2.9.",
    ),
    Target(
        key="debtor.discount",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="Discount",
        input=Input.TEXT,
        note="Spec 2.9: 0%.",
    ),
    Target(
        key="debtor.net_or_gross",
        screen=Screen.DEBTOR_EDITOR,
        control_type="ComboBoxControl",
        name="Net or Gross",
        input=Input.COMBO,
        note="Spec 2.9. Options: ---/Net/Gross; defaults to '---'.",
    ),
    Target(
        key="debtor.payment",
        screen=Screen.DEBTOR_EDITOR,
        control_type="ComboBoxControl",
        name="Payment",
        input=Input.COMBO,
        note=(
            "Spec 2.10. This combo's option list is the readable oracle for "
            "whether a Payment Method exists - the terms-of-payment list is "
            "another opaque NatTable."
        ),
    ),
    # --- 2.10.1-2.10.5: terms of payment ------------------------------------
    Target(
        key="nav.terms_of_payment",
        screen=Screen.MAIN,
        control_type="TextControl",
        name="terms of payment",
        note="Spec 2.10.1. Data panel entry on the left.",
    ),
    Target(
        key="payment.list_new",
        screen=Screen.MAIN,
        control_type="ButtonControl",
        name="Create a new term of payment",
        note=(
            "Spec 2.10.2, the green '+'. Its neighbour is 'Delete the marked "
            "entry' - the Name is what keeps those apart."
        ),
    ),
    Target(
        key="payment.name",
        screen=Screen.PAYMENT_EDITOR,
        control_type="EditControl",
        name="Name",
        input=Input.TEXT,
        note="Spec 2.10.3.",
    ),
    Target(
        key="payment.description",
        screen=Screen.PAYMENT_EDITOR,
        control_type="EditControl",
        name="Description",
        input=Input.TEXT,
        note="Spec 2.10.3.",
    ),
    Target(
        key="payment.account",
        screen=Screen.PAYMENT_EDITOR,
        control_type="ComboBoxControl",
        name="Account",
        input=Input.COMBO,
        note="Spec 2.10.3: left blank. Catalogued so it is never written by accident.",
    ),
    Target(
        key="payment.code",
        screen=Screen.PAYMENT_EDITOR,
        control_type="ComboBoxControl",
        name="!editorPaymentPaymentcode!",
        input=Input.COMBO,
        note=(
            "Spec 2.10.4. The Name is an untranslated i18n key - ugly, but it is "
            "what the widget publishes, so it is what the catalog records. "
            "Options carry a trailing space ('Credit transfer ')."
        ),
    ),
    Target(
        key="payment.cash_discount",
        screen=Screen.PAYMENT_EDITOR,
        control_type="EditControl",
        name="Cash discount",
        input=Input.TEXT,
        note="Spec 2.10.5: 0.",
    ),
    Target(
        key="payment.discount_days",
        screen=Screen.PAYMENT_EDITOR,
        control_type="EditControl",
        name="Discount Days",
        input=Input.TEXT,
        note="Spec 2.10.5: 0.",
    ),
    Target(
        key="payment.net_days",
        screen=Screen.PAYMENT_EDITOR,
        control_type="EditControl",
        name="Net Days",
        input=Input.TEXT,
        note="Spec 2.10.5: 0.",
    ),
    Target(
        key="payment.set_as_standard",
        screen=Screen.PAYMENT_EDITOR,
        control_type="ButtonControl",
        name="Set as standard",
        note=(
            "Spec 2.10.5 forbids clicking this. Catalogued precisely so the "
            "prohibition is checkable, never so it can be invoked."
        ),
    ),
    Target(
        key="debtor.district",
        screen=Screen.DEBTOR_EDITOR,
        control_type="EditControl",
        name="district",
        input=Input.TEXT,
        note="Spec 2.7: filled only when the source supplies it.",
    ),
    Target(
        key="product_dialog.search",
        screen=Screen.ADDRESS_DIALOG,
        control_type="EditControl",
        anchor="Search:",
        input=Input.TEXT,
        commit_with_tab=False,
        paste=True,
        note="Spec 3.3. Same chooser shell as the address selector. Pasted, "
             "not typed: per-keystroke filtering closes the dialog mid-SKU.",
    ),
    Target(
        key="product_dialog.ok",
        press_with_keyboard=True,
        screen=Screen.ADDRESS_DIALOG,
        control_type="ButtonControl",
        name="OK",
        note="Spec 3.3.",
    ),
    Target(
        key="product_dialog.cancel",
        press_with_keyboard=True,
        screen=Screen.ADDRESS_DIALOG,
        control_type="ButtonControl",
        name="Cancel",
        note="Spec 3.3: the no-exact-SKU exit into the creation branch.",
    ),
    # --- 3.4-3.6: VAT / tax rates -------------------------------------------
    Target(
        key="nav.vats",
        screen=Screen.MAIN,
        control_type="TextControl",
        name="VATs",
        note="Spec 3.4. Data panel entry.",
    ),
    Target(
        key="vat.list_new",
        screen=Screen.MAIN,
        control_type="ButtonControl",
        name="Create a new tax rate",
        note="Spec 3.6, the green '+'. Named, so no positional guessing.",
    ),
    Target(
        key="vat.name",
        screen=Screen.VAT_EDITOR,
        control_type="EditControl",
        name="Name",
        input=Input.TEXT,
        note="Spec 3.6: 'VAT' followed by the percentage.",
    ),
    Target(
        key="vat.description",
        screen=Screen.VAT_EDITOR,
        control_type="EditControl",
        name="Description",
        input=Input.TEXT,
        note="Spec 3.6: same text as Name.",
    ),
    Target(
        key="vat.code",
        screen=Screen.VAT_EDITOR,
        control_type="ComboBoxControl",
        name="VAT code (E-Invoice)",
        input=Input.COMBO,
        note="Spec 3.5-3.6: must be 'S (Standard rate)'. Already the default.",
    ),
    Target(
        key="vat.value",
        screen=Screen.VAT_EDITOR,
        control_type="EditControl",
        name="Value",
        input=Input.TEXT,
        note="Spec 3.6: the percentage.",
    ),
    Target(
        key="vat.set_as_standard",
        screen=Screen.VAT_EDITOR,
        control_type="ButtonControl",
        name="Set as standard",
        note="Spec 3.6 leaves the displayed Standard VAT unchanged; never clicked.",
    ),
    # --- 3.7: creating a product the chooser did not have --------------------
    Target(
        key="product.list_new",
        screen=Screen.MAIN,
        control_type="ButtonControl",
        name="Create a new product",
        any_of_several=True,
        note="Two of these exist - the main toolbar's and the Products view's - "
             "and they are the same command. Preferred over the left panel's "
             "'New product' link, which was measured silently doing nothing "
             "once a session had opened and closed a product editor.",
    ),
    Target(
        key="product.item_number",
        screen=Screen.PRODUCT_EDITOR,
        control_type="EditControl",
        name="Item Number",
        input=Input.TEXT,
        note="The SKU. This is what the chooser's search matches on.",
    ),
    Target(
        key="product.name",
        screen=Screen.PRODUCT_EDITOR,
        control_type="EditControl",
        name="Name",
        input=Input.TEXT,
        note="The line text that ends up on the document.",
    ),
    Target(
        key="product.description",
        screen=Screen.PRODUCT_EDITOR,
        control_type="EditControl",
        name="Description",
        input=Input.TEXT,
        multiline=True,
        note="Free text under the name; left empty unless the source has more "
             "than the line description already written into Name.",
    ),
    Target(
        key="product.vat",
        screen=Screen.PRODUCT_EDITOR,
        control_type="ComboBoxControl",
        name="VAT",
        input=Input.COMBO,
        note="Lists the tax rates by Name, so 3.5 has to resolve the rate first.",
    ),
    # The price field carries no Name and no tooltip, and its label states the
    # mode: 'Price (gross)' or 'Price (net)', a workspace preference. Two rows
    # rather than one, so the form finds out which mode it is in by which key
    # resolves - and converts the extracted net price only when it must.
    Target(
        key="product.price_gross",
        screen=Screen.PRODUCT_EDITOR,
        control_type="EditControl",
        anchor="Price (gross)",
        anchor_side="right",
        input=Input.MONEY,
        note="Present when the workspace prices products gross.",
    ),
    Target(
        key="product.price_net",
        screen=Screen.PRODUCT_EDITOR,
        control_type="EditControl",
        anchor="Price (net)",
        anchor_side="right",
        input=Input.MONEY,
        note="Present when the workspace prices products net.",
    ),
)

_BY_KEY = {t.key: t for t in CATALOG}


def target(key: str) -> Target:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"no catalog entry {key!r}; known keys: {sorted(_BY_KEY)}") from None


def keys() -> list[str]:
    return sorted(_BY_KEY)



