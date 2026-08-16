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
import time
from dataclasses import dataclass
from datetime import date, datetime

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
    auto.SendKeys("{Ctrl}a", waitTime=0.1)
    if value == "":
        auto.SendKeys("{Delete}", waitTime=0.1)
    else:
        auto.SendKeys(escape_keys(value), waitTime=0.03)
    time.sleep(config.SETTLE)
    auto.SendKeys("{Tab}", waitTime=0.15)   # commit: SWT fires on focus-out
    time.sleep(config.SETTLE)

    got = read_value(resolved)
    return Written(
        key=resolved.key, wrote=value, read_back=got, ok=(got == value),
        detail="" if got == value else f"wrote {value!r} but field holds {got!r}",
    )


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
    match = [i for i in items if i.Name == value]
    if not match:
        auto.SendKeys("{Esc}", waitTime=0.2)
        raise UIError(f"{resolved.key}: no option {value!r}; available: {names}")
    match[0].Click(simulateMove=False)
    time.sleep(config.SETTLE * 2)

    got = read_value(resolved)
    if got == value:
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
    raise UIError(f"{t.key}: input kind {t.input} cannot be written")


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
