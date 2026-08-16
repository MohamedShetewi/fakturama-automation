"""resolve_or_create - the tri-state lookup used for Debtor, Product, VAT and
Payment Method (spec 2.x-3.x).

The three outcomes exist because the middle one is a trap. Searching
Fakturama for a customer or an SKU has three meaningfully different results,
and collapsing them to "found / not found" is what makes an automation book
against the wrong record:

    exactly one exact match  -> select it
    no match                 -> create it
    more than one, or a
    match that conflicts     -> HALT for manual review

Note "exact". A substring hit is not a match: Fakturama's product search on
'CHR-ERG-01' will happily return 'CHR-ERG-011'. Selecting that is a silent
wrong booking, which is the same failure mode the reconciliation gate exists
to prevent one stage earlier.

Everything here is pure - candidates in, decision out - so the whole decision
table is unit-tested without Fakturama running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence


class Outcome(str, Enum):
    SELECTED = "selected"
    CREATED = "created"
    HALT = "halt"


class Verdict(str, Enum):
    """What the candidate list means, before acting on it."""

    UNIQUE = "unique"
    NONE = "none"
    AMBIGUOUS = "ambiguous"
    CONFLICT = "conflict"


def normalize(text: str) -> str:
    """Fold the differences that are never meaningful in a name or an SKU."""
    return " ".join((text or "").split()).strip().casefold()


def classify(
    candidates: Sequence,
    wanted: str,
    *,
    key: Callable[[object], str] = str,
    conflicts: Callable[[object], str | None] = None,
) -> tuple[Verdict, list]:
    """Reduce a search result to a verdict.

    `conflicts` may inspect the single exact match and return a reason it must
    not be used silently - e.g. a debtor whose stored address differs from the
    document's. That is a HALT, not a select: the record exists, but acting on
    it would overwrite or contradict real data.
    """
    want = normalize(wanted)
    exact = [c for c in candidates if normalize(key(c)) == want]

    if not exact:
        return Verdict.NONE, []
    if len(exact) > 1:
        return Verdict.AMBIGUOUS, exact
    if conflicts is not None:
        reason = conflicts(exact[0])
        if reason:
            return Verdict.CONFLICT, exact
    return Verdict.UNIQUE, exact


@dataclass
class Resolution:
    entity: str
    wanted: str
    outcome: Outcome
    verdict: Verdict
    matches: list = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is not Outcome.HALT

    def __str__(self) -> str:
        return f"{self.entity} {self.wanted!r}: {self.outcome.value} ({self.verdict.value}){' - ' + self.detail if self.detail else ''}"


def resolve_or_create(
    entity: str,
    wanted: str,
    candidates: Sequence,
    *,
    select: Callable[[object], None],
    create: Callable[[str], None] = None,
    key: Callable[[object], str] = str,
    conflicts: Callable[[object], str | None] = None,
    allow_create: bool = True,
) -> Resolution:
    """Select, create, or halt - and never guess.

    `select` and `create` are the UI actions; injecting them keeps this
    decision testable and lets the same logic drive four different screens.
    """
    verdict, matches = classify(candidates, wanted, key=key, conflicts=conflicts)

    if verdict is Verdict.UNIQUE:
        select(matches[0])
        return Resolution(entity, wanted, Outcome.SELECTED, verdict, matches,
                          f"matched {key(matches[0])!r}")

    if verdict is Verdict.NONE:
        if not allow_create or create is None:
            return Resolution(entity, wanted, Outcome.HALT, verdict, [],
                              "no match and creation is not permitted here")
        create(wanted)
        return Resolution(entity, wanted, Outcome.CREATED, verdict, [], "created")

    if verdict is Verdict.AMBIGUOUS:
        names = [key(m) for m in matches]
        return Resolution(entity, wanted, Outcome.HALT, verdict, matches,
                          f"{len(matches)} exact matches ({names}); refusing to choose")

    reason = conflicts(matches[0]) if conflicts else "conflict"
    return Resolution(entity, wanted, Outcome.HALT, verdict, matches,
                      f"single match {key(matches[0])!r} conflicts: {reason}")
