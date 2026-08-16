"""UIA primitives: tree walking, the application window, the clipboard.

Deliberately thin. Locating controls is resolver.py's job, acting on them is
actions.py's - this module only knows how to walk a UIA tree and find
Fakturama's shell.

Three measured facts about Fakturama drive the design of everything above:

  1. **AutomationIds are worthless.** They come from SWT widget handles;
     closing and reopening the editor changed 14 of 15 (the Cust.Ref. field
     was 67590, then 133234, then 396046 across three instances). Nothing may
     locate by aid - see selectors.py, which has no column for it.

  2. **There are no UIA patterns.** Not ValuePattern, not InvokePattern, not
     LegacyIAccessible - `patterns=[]` on every control measured, buttons
     included. Values move through the clipboard and real keystrokes.

  3. **There are no tooltips.** HelpText is empty everywhere; SWT publishes
     the tooltip as the accessible Name instead.
"""

from __future__ import annotations

import re
import time

import uiautomation as auto

from . import config


class UIError(RuntimeError):
    """The UI was not in a state this step could act on."""


# --- tree walking ------------------------------------------------------------


def find_all(root, pred, max_depth: int = 14) -> list:
    """Every descendant satisfying `pred`. uiautomation has no find-all."""
    out = []

    def walk(ctrl, depth=0):
        if depth > max_depth:
            return
        for child in ctrl.GetChildren():
            try:
                if pred(child):
                    out.append(child)
            except Exception:
                # Controls vanish mid-walk while SWT redraws. Not an error -
                # just not a match.
                pass
            walk(child, depth + 1)

    walk(root)
    return out


def find_one(root, pred, what: str, max_depth: int = 14):
    """Exactly one match, or an error naming the ambiguity.

    Never returns hits[0] from several: taking an arbitrary match is how
    automation types into the wrong field while the log still says "found".
    """
    hits = find_all(root, pred, max_depth)
    if not hits:
        raise UIError(f"could not find {what}")
    if len(hits) > 1:
        rects = ", ".join(str(h.BoundingRectangle) for h in hits[:4])
        raise UIError(f"{what} is ambiguous: {len(hits)} matches ({rects})")
    return hits[0]


# --- the application ---------------------------------------------------------


def window():
    win = auto.WindowControl(searchDepth=1, RegexName=config.WINDOW_RE)
    if not win.Exists(config.FIND_TIMEOUT):
        raise UIError("Fakturama window not found - is the application running?")
    return win


def activate(win) -> None:
    win.SetActive()
    time.sleep(config.SETTLE)


_EDITOR_RE = re.compile(config.ORDER_EDITOR_RE)


def order_editor(win):
    """The New Order editor's content Pane, or None.

    Anchored on the Pane, not the TabControl: a TabControl's Name is whichever
    tab is *selected*, and it gains a '*' the moment the editor is dirty. The
    Pane keeps the clean title through both.
    """
    panes = find_all(
        win, lambda c: c.ControlTypeName == "PaneControl" and _EDITOR_RE.match(c.Name or "")
    )
    if not panes:
        return None
    # The title also appears on small chrome panes; the editor is the big one.
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def order_tabs(win) -> list:
    """Every New Order editor tab, dirty or not."""
    return [
        c for c in find_all(win, lambda c: c.ControlTypeName == "TabItemControl")
        if (c.Name or "").lstrip("*") == "New Order"
    ]


def activate_order_editor(win, index: int = -1):
    """Bring a New Order editor to the front and return its pane.

    SWT only realises the controls of the *active* editor, so a New Order tab
    that is not selected is invisible to UIA - order_editor() returns None
    even though the tab is right there. Selecting the tab is therefore part of
    locating the editor, not a nicety.
    """
    editor = order_editor(win)
    if editor is not None:
        return editor
    tabs = order_tabs(win)
    if not tabs:
        return None
    tabs[index].Click(simulateMove=False)
    time.sleep(config.SETTLE * 2)
    return order_editor(win)


_DEBTOR_RE = re.compile(config.DEBTOR_EDITOR_RE)


def debtor_editor(win):
    """The New Debtor editor's content Pane, or None."""
    panes = find_all(
        win, lambda c: c.ControlTypeName == "PaneControl" and _DEBTOR_RE.match(c.Name or "")
    )
    if not panes:
        return None
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def address_dialog(win):
    """The modal 'Select the address' chooser, or None."""
    for w in find_all(win, lambda c: c.ControlTypeName == "WindowControl", 6):
        if (w.Name or "").startswith(config.ADDRESS_DIALOG_TITLE):
            return w
    return None


