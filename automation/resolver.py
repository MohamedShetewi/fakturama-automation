"""find_control: turn a catalog key into a live control, via a fallback chain.

    layer 1  UIA property   ControlType + Name
    layer 2  tooltip        ControlType + the text SWT shows on hover
    layer 3  tree-relative  nearest control of that type to a labeled neighbor

Layers 1 and 2 are semantic: they key on what a control *is*. Layer 3 is
structural, and a row that reaches it is a hint that the catalog is drifting.

There is deliberately no pixel-offset layer. An earlier draft had one, and its
coordinates went stale inside a single session when the layout reflowed - the
address icons moved 150px sideways - which is exactly why absolute or
anchor-relative coordinates cannot be trusted as a locator.

Each attempt logs why it succeeded or was skipped, because "which layer did
that come from" is the first question when a step types into the wrong field.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import ui
from .selectors import Layer, Screen, Target, target as catalog_target
from .ui import UIError

log = logging.getLogger("automation.resolver")


@dataclass(frozen=True)
class Resolved:
    """A located control, and the provenance of how it was found."""

    control: object
    target: Target
    layer: Layer
    detail: str = ""

    @property
    def key(self) -> str:
        return self.target.key

    def __str__(self) -> str:
        return f"{self.key} via layer {int(self.layer)} ({self.layer.name}){' ' + self.detail if self.detail else ''}"


class Scope:
    """Where a target lives, plus a tooltip cache.

    Reading a tooltip costs a mouse hover and up to a few seconds, and the
    same control gets resolved repeatedly across a run, so results are cached
    per rectangle for the life of the scope.
    """

    def __init__(self, win, editor=None):
        self.win = win
        self.editor = editor
        self._tips: dict[tuple, str | None] = {}

    def tooltip(self, ctrl) -> str | None:
        r = ctrl.BoundingRectangle
        key = (r.left, r.top, r.right, r.bottom)
        if key not in self._tips:
            self._tips[key] = ui.tooltip_of(ctrl)
        return self._tips[key]

    def root_for(self, t: Target):
        if t.screen is Screen.MAIN:
            return self.win
        if self.editor is None:
            raise UIError(f"{t.key}: needs the order editor, which is not open")
        return self.editor


# --- the layers --------------------------------------------------------------


def _layer1(root, t: Target):
    """ControlType + exact Name."""
    if not t.name:
        return None, "no name in catalog"
    hits = ui.find_all(root, lambda c: c.ControlTypeName == t.control_type and c.Name == t.name)
    if not hits:
        return None, f"no {t.control_type} named {t.name!r}"
    if len(hits) > 1:
        # Do not silently take [0]; fall through and let a positional layer
        # disambiguate. Picking arbitrarily is how a value lands in the
        # wrong field while every log line still says "found".
        return None, f"{len(hits)} controls named {t.name!r} - ambiguous"
    return hits[0], f"unique {t.control_type} named {t.name!r}"


def _anchor_rect(root, t: Target):
    labels = ui.find_all(
        root, lambda c: c.ControlTypeName == "TextControl" and c.Name == t.anchor
    )
    if not labels:
        return None, f"no label {t.anchor!r}"
    if len(labels) > 1:
        return None, f"{len(labels)} labels named {t.anchor!r} - ambiguous anchor"
    return labels[0].BoundingRectangle, ""


def _layer_tooltip(root, t: Target, scope: "Scope"):
    """ControlType + the tooltip SWT shows on hover.

    The only semantic handle on Fakturama's unlabeled icons: 'Pick an address
    from the list of all contacts' versus 'Open the contact editor to enter a
    new address' are two icons that position alone cannot safely tell apart,
    and picking the wrong one starts a new debtor instead of selecting one.
    """
    if not t.tooltip:
        return None, "no tooltip in catalog"
    want = ui.normalize_tip(t.tooltip)
    candidates = ui.find_all(root, lambda c: c.ControlTypeName == t.control_type)
    if not candidates:
        return None, f"no {t.control_type} in scope"

    # Position proposes, the tooltip disposes. Each hover costs a mouse move
    # and up to a few seconds, so try the structurally-likely control first -
    # but the tooltip still has to match, so a wrong guess only costs time and
    # can never select the wrong control.
    candidates = _ordered_by_likelihood(root, t, candidates)

    seen = []
    for c in candidates:
        tip = scope.tooltip(c)
        if tip is None:
            continue
        seen.append(tip)
        if ui.normalize_tip(tip) == want:
            return c, f"tooltip {tip!r}"
    return None, (
        f"no {t.control_type} with tooltip {t.tooltip!r} "
        f"(saw {len(seen)} tooltips among {len(candidates)} candidates)"
    )


def _ordered_by_likelihood(root, t: Target, candidates: list) -> list:
    """Sort candidates so the structurally-plausible one is hovered first.

    Purely an ordering hint: it never decides which control is used.
    """
    if not t.anchor:
        return candidates
    rect, _ = _anchor_rect(root, t)
    if rect is None:
        return candidates

    def distance(c):
        r = c.BoundingRectangle
        if t.anchor_side == "right":
            return (r.left - rect.right) if r.left >= rect.right - 2 else 10_000
        return (r.top - rect.bottom) if r.top >= rect.bottom - 2 else 10_000

    return sorted(candidates, key=distance)


def _layer_tree(root, t: Target):
    """Nearest control of the right type, right of / below the label."""
    if not t.anchor:
        return None, "no anchor in catalog"
    rect, why = _anchor_rect(root, t)
    if rect is None:
        return None, why

    mid_y = (rect.top + rect.bottom) / 2
    mid_x = (rect.left + rect.right) / 2
    candidates = []
    for c in ui.find_all(root, lambda c: c.ControlTypeName == t.control_type):
        r = c.BoundingRectangle
        if t.anchor_side == "right":
            if r.left < rect.right - 2:
                continue
            if not (r.top - 10 <= mid_y <= r.bottom + 10):
                continue
            candidates.append((r.left - rect.right, c))
        else:  # below
            if r.top < rect.bottom - 2:
                continue
            if not (r.left - 40 <= mid_x <= r.right + 40):
                continue
            candidates.append((r.top - rect.bottom, c))

    if not candidates:
        return None, f"no {t.control_type} {t.anchor_side} of {t.anchor!r}"
    candidates.sort(key=lambda p: p[0])
    if t.occurrence >= len(candidates):
        return None, (
            f"wanted occurrence {t.occurrence} {t.anchor_side} of {t.anchor!r}, "
            f"found only {len(candidates)}"
        )
    dist, ctrl = candidates[t.occurrence]
    return ctrl, f"occurrence {t.occurrence} {t.anchor_side} of {t.anchor!r} (+{dist}px)"


_LAYERS = {
    Layer.UIA_PROPERTY: lambda root, t, scope: _layer1(root, t),
    Layer.TOOLTIP: _layer_tooltip,
    Layer.TREE_RELATIVE: lambda root, t, scope: _layer_tree(root, t),
}


# --- the resolver ------------------------------------------------------------


def find_control(key: str | Target, scope: Scope, *, required: bool = True) -> Resolved | None:
    """Resolve a catalog key through the fallback chain.

    Returns None (rather than raising) when required=False, so callers can ask
    "is the Total Net field present?" as a state probe.
    """
    t = key if isinstance(key, Target) else catalog_target(key)
    root = scope.root_for(t)

    if not t.layers:
        raise UIError(f"catalog row {t.key!r} declares no usable layer")

    attempts = []
    for layer in t.layers:
        try:
            ctrl, why = _LAYERS[layer](root, t, scope)
        except Exception as exc:
            ctrl, why = None, f"{type(exc).__name__}: {exc}"
        if ctrl is not None:
            log.info("%s: layer %d (%s) - %s", t.key, int(layer), layer.name, why)
            if layer is Layer.TREE_RELATIVE:
                # Structural, not semantic: it worked, but both layers that key
                # on what the control *is* failed, which usually means the
                # catalog's Name or tooltip has drifted from the UI.
                log.warning(
                    "%s resolved structurally (layer 3) - check its Name/tooltip row", t.key
                )
            return Resolved(control=ctrl, target=t, layer=layer, detail=why)
        attempts.append(f"layer {int(layer)} ({layer.name}): {why}")
        log.debug("%s: layer %d failed - %s", t.key, int(layer), why)

    if not required:
        return None
    raise UIError(f"could not resolve {t.key!r}; tried " + "; ".join(attempts))


def present(key: str, scope: Scope) -> bool:
    """State probe: does this control exist right now?"""
    try:
        return find_control(key, scope, required=False) is not None
    except UIError:
        return False
