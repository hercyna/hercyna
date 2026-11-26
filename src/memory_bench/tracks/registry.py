"""Pluggable Track registry (memory-bench v2)."""

from __future__ import annotations

from .account_review import AccountReviewTrack
from .base import TrackSpec
from .chat_agent import ChatAgentTrack

_TRACKS: dict[str, TrackSpec] = {}


def register_track(track: TrackSpec) -> TrackSpec:
    _TRACKS[track.meta.name] = track
    return track


def get_track(name: str) -> TrackSpec:
    if name not in _TRACKS:
        raise KeyError(f"unknown track {name!r}; registered: {', '.join(sorted(_TRACKS))}")
    return _TRACKS[name]


def list_tracks() -> tuple[str, ...]:
    return tuple(sorted(_TRACKS))


register_track(AccountReviewTrack())
register_track(ChatAgentTrack())