def activate_editor(win, title: str, finder):
    """Bring the editor whose tab is `title` to the front and return its pane.

    SWT realises only the active editor's controls, so selecting the tab is
    part of locating any editor that is not already in front.
    """
    pane = finder(win)
    if pane is not None:
        return pane
    for item in find_all(win, lambda c: c.ControlTypeName == "TabItemControl"):
        if (item.Name or "").lstrip("*") == title:
            item.Click(simulateMove=False)
            time.sleep(config.SETTLE * 3)
            return finder(win)
    return None


def is_dirty(win) -> bool:
    """True when the editor has unsaved changes (SWT's '*' title prefix)."""
    items = find_all(win, lambda c: c.ControlTypeName == "TabItemControl")
    return any((i.Name or "").startswith("*New Order") for i in items)


# --- reading values ----------------------------------------------------------


def legacy_value(ctrl) -> str | None:
    """The control's value via the LegacyIAccessible pattern, or None.

    Worth trying even though `IsLegacyIAccessiblePatternAvailable()` reports
    False on every Fakturama control: the availability flag lies, and calling
    the pattern anyway returns real values - including for the combo boxes,
    which expose nothing else.

    This is the preferred read path because it is a *pure* read. The clipboard
    route has to click the control and select its contents, which moves focus
    and can commit a half-typed neighbour.
    """
    try:
        value = ctrl.GetLegacyIAccessiblePattern().Value
    except Exception:
        return None
    return value if value is not None else None


# --- chooser grids -----------------------------------------------------------


def grid_pane(dialog):
    """The NatTable body inside a chooser dialog."""
    panes = [p for p in find_all(dialog, lambda c: c.ControlTypeName == "PaneControl", 16)
             if p.BoundingRectangle.height() > 150]
    if not panes:
        raise UIError("no grid pane in this dialog")
    return min(panes, key=lambda p: p.BoundingRectangle.height())


def grid_rows(dialog) -> list[list[str]]:
    """Read every visible row as a list of cell strings.

    The grid publishes nothing to UIA - no ListItems, no DataItems - but it
    *does* implement Ctrl+A / Ctrl+C, and copies tab-separated rows. That is
    the only channel to the row data, and it is enough to decide an exact
    match rather than guessing from position.

    (An earlier reading of this project concluded the grid was unreadable.
    That measurement was taken against an empty list: nothing copied because
    there was nothing in it.)
    """
    try:
        pane = grid_pane(dialog)
    except UIError:
        # When a filter matches nothing the grid body collapses, and there is
        # no pane left to click. That is an empty result, not a failure.
        return []
    r = pane.BoundingRectangle
    auto.SetClipboardText("")
    # One click only. Reading and then selecting used to click the same row
    # twice in quick succession, which the chooser took as a double-click -
    # accepting the row and adding the wrong product to the order.
    auto.Click(r.left + 80, r.top + config.GRID_FIRST_ROW_DY)
    time.sleep(config.SETTLE * 2)
    auto.SendKeys("{Ctrl}a", waitTime=0.2)
    auto.SendKeys("{Ctrl}c", waitTime=0.3)
    time.sleep(config.SETTLE)
    text = auto.GetClipboardText() or ""
    return [line.split("\t") for line in text.splitlines() if line.strip()]


def grid_select_row(dialog, index: int) -> list[str]:
    """Click the row at `index` and return what the grid says is selected.

    Selecting still needs a coordinate - the rows are painted, not published -
    but the click is immediately proved by copying the selected row back, so a
    mis-aimed click is caught rather than committed.
    """
    pane = grid_pane(dialog)
    r = pane.BoundingRectangle
    y = r.top + config.GRID_FIRST_ROW_DY + index * config.GRID_ROW_HEIGHT
    auto.SetClipboardText("")
    auto.Click(r.left + 80, y)
    time.sleep(config.SETTLE)
    auto.SendKeys("{Ctrl}c", waitTime=0.3)
    time.sleep(config.SETTLE)
    text = auto.GetClipboardText() or ""
    return text.splitlines()[0].split("\t") if text.strip() else []


# --- tooltips ----------------------------------------------------------------


def normalize_tip(text: str) -> str:
    """Fold whitespace and case - the differences a tooltip never means by."""
    return " ".join((text or "").split()).strip().casefold()


def _visible_tooltip(root=None) -> str | None:
    """The text of whatever tooltip is on screen right now, if any."""
    root = root or auto.GetRootControl()
    for c in find_all(root, lambda c: c.ControlTypeName == "ToolTipControl", 4):
        if c.Name:
            return c.Name
        kids = [k.Name for k in find_all(c, lambda k: bool(k.Name), 3)]
        if kids:
            return " | ".join(kids)
    return None


