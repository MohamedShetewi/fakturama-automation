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

import logging
import re
import subprocess
import time
from dataclasses import dataclass

import uiautomation as auto

from . import config

log = logging.getLogger("automation.ui")


class UIError(RuntimeError):
    """The UI was not in a state this step could act on."""


# --- tree walking ------------------------------------------------------------


def _children(ctrl) -> list:
    """A control's children, or none if it disappeared while we asked.

    Enumerating a disposed element does not return empty - it raises, out of
    the COM layer:

        _ctypes.COMError: An event was unable to invoke any of the subscribers

    which used to abort the entire tree walk. That matters because the walks
    happen exactly when the tree is unstable: right after a dialog closes,
    while SWT is disposing its children. A branch that evaporates mid-walk is
    a normal event here, not a failure of the search.
    """
    try:
        return ctrl.GetChildren()
    except Exception as exc:
        log.debug("a control vanished while walking into it (%s)", exc)
        return []


def find_all(root, pred, max_depth: int = 14) -> list:
    """Every descendant satisfying `pred`. uiautomation has no find-all."""
    out = []

    def walk(ctrl, depth=0):
        if depth > max_depth:
            return
        for child in _children(ctrl):
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


def _process_ids(image: str = "Fakturama.exe") -> list[str]:
    """The pids of a named process, or [] if none and [] if we cannot tell."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []
    return [line.split(",")[1].strip('" ') for line in out.splitlines()
            if line.startswith(f'"{image}"')]


def window():
    win = auto.WindowControl(searchDepth=1, RegexName=config.WINDOW_RE)
    if win.Exists(config.FIND_TIMEOUT):
        return win

    # Distinguish "not started" from "started and hung". Fakturama has been
    # seen surviving a crash as a process with no window at all, which holds
    # the workspace lock and makes a fresh instance fail too - so "is it
    # running?" is exactly the wrong question to send someone away with.
    pids = _process_ids()
    if pids:
        raise UIError(
            f"Fakturama is running (pid {', '.join(pids)}) but has no window. "
            "It has most likely crashed; end that process and start it again "
            "before re-running - a windowless instance still holds the "
            "workspace lock."
        )
    raise UIError("Fakturama window not found - is the application running?")


def activate(win) -> None:
    """Bring the shell forward, clearing any error box in the way first.

    A modal error dialog does not just sit there: it holds activation, and an
    inactive application renders no tooltips at all, which silently disables
    the resolver's semantic layer for the whole run. So clearing it is part of
    activating, not a separate courtesy.
    """
    for report in dismiss_error_dialogs(win):
        log.warning("cleared before activating: %s", report)
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


def editor_named(win, title: str):
    """The content Pane of the editor whose title is `title`, dirty or not.

    Saving renames these editors: a 'New Order' becomes 'PO000001' the moment
    it is written, exactly as a 'New product' becomes the product's name. So
    anything that acts on a document *after* saving it has to look for the new
    title, not the one it opened.
    """
    panes = find_all(win, lambda c: c.ControlTypeName == "PaneControl"
                     and (c.Name or "").lstrip("*") == title)
    if not panes:
        return None
    return max(panes, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def editor_is_dirty(win, title: str) -> bool:
    """True when an editor titled `title` has unsaved changes.

    The star lives on the *tab*, never on the pane - the pane keeps its clean
    title through the whole edit. Asking the pane therefore always answers
    "clean", which is worse than no check at all: a save verified that way
    passes the instant it is asked, and reports a product written when nothing
    was.
    """
    items = find_all(win, lambda c: c.ControlTypeName == "TabItemControl")
    return any((i.Name or "") == "*" + title for i in items)


def is_dirty(win) -> bool:
    """True when the New Order editor has unsaved changes."""
    return editor_is_dirty(win, "New Order")


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


def _runtime_id(ctrl):
    try:
        return tuple(ctrl.GetRuntimeId())
    except Exception:
        return None


def focus_is_inside(root, depth: int = 30) -> bool:
    """Is the keyboard focus somewhere under `root`?

    Ctrl+A and Ctrl+C are not sent *to* a control - they go wherever the
    keyboard focus happens to be. Aiming a click at a grid is not the same as
    the grid having focus, and when it does not, the copy silently reads some
    other widget.

    This is the guard for a failure that cost a whole session: the product
    chooser's grid was clicked while focus was still on the Order's Items
    grid, so Ctrl+A selected the order lines and Ctrl+C hit Fakturama's copy
    handler in a state it does not support -

        java.lang.NullPointerException: Cannot read the array length because
        "this.copiedCells" is null

    - which raised a modal error box, which suppressed every tooltip in the
    application, which made the next run's guarded icons unresolvable. One
    unverified keystroke, three layers of consequence.
    """
    want = _runtime_id(root)
    if want is None:
        return False
    node = auto.GetFocusedControl()
    for _ in range(depth):
        if node is None:
            return False
        if _runtime_id(node) == want:
            return True
        try:
            node = node.GetParentControl()
        except Exception:
            return False
    return False


def _claim_grid(dialog, x: int, y: int) -> bool:
    """Click into the grid and report whether the keyboard followed.

    An empty grid has nothing at that coordinate to select, so the click lands
    on bare canvas and focus never enters the dialog. That is the whole signal:
    no row took focus means there is no row.

    It matters because the alternative is silent corruption. Ctrl+A and Ctrl+C
    go wherever focus *is*, so sending them after a missed click aimed them at
    the Order's own Items grid, where Fakturama's copy handler died -

        java.lang.NullPointerException: Cannot read the array length because
        "this.copiedCells" is null

    - raising a modal error box, which suppressed every tooltip in the
    application, which left the next run's guarded icons unresolvable. One
    unverified click, three layers of consequence.

    One retry first, because activation does genuinely lose races with SWT
    redraws and a single miss is not proof of an empty list.
    """
    for attempt in (1, 2):
        auto.Click(x, y)
        time.sleep(config.SETTLE * 2)
        if focus_is_inside(dialog):
            return True
        log.debug("grid focus did not land (attempt %d); re-activating", attempt)
        try:
            dialog.SetActive()
        except Exception:
            pass
        time.sleep(config.SETTLE)
    return False


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

    An empty result is a normal answer here, not a failure: it is how a search
    that matched nothing reports itself, and the caller's next move is to
    create the record rather than select one.
    """
    return grid_read(dialog).rows


