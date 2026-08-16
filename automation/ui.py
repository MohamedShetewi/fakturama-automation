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
    r = ctrl.BoundingRectangle
    cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    auto.SetCursorPos(cx, max(r.top - 220, 5))
    time.sleep(config.TOOLTIP_PARK)
    auto.SetCursorPos(cx, cy)
    time.sleep(0.05)
    auto.SetCursorPos(cx + 2, cy + 1)

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