def blocking_popups(win) -> list[str]:
    """Modal windows that will suppress tooltips while they are open.

    An inactive window does not render tooltips, so any open dialog - a
    chooser left behind, an 'Internal Error' box - silently disables the whole
    tooltip layer. Every guarded control then drops to a structural match, and
    the guarded ones refuse to act at all. Diagnosed the hard way: a stray
    error dialog made the item-delete icon unresolvable, and the positional
    fallback clicked *paste* instead.
    """
    names = [w.Name for w in find_all(win, lambda c: c.ControlTypeName == "WindowControl", 6)
             if w.Name]
    # Some dialogs - notably Eclipse's 'Internal Error' box - are siblings of
    # the shell at the desktop root, not children of it. Searching only inside
    # the shell reported "no popups" while one held the foreground and
    # suppressed every tooltip in the application.
    try:
        foreground = auto.GetForegroundControl()
        if foreground is not None:
            name, cls = foreground.Name or "", foreground.ClassName or ""
            # Only a real dialog counts. Matching on "not the shell" would
            # flag the terminal the automation is launched from, and refuse
            # to do anything at all.
            if name != (win.Name or "") and (cls == "#32770" or "Error" in name):
                names.append(name)
    except Exception:
        pass
    return names


def tooltip_of(ctrl, timeout: float = None) -> str | None:
    """Hover the control and read its tooltip.

    SWT does not publish tooltips as a property - HelpText is empty on every
    control - but it does render them as a transient ToolTipControl on the
    desktop. So the text is reachable; it just has to be provoked.

    The cursor is parked away first (a lingering tip from the previous probe
    would be read instead) and jiggled on arrival, because SWT starts its
    hover timer on mouse *movement* within the control.

    Costs a real mouse move and up to `timeout` seconds, so callers should
    cache. Returns None if no tooltip appears.
    """
    timeout = config.TOOLTIP_TIMEOUT if timeout is None else timeout

    # Retried: the hover is intermittent, not broken. A single miss otherwise
    # drops a control to the structural layer - and for a guarded icon (the
    # 'new debtor' +, the item delete) it blocks the action entirely, because
    # those refuse to be clicked without semantic confirmation.
    for attempt in range(config.TOOLTIP_ATTEMPTS):
        r = ctrl.BoundingRectangle
        cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2

        # Park somewhere that is definitely not another control, then wait for
        # the previous tooltip to actually disappear. These icons are stacked
        # a few pixels apart, so a fixed offset parks on a *sibling* - whose
        # tooltip is then read back as if it were this one's.
        auto.SetCursorPos(cx, max(r.top - 220, 5))
        clear_by = time.monotonic() + 1.0
        while time.monotonic() < clear_by and _visible_tooltip():
            time.sleep(0.1)
        time.sleep(config.TOOLTIP_PARK)

        # simulateMove generates the intermediate WM_MOUSEMOVE events SWT's
        # hover timer waits for. SetCursorPos alone teleports the pointer, and
        # a pointer that never "moved" onto the control never starts the timer.
        try:
            ctrl.MoveCursorToMyCenter(simulateMove=True)
        except Exception:
            auto.SetCursorPos(cx, cy)
        time.sleep(0.05)
        auto.SetCursorPos(cx + 2, cy + 1)
        time.sleep(0.05)
        auto.SetCursorPos(cx - 1, cy)      # a second nudge restarts the timer

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            txt = _visible_tooltip()
            if txt:
                return txt
            time.sleep(0.15)
    return None


# --- date segments -----------------------------------------------------------


def segment_order(fmt: str) -> list[str]:
    """Which of month/day/year the date widget renders first, second, third.

    Derived from the format, never assumed: a 'dd.MM.yyyy' workspace puts the
    day leftmost, and typing the month into it produces a wrong date that
    still looks plausible.
    """
    found: list[tuple[int, str]] = []
    for token, kind in (("%b", "month"), ("%B", "month"), ("%m", "month"),
                        ("%d", "day"), ("%Y", "year"), ("%y", "year")):
        i = fmt.find(token)
        if i >= 0:
            found.append((i, kind))
    found.sort()
    out: list[str] = []
    for _, kind in found:
        if kind not in out:
            out.append(kind)
    return out


# --- clipboard ---------------------------------------------------------------


class Clipboard:
    """Borrow the clipboard, then put back what the user had in it."""

    def __enter__(self):
        try:
            self._saved = auto.GetClipboardText()
        except Exception:
            self._saved = None
        return self

    def __exit__(self, *exc):
        try:
            if self._saved:
                auto.SetClipboardText(self._saved)
        except Exception:
            pass
        return False
