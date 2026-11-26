"""Decider contract for the run engine (CHARTER section 4.2, section 5 rule 9).

A decider reads one DecisionInput and returns one action name or PARSE_ERROR.
Real deciders get only a DecisionInput: the query, the records text and the ids
quoted in it. They never get a Case, so they never see a label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..cases.schema import ACTIONS
from .lifted import PARSE_ERROR

__all__ = ["DecisionInput", "Decider", "PARSE_ERROR", "VALID_OUTPUTS"]

VALID_OUTPUTS = (*ACTIONS, PARSE_ERROR)


@dataclass(frozen=True)
class DecisionInput:
    case_id: str
    query_text: str
    records_text: str  # the exact text the decider reads as its records
    record_ids: list[str] = field(default_factory=list)  # ids quoted verbatim in records_text
    repeat: int = 0  # 0 for the gate call, 1..R for floor and view calls
    track: str = "account_review"  # v2 track name; defaults to v1 account_review


@runtime_checkable
class Decider(Protocol):
    id: str  # decider id as it appears in metric rows, e.g. "mock-oracle"
    model: str
    params: dict

    def decide(self, inp: DecisionInput) -> str: ...