def items_grid(editor):
    """The order's Items table, found by its own label.

    Emphatically not "the smallest pane over 150px", which is how the chooser
    and list views are found. That heuristic works there because those windows
    contain one table; an order editor contains several boxes of similar size,
    and the moment the item table grew past the Remarks box below it the
    heuristic silently picked Remarks instead.

    The consequence was the worst kind: reading the wrong pane returns *empty*
    rather than obviously wrong, so a chooser that had correctly added a line
    looked like it had done nothing - and the retry that followed added the
    same product again. Three chairs and two mats on a two-line order, with
    every step reporting failure while it worked.
    """
    labels = [t for t in find_all(editor, lambda c: c.ControlTypeName == "TextControl")
              if t.Name == "Items"]
    if len(labels) != 1:
        raise UIError(f"expected one 'Items' label in the editor, found {len(labels)}")
    top = labels[0].BoundingRectangle.top

    candidates = []
    for p in find_all(editor, lambda c: c.ControlTypeName == "PaneControl", 16):
        r = p.BoundingRectangle
        # Starts level with the label, and spans the editor: the table body.
        if top - 12 <= r.top <= top + 48 and r.width() > 800 and r.height() > 60:
            candidates.append(p)
    if not candidates:
        raise UIError("no Items table beside the 'Items' label")
    # Innermost of the nested panes is the table itself rather than its frame.
    return min(candidates, key=lambda p: p.BoundingRectangle.width() * p.BoundingRectangle.height())


def item_rows(editor) -> GridRead:
    """Every row of the order's Items grid.

    Its own function because the grid is editable and so selects by *cell*:
    the data-cell click that reads the chooser returns a single value here.
    Clicking the row header first makes the selection row-shaped, and only
    then does Ctrl+A cover the table.
    """
    try:
        pane = items_grid(editor)
    except UIError as exc:
        log.warning("could not locate the Items table: %s", exc)
        return GridRead([], "no-pane")
    return grid_read(editor, dx=config.GRID_ROW_HEADER_DX, select_all=True, pane=pane)


@dataclass(frozen=True)
class GridRead:
    """Rows, and how confident we are that there were no more.

    The distinction is not pedantry. An empty *filter result* means "create
    it"; an empty *read* means "we could not see". Collapsing the two let a
    VATs list that failed to take focus report as having no rates at all, one
    step away from creating a duplicate of a rate that was sitting right
    there. Callers that are searching may treat both as absence; callers
    reading a whole list must not.
    """

    rows: list[list[str]]
    how: str          # 'read' | 'no-pane' | 'no-focus'

    @property
    def trustworthy(self) -> bool:
        return self.how == "read"


