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


class Screen(str, Enum):
    MAIN = "main"                  # the application shell and its toolbars
    ORDER_EDITOR = "order_editor"   # the New Order editor pane


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
)

_BY_KEY = {t.key: t for t in CATALOG}


def target(key: str) -> Target:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"no catalog entry {key!r}; known keys: {sorted(_BY_KEY)}") from None


def keys() -> list[str]:
    return sorted(_BY_KEY)
