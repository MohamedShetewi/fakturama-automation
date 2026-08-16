"""Catalog integrity, and the SendKeys escaping set_value depends on.

These are cheap invariants that catch the two ways a declarative catalog rots:
a row nobody can resolve, and a key silently duplicated.
"""

from __future__ import annotations

import pytest

from automation.actions import escape_keys
from automation.selectors import CATALOG, Input, Layer, Screen, keys, target


def test_keys_are_unique():
    seen = [t.key for t in CATALOG]
    assert len(seen) == len(set(seen)), "duplicate catalog key"


def test_every_row_declares_at_least_one_usable_layer():
    # A row with neither a name nor an anchor can never be resolved; the
    # resolver would raise at run time, which is far too late to find out.
    for t in CATALOG:
        assert t.layers, f"{t.key} declares no usable resolution layer"


def test_layers_are_ordered_most_reliable_first():
    for t in CATALOG:
        assert list(t.layers) == sorted(t.layers)


def test_each_layer_is_offered_exactly_when_its_column_is_filled():
    for t in CATALOG:
        assert (Layer.UIA_PROPERTY in t.layers) == bool(t.name)
        assert (Layer.TOOLTIP in t.layers) == bool(t.tooltip)
        assert (Layer.TREE_RELATIVE in t.layers) == bool(t.anchor)


def test_there_is_no_pixel_geometry_layer():
    # Coordinates went stale inside one session (the address icons moved 150px
    # when the layout reflowed). No row may carry an offset, and no such layer
    # may exist to be reintroduced.
    assert not hasattr(Layer, "ANCHOR_GEOMETRY")
    for t in CATALOG:
        assert not hasattr(t, "anchor_offset"), f"{t.key} still carries pixel offsets"


def test_unnamed_swt_controls_are_identified_by_tooltip():
    # The fields and icons Fakturama leaves unnamed are exactly the ones that
    # must carry a tooltip, or they are reachable only by position.
    for key in ("order.number", "order.date", "order.price_mode",
                "order.address_pick_contact", "order.address_new_contact"):
        t = target(key)
        assert t.name is None, f"{key} unexpectedly has a Name"
        assert t.tooltip, f"{key} is unnamed and has no tooltip"


def test_the_two_address_icons_are_distinguished_semantically():
    # Position alone cannot safely tell these apart, and clicking the wrong
    # one starts a new debtor instead of selecting an existing contact.
    pick = target("order.address_pick_contact")
    new = target("order.address_new_contact")
    assert pick.tooltip != new.tooltip
    assert Layer.TOOLTIP in pick.layers and Layer.TOOLTIP in new.layers


def test_read_only_rows_are_marked_so_set_value_refuses_them():
    for key in ("order.number", "order.total_net", "order.total_gross"):
        assert target(key).read_only


def test_no_row_claims_an_automation_id():
    # aids are SWT handle-derived and change every editor instance; the
    # dataclass must not grow a column for them.
    assert not hasattr(CATALOG[0], "automation_id")


def test_editor_rows_are_scoped_to_the_editor():
    for t in CATALOG:
        if t.key.startswith("order."):
            assert t.screen is Screen.ORDER_EDITOR
        if t.key.startswith("toolbar."):
            assert t.screen is Screen.MAIN


def test_unknown_key_names_the_alternatives():
    with pytest.raises(KeyError) as exc:
        target("order.nope")
    assert "order.cust_ref" in str(exc.value)


def test_keys_are_sorted_and_complete():
    assert keys() == sorted(t.key for t in CATALOG)


# --- SendKeys escaping -------------------------------------------------------


@pytest.mark.parametrize(
    "raw,escaped",
    [
        ("X{1}Y", "X{{}1{}}Y"),
        ("a}b{c", "a{}}b{{}c"),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_braces_are_escaped(raw, escaped):
    assert escape_keys(raw) == escaped


@pytest.mark.parametrize("raw", ["A+B^C%D", "PO(9)~Z", "WEB-2026-0714-A17"])
def test_other_sendkeys_metacharacters_are_literal(raw):
    # Measured against this SendKeys: only braces are special. Escaping '+'
    # or '%' as well would type the escape sequence verbatim.
    assert escape_keys(raw) == raw


VALUE_BEARING = ("EditControl", "ComboBoxControl")


def test_every_writable_field_declares_how_to_write_it():
    # set_value dispatches on Input; a writable Edit/ComboBox left at NONE
    # would raise at run time, mid-form.
    for t in CATALOG:
        if t.read_only or t.control_type not in VALUE_BEARING:
            continue
        assert t.input is not Input.NONE, f"{t.key} is writable but declares no input kind"


def test_click_targets_declare_no_input_kind():
    # Buttons, icons and nav links are clicked, never filled.
    for t in CATALOG:
        if t.control_type not in VALUE_BEARING:
            assert t.input is Input.NONE, f"{t.key} is a click target; it cannot take a value"


def test_multiline_is_only_claimed_by_text_fields():
    # The flag exists to suppress the Tab commit, which only makes sense for
    # a typed text field.
    for t in CATALOG:
        if t.multiline:
            assert t.input is Input.TEXT, f"{t.key} claims multiline but is not a text field"