def grid_read(dialog, *, dx: int = None, select_all: bool = True, pane=None) -> GridRead:
    """Read a grid, reporting whether the read itself can be trusted.

    `dx` is how far into the grid to click: a data cell by default, or the row
    header for a cell-selecting grid - see config.GRID_ROW_HEADER_DX.

    `pane` names the table explicitly. Callers that can identify their grid by
    something better than size should: see items_grid.
    """
    dx = config.GRID_DATA_DX if dx is None else dx
    if pane is None:
        try:
            pane = grid_pane(dialog)
        except UIError:
            # When a filter matches nothing the grid body collapses, and there
            # is no pane left to click. That is an empty result, not a failure.
            return GridRead([], "no-pane")
    r = pane.BoundingRectangle
    auto.SetClipboardText("")
    # An inline cell editor left open from an earlier click swallows the
    # keystrokes below. Escape cancels it without committing anything.
    auto.SendKeys("{Esc}", waitTime=0.1)
    # One click only. Reading and then selecting used to click the same row
    # twice in quick succession, which the chooser took as a double-click -
    # accepting the row and adding the wrong product to the order.
    if not _claim_grid(dialog, r.left + dx, r.top + config.GRID_FIRST_ROW_DY):
        log.info("no row took focus in %r - nothing selectable there", dialog.Name)
        return GridRead([], "no-focus")
    if select_all:
        auto.SendKeys("{Ctrl}a", waitTime=0.2)
    text = _copy_selection(dialog)
    return GridRead([line.split("\t") for line in text.splitlines() if line.strip()], "read")


def _copy_selection(dialog) -> str:
    """Ctrl+C the grid, and clear the error box if there was nothing to copy.

    Copying an empty NatTable is not a no-op - it throws, every time:

        java.lang.NullPointerException: Cannot read the array length because
        "this.copiedCells" is null

    and Eclipse queues an 'Internal Error' box for it. The box then suppresses
    every tooltip in the application, so the *next* item's icon cannot be
    confirmed and the run stops on a step that was never at fault. That is the
    loop this closes: a search matching nothing is an ordinary answer, so its
    error box is cleaned up here and the empty result returned plainly.
    """
    auto.SendKeys("{Ctrl}c", waitTime=0.3)
    time.sleep(config.SETTLE)
    text = auto.GetClipboardText() or ""
    if not text.strip():
        for report in dismiss_error_dialogs(dialog_owner(dialog)):
            log.debug("cleared after copying an empty grid: %s", report)
    return text


def dialog_owner(ctrl):
    """The Fakturama shell, whether `ctrl` is the shell, a view or a dialog."""
    try:
        return window()
    except UIError:
        return ctrl


def grid_select_row(dialog, index: int) -> list[str]:
    """Click the row at `index` and return what the grid says is selected.

    Selecting still needs a coordinate - the rows are painted, not published -
    but the click is immediately proved by copying the selected row back, so a
    mis-aimed click is caught rather than committed.

    Unlike grid_rows, a missed click here does raise. The caller has already
    read this row and is asking for it by index, so "nothing took focus" is a
    contradiction, not an empty list.
    """
    pane = grid_pane(dialog)
    r = pane.BoundingRectangle
    y = r.top + config.GRID_FIRST_ROW_DY + index * config.GRID_ROW_HEIGHT
    auto.SetClipboardText("")
    if not _claim_grid(dialog, r.left + 80, y):
        raise UIError(
            f"row {index} of {dialog.Name!r} did not take focus; refusing to send "
            "Ctrl+C, which would copy whatever else is focused"
        )
    text = _copy_selection(dialog)
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


def owned_dialogs(win) -> list:
    """Fakturama's own Win32 dialogs, wherever Windows parented them.

    Both places have to be searched. Eclipse's 'Internal Error' box is a
    sibling of the shell at the desktop root, while the web shop's error box
    is a descendant of it - and looking in only one of them reported "no
    popups" with a dialog plainly on screen. Ownership is decided by process
    id: matching on "not the shell" would sweep in the terminal this
    automation runs from and every other app on the desktop.
    """
    try:
        pid = win.ProcessId
    except Exception:
        return []

    def usable(w) -> bool:
        try:
            if w.ClassName != config.DIALOG_CLASS:
                return False
            r = w.BoundingRectangle
            if r.width() <= 0 or r.height() <= 0:
                return False      # a disposed dialog still lingers in the tree
            return w.ProcessId == pid
        except Exception:
            return False

    out = [w for w in auto.GetRootControl().GetChildren()
           if w.ControlTypeName == "WindowControl" and usable(w)]
    seen = {_runtime_id(w) for w in out}
    for w in find_all(win, lambda c: c.ControlTypeName == "WindowControl", 6):
        if usable(w) and _runtime_id(w) not in seen:
            out.append(w)
    return out


def dialog_buttons(dlg) -> set[str]:
    """The buttons a dialog offers, minus its title bar and any expander."""
    names = {b.Name for b in find_all(dlg, lambda c: c.ControlTypeName == "ButtonControl", 6)
             if b.Name}
    return names - config.TITLE_BAR_BUTTONS - config.EXPANDER_BUTTONS


