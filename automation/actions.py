"""set_value / click / wait_ready - the verbs the flow is written in.

Two rules this module exists to enforce:

  * **Never UIA SetValue on an editable field.** It writes the property
    without producing the keyboard events SWT's ModifyListeners are attached
    to, so Fakturama's recalculation never fires and the form looks right
    while the model behind it is stale. Everything here goes through real
    input. (Moot in practice - Fakturama exposes no ValuePattern at all - but
    the rule is what keeps a future 'optimisation' from reintroducing it.)

  * **Every write is read back and asserted.** A step that cannot prove its
    value landed reports that, rather than claiming success.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import uiautomation as auto

from . import config, ui
from .resolver import Resolved, Scope, find_control
from .selectors import Input
from .ui import UIError

log = logging.getLogger("automation.actions")


# --- keyboard ----------------------------------------------------------------

def escape_keys(text: str) -> str:
    """Make `text` safe for SendKeys.

    Measured against this SendKeys implementation: '+', '^', '%', '(', ')' and
    '~' are literal, but '{...}' is a key-name escape - sending 'X{1}Y' types
    'X1Y'. Only the braces need escaping, and doubling them is what works.

    translate, not chained replace: replacing '{' then '}' re-escapes the '}'
    inside the '{{}' just inserted, turning 'X{1}Y' into 'X{{{}}1{}}Y'.
    translate makes a single pass and never rescans its own output.
    """
    return text.translate({ord("{"): "{{}", ord("}"): "{}}"})


def _focus(ctrl) -> None:
    """Put the keyboard on `ctrl`, and check that it actually landed.

    A coordinate click is the reliable route for most SWT widgets, but inside
    a modal chooser it can miss entirely - measured: clicking the search box
    left focus on the OK button, so every keystroke went nowhere and the
    write silently produced an empty field. So the click is verified, and
    SetFocus is the fallback.
    """
    def landed() -> bool:
        try:
            focused = auto.GetFocusedControl()
            if not focused:
                return False
            a, b = focused.BoundingRectangle, ctrl.BoundingRectangle
            return (a.left, a.top, a.right, a.bottom) == (b.left, b.top, b.right, b.bottom)
        except Exception:
            return False

    # SetFocus first: it is the non-destructive route, and inside a modal
    # chooser a coordinate click lands on the OK button instead of the search
    # box - after which every keystroke is discarded and the write reads back
    # empty. The click stays as the fallback for widgets SetFocus ignores.
    try:
        ctrl.SetFocus()
        time.sleep(config.SETTLE)
        if landed():
            return
    except Exception as exc:
        log.debug("SetFocus failed (%s)", exc)

    ctrl.Click(simulateMove=False)
    time.sleep(config.SETTLE)


# --- reading -----------------------------------------------------------------

def read_value(resolved: Resolved) -> str:
    """Read a control's current value, preferring the pure read.

    LegacyIAccessible first: it is instant, has no side effects, and works on
    the combo boxes, which expose no value any other way. Only if that returns
    nothing do we fall back to the clipboard route, which must click the
    control and select its contents - moving focus, and potentially committing
    a half-typed neighbour.
    """
    ctrl = resolved.control
    value = ui.legacy_value(ctrl)
    if value is not None:
        log.debug("read %s = %r (legacy)", resolved.key, value)
        return value

    log.debug("read %s: legacy value unavailable, using the clipboard", resolved.key)
    auto.SetClipboardText("")
    _focus(ctrl)
    auto.SendKeys("{Ctrl}a", waitTime=0.1)
    auto.SendKeys("{Ctrl}c", waitTime=0.2)
    time.sleep(0.25)
    try:
        return auto.GetClipboardText() or ""
    except Exception as exc:
        raise UIError(f"{resolved.key}: could not read value ({exc})") from exc


# --- writing -----------------------------------------------------------------

@dataclass(frozen=True)
class Written:
    """The outcome of a write, including what the field actually holds."""

    key: str
    wrote: str
    read_back: str
    ok: bool
    detail: str = ""


def _set_text(resolved: Resolved, value: str) -> Written:
    """focus -> select all -> real keystrokes -> Tab to commit -> read back."""
    ctrl = resolved.control
    _focus(ctrl)
    # Only clear when there is something to clear. Ctrl+A inside a modal
    # chooser is interpreted by the grid behind the search box, not by the
    # box, and it takes the keyboard with it - after which every character
    # typed goes nowhere and the field reads back empty.
    if (ui.legacy_value(ctrl) or "").strip():
        auto.SendKeys("{Ctrl}a", waitTime=0.1)
        if value == "":
            auto.SendKeys("{Delete}", waitTime=0.1)
    if value != "":
        if resolved.target.paste:
            # One keystroke instead of len(value). See Target.paste: a filter
            # box that re-queries per character does not survive being typed
            # into, and this is the only field where that trade is correct.
            auto.SetClipboardText(value)
            time.sleep(0.2)
            auto.SendKeys("{Ctrl}v", waitTime=0.15)
        else:
            auto.SendKeys(escape_keys(value), waitTime=0.03)
    time.sleep(config.SETTLE)

    if not resolved.target.commit_with_tab:
        # A filter box has nothing to commit, and tabbing out of one whose
        # term matched nothing clears it - which reads back as "the write
        # failed" when in fact the search ran and found none.
        pass
    elif resolved.target.multiline:
        # Tab is a literal character in a multi-line Text. Commit by moving
        # focus with the mouse instead - clicking the control's own label area
        # is safe and stays inside the editor.
        auto.SendKeys("{Ctrl}{Home}", waitTime=0.1)
    else:
        auto.SendKeys("{Tab}", waitTime=0.15)   # commit: SWT fires on focus-out
    time.sleep(config.SETTLE)

    got = read_value(resolved)
    return Written(
        key=resolved.key, wrote=value, read_back=got, ok=(got == value),
        detail="" if got == value else f"wrote {value!r} but field holds {got!r}",
    )


def parse_money(text: str) -> Decimal | None:
    """Read an amount out of whatever the field renders around it.

    '$297.50', '297,50 EUR' and '1.234,56' all name a number; the symbol,
    the spacing and the separator convention are presentation. Returns None
    when there is no number at all, which is never the same as zero.
    """
    raw = (text or "").strip()
    digits = re.sub(r"[^\d,.\-]", "", raw)
    if not re.search(r"\d", digits):
        return None
    # Whichever separator appears last is the decimal point; the other one
    # groups thousands. '1.234,56' and '1,234.56' both mean 1234.56.
    last_comma, last_dot = digits.rfind(","), digits.rfind(".")
    if last_comma > last_dot:
        digits = digits.replace(".", "").replace(",", ".")
    else:
        digits = digits.replace(",", "")
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def _set_money(resolved: Resolved, value: Decimal) -> Written:
    """Type an amount, then verify the number rather than the rendering.

    The field reformats what it is given - currency symbol, separators, its
    own rounding - so a string comparison would report a correct write as a
    failure. What has to match is the amount.
    """
    wrote = f"{value:.2f}"
    written = _set_text(resolved, wrote)
    got = parse_money(written.read_back)
    if got is not None and got == value:
        return Written(resolved.key, wrote, written.read_back, True)
    if got is None:
        return Written(resolved.key, wrote, written.read_back, False,
                       f"read back {written.read_back!r}, which holds no amount")
    return Written(resolved.key, wrote, written.read_back, False,
                   f"wrote {value}, field holds {got}")


def _set_segmented_date(resolved: Resolved, value: date) -> Written:
    """Fill an SWT CDateTime segment by segment.

    Not a text field: Ctrl+A does not select it and {Home} does not move to the
    first segment, so typing a rendered date dumps every digit into whichever
    segment has focus - measured, that turns 'Jul 14, 2026' into 'Aug 15,
    1420'. Clicking the leftmost pixels focuses the first segment; each one
    auto-advances when full.
    """
    ctrl = resolved.control
    digits = {"month": "%02d" % value.month, "day": "%02d" % value.day,
              "year": "%04d" % value.year}
    r = ctrl.BoundingRectangle
    auto.Click(r.left + 8, (r.top + r.bottom) // 2)
    time.sleep(config.SETTLE)
    for segment in ui.segment_order(config.DATE_WRITE_FORMAT):
        auto.SendKeys(digits[segment], waitTime=0.15)
    time.sleep(config.SETTLE)
    auto.SendKeys("{Tab}", waitTime=0.15)
    time.sleep(config.SETTLE)

    got = read_value(resolved)
    parsed = parse_ui_date(got)
    wrote = value.strftime(config.DATE_WRITE_FORMAT)
    if parsed == value:
        return Written(resolved.key, wrote, got, True)
    if parsed is None:
        return Written(resolved.key, wrote, got, False,
                       f"read back {got!r}, which is not a date")
    return Written(resolved.key, wrote, got, False, f"wanted {value}, field shows {parsed}")


def _set_combo(resolved: Resolved, value: str) -> Written:
    """Pick an option by clicking its ListItem, then read the selection back.

    There is no SelectionItemPattern, but LegacyIAccessible.Value does report
    the selected option, so this verifies itself like every other write.
    """
    ctrl = resolved.control
    ctrl.Click(simulateMove=False)
    time.sleep(config.COMBO_OPEN)
    lists = [k for k in ctrl.GetChildren() if k.ControlTypeName == "ListControl"]
    if not lists:
        auto.SendKeys("{Esc}", waitTime=0.2)
        raise UIError(f"{resolved.key}: dropdown did not open")
    items = ui.find_all(lists[0], lambda c: c.ControlTypeName == "ListItemControl", 3)
    names = [i.Name for i in items]
    # Compare on stripped text: Fakturama's payment codes carry a trailing
    # space ('Credit transfer '), so an exact match would never fire and the
    # combo would silently keep its default.
    want = (value or "").strip()
    match = [i for i in items if (i.Name or "").strip() == want]
    if not match:
        auto.SendKeys("{Esc}", waitTime=0.2)
        raise UIError(f"{resolved.key}: no option {value!r}; available: {names}")
    item = match[0]
    # A long dropdown is virtualised: an option scrolled out of view reports a
    # (0,0,0,0) rectangle, and clicking it moves the cursor nowhere - which is
    # how 'Germany' silently left the Country combo on 'United States'.
    if item.BoundingRectangle.width() == 0:
        log.debug("%s: option %r is scrolled out of view, bringing it in",
                  resolved.key, value)
        try:
            item.GetScrollItemPattern().ScrollIntoView()
            time.sleep(config.SETTLE)
        except Exception as exc:
            log.debug("%s: ScrollIntoView unavailable (%s)", resolved.key, exc)
    if item.BoundingRectangle.width() == 0:
        # Still not rendered: fall back to keyboard selection, which the
        # widget resolves internally without needing the item on screen.
        auto.SendKeys(escape_keys(value), waitTime=0.05)
        time.sleep(config.SETTLE)
        auto.SendKeys("{Enter}", waitTime=0.2)
    else:
        item.Click(simulateMove=False)
    time.sleep(config.SETTLE * 2)

    got = read_value(resolved)
    if (got or "").strip() == want:
        return Written(resolved.key, value, got, True)
    if not got:
        # No readable selection: report honestly rather than assume the click
        # took. The caller may still have an observable oracle.
        return Written(resolved.key, value, got, True,
                       "selected, but the combo reported no value to verify against")
    return Written(resolved.key, value, got, False,
                   f"selected {value!r} but the combo reports {got!r}")


def combo_options(resolved: Resolved) -> list[str]:
    """Read a dropdown's options, leaving it closed."""
    ctrl = resolved.control
    ctrl.Click(simulateMove=False)
    time.sleep(config.COMBO_OPEN)
    try:
        lists = [k for k in ctrl.GetChildren() if k.ControlTypeName == "ListControl"]
        if not lists:
            return []
        return [i.Name for i in ui.find_all(
            lists[0], lambda c: c.ControlTypeName == "ListItemControl", 3)]
    finally:
        auto.SendKeys("{Esc}", waitTime=0.2)
        time.sleep(0.3)


