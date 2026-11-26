"""Memory-adapter contract (docs/run-files.md, section 1).

An adapter sees Record objects and the query string only. `records_of` is the
single bridge from a Case to an adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from ..cases.schema import Case
from .legacy.base import count_tokens, rendered, take_within_budget, truncate_tokens

__all__ = [
    "Record",
    "Usage",
    "Retrieved",
    "MemoryAdapter",
    "records_of",
    "enforce_budget",
    "count_tokens",
]


@dataclass(frozen=True)
class Record:
    id: str  # opaque record id, as it appears at the start of the rendered line
    date: str  # ISO date
    text: str  # the full rendered line, exactly as the full history shows it


@dataclass
class Usage:
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embed_calls: int = 0


@dataclass
class Retrieved:
    text: str  # what the decider will read as its records
    record_ids: list[str] = field(default_factory=list)  # ids quoted verbatim; empty for rewritten memory
    units: list[str] = field(default_factory=list)  # the text units in order (lines or sentences)
    usage: Usage = field(default_factory=Usage)


class MemoryAdapter(Protocol):
    name: str
    product_version: str
    config_label: str
    reference: bool

    def reset(self, namespace: str) -> None: ...

    def ingest(self, records: Sequence[Record]) -> None: ...

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved: ...

    def close(self) -> None: ...


def records_of(case: Case) -> list[Record]:
    """The rendered lines of a case in history order, as Records.

    For v1 `account_review`, preserves the exact space-parsed rendering. For v2 tracks,
    constructs Record(id, date, text) directly from Event fields and track rendering so
    ISO dates never break on arbitrary text formatting.
    """
    if getattr(case, "track", "account_review") == "account_review":
        out: list[Record] = []
        for _eid, line in rendered(case):
            rid, date, _rest = str(line).split(" ", 2)
            out.append(Record(id=str(rid), date=str(date), text=str(line)))
        return out
    from ..tracks import get_track, render_v2_record

    try:
        spec = get_track(case.track)
        render_fn = lambda ev: spec.render_record(case, ev)
    except KeyError:
        render_fn = render_v2_record
    return [Record(id=str(ev.id), date=str(ev.date), text=str(render_fn(ev))) for ev in case.events]


def enforce_budget(retrieved: Retrieved, budget_tokens: int, reference: bool = False) -> Retrieved:
    """Cut `text` to the budget with the shared rule. A reference adapter is returned unchanged.

    When the cut changes the text, units and record_ids keep only the leading
    units that survive whole.
    """
    if reference:
        return retrieved
    text = truncate_tokens(retrieved.text, budget_tokens)
    if text == retrieved.text:
        return retrieved
    units: list[str] = []
    pos = 0
    for u in retrieved.units:
        end = pos + len(u)
        if end > len(text) or text[pos:end] != u:
            break
        units.append(u)
        pos = end + 1  # the joining newline
    ids = retrieved.record_ids[: len(units)] if len(retrieved.record_ids) == len(retrieved.units) else []
    return Retrieved(text=text, record_ids=ids, units=units, usage=retrieved.usage)


class _VerbatimAdapter:
    name = ""
    product_version = "builtin"
    config_label = "default"
    reference = False

    def __init__(self, **config) -> None:
        self.config = dict(config)
        self._namespace: str | None = None
        self._records: list[Record] = []

    def reset(self, namespace: str) -> None:
        self._namespace = namespace
        self._records = []

    def ingest(self, records: Sequence[Record]) -> None:
        self._records.extend(records)

    def close(self) -> None:
        self._namespace = None
        self._records = []

    def _pack(self, ids: list[str]) -> Retrieved:
        """Whole records, in history order."""
        wanted = set(ids)
        chosen = [r for r in self._records if r.id in wanted]
        units = [r.text for r in chosen]
        return Retrieved(text="\n".join(units), record_ids=[r.id for r in chosen], units=units)

    def _walk(self, ranked_idx: list[int], budget_tokens: int) -> Retrieved:
        pairs = ((self._records[i].id, self._records[i].text) for i in ranked_idx)
        return self._pack(take_within_budget(pairs, budget_tokens))
