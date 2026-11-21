"""Shared fakes for model-decider tests: deterministic replies, no network, no real command."""

from __future__ import annotations

import hashlib
import json

from memory_bench.cases.schema import ACTIONS
from memory_bench.deciders.lifted import REMINDER
from memory_bench.deciders.transports import RawReply

HAIKU_CFG = {"id": "haiku-5-5", "transport": "anthropic_command", "model": "claude-haiku-5-5", "prompt_version": "v1",
             "command": ["fake-wrapper"], "params": {"max_tokens": 6000, "effort": "high"},
             "price": {"input_per_m": 0.10, "output_per_m": 0.50, "as_of": "2026-10-16", "source": "test"},
             "timeout_s": 300, "retries": 2}


def reply(action: str) -> str:
    return json.dumps({"action": action, "reason": "r.", "event_ids": []})


def action_for(text: str) -> str:
    """A deterministic action from the request text; roughly one request in seven gets a bad first reply."""
    h = hashlib.sha256(text.encode()).digest()
    if h[1] % 7 == 0 and REMINDER not in text:
        return "BAD"
    return ACTIONS[h[0] % len(ACTIONS)]


def anthropic_runner(log: list | None = None):
    """A fake `anthropic-api`: answers from the body, reports usage from its length."""

    def run(argv, stdin, timeout):
        if log is not None:
            log.append((list(argv), stdin))
        body = json.loads(stdin)
        user = body["messages"][0]["content"]
        a = action_for(user)
        text = "I think it is credit" if a == "BAD" else reply(a)
        out = {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
               "usage": {"input_tokens": len(user) // 2, "output_tokens": 150}}
        return 0, json.dumps(out), ""

    return run


class ScriptedTransport:
    """send() returns the scripted replies in order (the last one repeats)."""

    def __init__(self, texts, usage=(100, 20), estimated=False):
        self.texts = list(texts)
        self.usage = usage
        self.estimated = estimated
        self.sent: list[tuple[list, int, bool]] = []

    def send(self, messages, *, repeat, retry_reminder=False):
        self.sent.append((messages, repeat, retry_reminder))
        t = self.texts[min(len(self.sent) - 1, len(self.texts) - 1)]
        return RawReply(t, self.usage[0], self.usage[1], self.estimated)
