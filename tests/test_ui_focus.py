"""The two guards that stand between the automation and someone else's data:
where the keyboard actually is, and which dialogs may be closed unattended.

Ctrl+A / Ctrl+C follow the keyboard, not the mouse. These tests pin the one
question the focus guard answers - "is focus actually under the grid I aimed
at?" - without needing Fakturama, by standing in fake controls with runtime ids.
"""

import re

import pytest

import uiautomation as auto
from automation import config, ui


class FakeControl:
    """Just enough of a uiautomation Control to walk a parent chain."""

    def __init__(self, rid, parent=None, name="fake"):
        self._rid = rid
        self._parent = parent
        self.Name = name

    def GetRuntimeId(self):
        if self._rid is None:
            raise RuntimeError("element is gone")
        return list(self._rid)

    def GetParentControl(self):
        return self._parent


@pytest.fixture
def tree():
    """dialog > pane > cell, plus an unrelated widget elsewhere."""
    dialog = FakeControl((42, 1), name="Select a product")
    pane = FakeControl((42, 2), parent=dialog)
    cell = FakeControl((42, 3), parent=pane)
    elsewhere = FakeControl((7, 1), parent=None, name="Items")
    return dialog, pane, cell, elsewhere


def focus_on(monkeypatch, ctrl):
    monkeypatch.setattr(auto, "GetFocusedControl", lambda: ctrl)


class TestFocusIsInside:
    def test_focus_on_a_descendant_counts(self, monkeypatch, tree):
        dialog, _, cell, _ = tree
        focus_on(monkeypatch, cell)
        assert ui.focus_is_inside(dialog)

    def test_focus_on_the_root_itself_counts(self, monkeypatch, tree):
        dialog, _, _, _ = tree
        focus_on(monkeypatch, dialog)
        assert ui.focus_is_inside(dialog)

    def test_focus_on_another_window_does_not(self, monkeypatch, tree):
        # The real case: the Order's Items grid still holds focus while the
        # chooser is on screen. This is what must return False.
        dialog, _, _, elsewhere = tree
        focus_on(monkeypatch, elsewhere)
        assert not ui.focus_is_inside(dialog)

    def test_no_focused_control_is_not_a_pass(self, monkeypatch, tree):
        dialog, _, _, _ = tree
        focus_on(monkeypatch, None)
        assert not ui.focus_is_inside(dialog)

    def test_an_unreadable_root_is_not_a_pass(self, monkeypatch, tree):
        # A stale element raises rather than answering. Treating that as
        # "close enough" is exactly the guess this guard exists to prevent.
        _, _, cell, _ = tree
        focus_on(monkeypatch, cell)
        assert not ui.focus_is_inside(FakeControl(None))

    def test_a_broken_parent_chain_terminates(self, monkeypatch, tree):
        dialog, _, _, _ = tree

        class Exploding(FakeControl):
            def GetParentControl(self):
                raise RuntimeError("tree changed under us")

        focus_on(monkeypatch, Exploding((9, 9)))
        assert not ui.focus_is_inside(dialog)

    def test_a_parent_cycle_cannot_hang(self, monkeypatch, tree):
        dialog, _, _, _ = tree
        a = FakeControl((1, 1))
        b = FakeControl((1, 2), parent=a)
        a._parent = b
        focus_on(monkeypatch, b)
        assert not ui.focus_is_inside(dialog, depth=5)


class FakeDialog:
    """A dialog that reports only its buttons, which is all the rule reads."""

    def __init__(self, name, buttons):
        self.Name = name
        self._buttons = buttons

    def GetChildren(self):
        return [FakeButton(b) for b in self._buttons]


class FakeButton:
    ControlTypeName = "ButtonControl"

    def __init__(self, name):
        self.Name = name

    def GetChildren(self):
        return []


class Vanishing:
    """A control that raises when enumerated, as a disposed element does."""

    ControlTypeName = "PaneControl"
    Name = "gone"

    def GetChildren(self):
        raise OSError("An event was unable to invoke any of the subscribers")


class Solid:
    ControlTypeName = "ButtonControl"

    def __init__(self, name, children=()):
        self.Name = name
        self._children = list(children)

    def GetChildren(self):
        return self._children


class TestFindAllSurvivesADisposedTree:
    """The walks happen exactly when the tree is unstable - just after a
    dialog closes, while SWT disposes its children. A branch that evaporates
    mid-walk must not abort the whole search."""

    def test_a_vanishing_branch_does_not_abort_the_walk(self):
        root = Solid("root", [Vanishing(), Solid("wanted")])
        found = ui.find_all(root, lambda c: c.Name == "wanted")
        assert [c.Name for c in found] == ["wanted"]

    def test_a_vanishing_root_yields_nothing_rather_than_raising(self):
        assert ui.find_all(Vanishing(), lambda c: True) == []

    def test_siblings_after_the_vanishing_one_are_still_reached(self):
        # Order matters: the old code raised on the first bad child and never
        # saw anything past it.
        root = Solid("root", [Solid("a"), Vanishing(), Solid("b"), Vanishing(), Solid("c")])
        found = ui.find_all(root, lambda c: c.ControlTypeName == "ButtonControl")
        assert [c.Name for c in found] == ["a", "b", "c"]

    def test_a_vanishing_branch_deep_in_the_tree(self):
        root = Solid("root", [Solid("mid", [Vanishing(), Solid("leaf")])])
        found = ui.find_all(root, lambda c: c.Name == "leaf")
        assert [c.Name for c in found] == ["leaf"]


class TestWhichDialogsMayBeClosed:
    """A box that reports may be closed; a box that asks may not.

    Decided by the buttons, not the title. Titles were tried first and are too
    weak in both directions - 'Error importing data from web shop' is a plain
    OK box, and a dialog called 'Confirm' might offer three choices.
    """

    @pytest.mark.parametrize(
        "name,buttons",
        [
            # Measured on the live application, title bar buttons included.
            ("Internal Error", ["OK", "Details >>", "Minimize", "Maximize", "Close"]),
            ("Error importing data from web shop", ["OK", "Close"]),
            ("Information", ["OK"]),
        ],
    )
    def test_a_box_with_only_ok_may_be_closed(self, name, buttons):
        assert ui.reports_only(FakeDialog(name, buttons))

    @pytest.mark.parametrize(
        "name,buttons",
        [
            ("Save changes?", ["Yes", "No", "Cancel", "Close"]),
            ("Confirm Delete", ["OK", "Cancel", "Close"]),
            # The product chooser is an OK/Cancel dialog: auto-closing it would
            # silently abandon a selection mid-flow.
            ("Select a product", ["OK", "Cancel", "Minimize", "Maximize", "Close"]),
            ("Overwrite?", ["Retry", "Abort", "Ignore"]),
        ],
    )
    def test_anything_offering_a_choice_stays_open(self, name, buttons):
        assert not ui.reports_only(FakeDialog(name, buttons))

    def test_a_dialog_with_no_content_buttons_is_not_touched(self):
        # Only a title bar: nothing here says it is safe to act on.
        assert not ui.reports_only(FakeDialog("Progress", ["Close", "Minimize"]))