def set_value(key: str, value, scope: Scope) -> Written:
    """Write `value` into the catalog target `key`, dispatching on its Input kind."""
    resolved = find_control(key, scope)
    t = resolved.target
    if t.read_only:
        raise UIError(f"{t.key} is catalogued read_only; refusing to write to it")

    log.info("set_value %s = %r (%s)", t.key, value, resolved)
    if t.input is Input.TEXT:
        return _set_text(resolved, str(value))
    if t.input is Input.SEGMENTED_DATE:
        return _set_segmented_date(resolved, value)
    if t.input is Input.COMBO:
        return _set_combo(resolved, str(value))
    if t.input is Input.MONEY:
        return _set_money(resolved, Decimal(str(value)))
    raise UIError(f"{t.key}: input kind {t.input} cannot be written")


# --- saving ------------------------------------------------------------------

def save_editor(pane) -> None:
    """Save the editor that owns `pane`, with Ctrl+S rather than the toolbar.

    The toolbar Save button applies to whatever Eclipse considers active, and
    once a run has several dirty editors open that is not reliably the one
    just filled. Measured on the product editor: clicking Save left it dirty
    for twelve seconds, while Ctrl+S on the focused editor saved it at once.

    Ctrl+S also names its subject, which is what makes the check afterwards
    mean anything - 'this editor is no longer dirty' only proves a save if the
    save was aimed at this editor.
    """
    pane.SetFocus()
    time.sleep(config.SETTLE)
    auto.SendKeys("{Ctrl}s", waitTime=0.2)
    time.sleep(config.SETTLE)