def reports_only(dlg) -> bool:
    """Does this dialog just tell you something, rather than ask?

    The distinction that matters for closing one unattended. A report offers a
    single way out - OK - so pressing it changes nothing that was not already
    true. A question offers alternatives, and picking one decides the fate of
    real work: 'Save changes?' with Yes/No/Cancel, or the product chooser with
    OK/Cancel. Those stay open.
    """
    content = dialog_buttons(dlg)
    return bool(content) and content <= config.ACKNOWLEDGE_BUTTONS


def blocking_popups(win) -> list[str]:
    """Windows that will suppress tooltips while they are open.

    An inactive window does not render tooltips, so any open dialog - a
    chooser left behind, an 'Internal Error' box - silently disables the whole
    tooltip layer. Every guarded control then drops to a structural match, and
    the guarded ones refuse to act at all. Diagnosed the hard way: a stray
    error dialog made the item-delete icon unresolvable, and the positional
    fallback clicked *paste* instead.

    An earlier version asked GetForegroundControl() whether a dialog held the
    screen. It returns None often enough to matter, and when it did, this
    reported "no popups" with an 'Internal Error' box plainly visible. Presence
    is the question, not activation - so presence is what gets measured.
    """
    names = [w.Name for w in find_all(win, lambda c: c.ControlTypeName == "WindowControl", 6)
             if w.Name]
    names += [w.Name or "(untitled dialog)" for w in owned_dialogs(win)]
    return names


def _dialog_message(dlg) -> str:
    """The text an error box is showing, for the log."""
    lines = [t.Name for t in find_all(dlg, lambda c: c.ControlTypeName == "TextControl", 6)
             if t.Name and t.Name.strip()]
    return " | ".join(lines[:3])


def dismiss_error_dialogs(win) -> list[str]:
    """Close the report-only dialogs in the way, and say what was closed.

    They are not incidental: while one is open every tooltip in the
    application is suppressed, which disables the resolver's semantic layer,
    which makes guarded icons refuse to act. Clearing them is a precondition
    for the run, not tidying up.

    Only boxes that report. Anything offering a choice is left exactly where
    it is - see reports_only.
    """
    closed = []
    # A loop, not a pass: Eclipse queues these and shows one at a time, so
    # closing the visible box brings up the next - each cascaded down and right
    # from the last. A single sweep clears one and reports success while the
    # tooltip layer is still suppressed by its successor.
    for _ in range(config.ERROR_DIALOG_SWEEPS):
        pending = [d for d in owned_dialogs(win) if reports_only(d)]
        if not pending:
            break
        for dlg in pending:
            title = (dlg.Name or "").strip() or "(untitled dialog)"
            message = _dialog_message(dlg)
            if not _press_dismiss(dlg, title):
                return closed          # stuck; say so rather than spin
            closed.append(f"{title}: {message}" if message else title)
            log.warning("dismissed %r - %s", title, message)

    for dlg in owned_dialogs(win):
        log.info("leaving the %r dialog alone - it asks something (%s)",
                 dlg.Name, sorted(dialog_buttons(dlg)))
    return closed


def _press_dismiss(dlg, title: str) -> bool:
    """Press the dialog's acknowledge button and confirm it went away.

    Three routes, because a Win32 dialog button is not an SWT one: Invoke is
    the clean path, a real click is what actually works when the dialog does
    not own the foreground, and Escape is the last resort.
    """
    buttons = {b.Name: b for b in
               find_all(dlg, lambda c: c.ControlTypeName == "ButtonControl", 6) if b.Name}
    button = next((buttons[n] for n in config.ERROR_DISMISS_BUTTONS if n in buttons), None)
    if button is None:
        log.warning("%r offers no acknowledge button (%s)", title, sorted(buttons))
        return False

    r = button.BoundingRectangle
    for route in ("invoke", "click", "escape"):
        try:
            if route == "invoke":
                button.GetInvokePattern().Invoke()
            elif route == "click":
                dlg.SetActive()
                time.sleep(0.2)
                auto.Click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
            else:
                dlg.SetActive()
                auto.SendKeys("{Esc}", waitTime=0.1)
        except Exception as exc:
            log.debug("%s route failed on %r: %s", route, title, exc)
            continue
        time.sleep(config.SETTLE)
        if not _still_open(dlg):
            return True
    log.warning("could not close the %r dialog; tooltips will stay suppressed", title)
    return False


def _still_open(dlg) -> bool:
    try:
        r = dlg.BoundingRectangle
        return r.width() > 0 and r.height() > 0
    except Exception:
        return False


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