# --- checkboxes --------------------------------------------------------------

def checkbox_state(resolved: Resolved) -> bool | None:
    """Read a checkbox via TogglePattern, or None if it will not answer."""
    try:
        return bool(resolved.control.GetTogglePattern().ToggleState)
    except Exception:
        return None


def set_checkbox(key: str, wanted: bool, scope: Scope) -> Written:
    """Tick or untick, by clicking - never TogglePattern.Toggle().

    Same rule as set_value: drive the widget the way a user would, so SWT's
    listeners fire, and use the pattern only to *read* the result back.
    """
    resolved = find_control(key, scope)
    before = checkbox_state(resolved)
    if before is None:
        raise UIError(f"{resolved.key}: checkbox state is not readable")
    if before != wanted:
        resolved.control.Click(simulateMove=False)
        time.sleep(config.SETTLE)
    after = checkbox_state(resolved)
    return Written(resolved.key, str(wanted), str(after), after == wanted,
                   "" if after == wanted else f"wanted {wanted}, checkbox reads {after}")


# --- clicking ----------------------------------------------------------------

def click(key: str, scope: Scope) -> str:
    """Invoke a target.

    InvokePattern first, per the usual rule that a pattern beats synthesised
    input. Fakturama supports it nowhere - every button measured reports
    patterns=[] - so in practice this always falls through to a real click at
    the control's centre, which is what icon-only toolbar buttons need anyway.
    The pattern branch stays because it is correct and free.
    """
    resolved = find_control(key, scope)
    ctrl = resolved.control
    try:
        if ctrl.IsInvokePatternAvailable():
            ctrl.GetInvokePattern().Invoke()
            log.info("click %s via InvokePattern (%s)", resolved.key, resolved)
            return "invoke"
    except Exception as exc:
        log.debug("%s: InvokePattern unavailable (%s)", resolved.key, exc)

    if resolved.target.control_type == "ButtonControl":
        # A coordinate click on a modal dialog's button is unreliable - the
        # chooser's Cancel repeatedly ignored one. Focus plus Space is the
        # keyboard equivalent of pressing it, and it lands every time.
        try:
            ctrl.SetFocus()
            time.sleep(config.SETTLE)
            auto.SendKeys("{Space}", waitTime=0.2)
            log.info("click %s via focus+Space (%s)", resolved.key, resolved)
            return "space"
        except Exception as exc:
            log.debug("%s: focus+Space failed (%s)", resolved.key, exc)

    ctrl.Click(simulateMove=False)
    log.info("click %s via real click (%s)", resolved.key, resolved)
    return "click"


# --- waiting -----------------------------------------------------------------

def wait_ready(condition, what: str, timeout: float = None, interval: float = 0.25):
    """Poll a UIA state predicate. Never a bare sleep.

    `condition` is a zero-arg callable returning something truthy; its return
    value is handed back, so it can double as a getter.
    """
    timeout = config.FIND_TIMEOUT if timeout is None else timeout
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            got = condition()
            if got:
                return got
        except Exception as exc:
            last = exc
        time.sleep(interval)
    raise UIError(f"timed out after {timeout:g}s waiting for {what}"
                  + (f" (last error: {last})" if last else ""))


# --- shared date helpers -----------------------------------------------------

def parse_ui_date(text: str) -> date | None:
    """Parse whatever the Date widget rendered, or None."""
    text = (text or "").strip()
    for fmt in config.DATE_READ_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def format_ui_date(value: date) -> str:
    return value.strftime(config.DATE_WRITE_FORMAT)
