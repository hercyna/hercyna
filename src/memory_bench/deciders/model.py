"""Model deciders from config files (docs/deciders.md sections 1 and 2).

    gw = Gateway(cache_path, cap_usd, run_dir)
    d = load_decider("configs/deciders/haiku-5-5.json", gw)
    action = d.decide(DecisionInput(...), context={"kind": "gate", "system": "full"})
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from ..gateway.meter import Gateway
from .base import PARSE_ERROR, Decider, DecisionInput
from .lifted import PROMPT_VERSIONS, parse_reply
from .prompts import build_prompt, with_reminder
from .transports import TRANSPORTS, Opener, Runner, Transport, make_transport

__all__ = ["ModelDecider", "load_decider", "load_config", "decider_spec", "CONFIG_KEYS"]

CONFIG_KEYS = frozenset({"id", "transport", "model", "prompt_version", "params", "base_url", "command", "key",
                         "price", "timeout_s", "retries", "assumed_output_tokens", "description",
                         "env_unset"})
PARAM_KEYS = frozenset({"max_tokens", "temperature", "effort", "thinking", "num_ctx"})


def load_config(path_or_dict: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Read and check a decider config. Raises ValueError naming the bad key (never echoing a key value)."""
    if isinstance(path_or_dict, dict):
        cfg = copy.deepcopy(path_or_dict)
    else:
        cfg = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("decider config must be a JSON object")
    unknown = sorted(k for k in cfg if k not in CONFIG_KEYS and not k.startswith("_"))
    if unknown:
        raise ValueError(f"decider config has unknown keys: {unknown}")
    for k in ("id", "transport", "model"):
        if not isinstance(cfg.get(k), str) or not cfg[k]:
            raise ValueError(f"decider config needs a string {k!r}")
    if cfg["transport"] not in TRANSPORTS:
        raise ValueError(f"unknown transport {cfg['transport']!r}; known: {', '.join(TRANSPORTS)}")
    cfg.setdefault("prompt_version", "v1")
    if cfg["prompt_version"] not in PROMPT_VERSIONS:
        raise ValueError(f"prompt_version must be one of {PROMPT_VERSIONS}")
    cfg["params"] = dict(cfg.get("params") or {})
    bad = sorted(set(cfg["params"]) - PARAM_KEYS)
    if bad:
        raise ValueError(f"decider params has unknown keys: {bad}")
    if "price" not in cfg:
        raise ValueError("decider config needs 'price' (an object, or null when unknown)")
    p = cfg["price"]
    if p is not None and not (isinstance(p, dict) and all(isinstance(p.get(k), (int, float))
                                                          for k in ("input_per_m", "output_per_m"))):
        raise ValueError("price must be null or have numeric input_per_m and output_per_m")
    key = cfg.get("key")
    if key is not None:
        ok = isinstance(key, dict) and len(key) == 1 and (
            (isinstance(key.get("env"), str) and key["env"])
            or (isinstance(key.get("command"), list) and key["command"]
                and all(isinstance(a, str) for a in key["command"])))
        if not ok:  # the value is not repeated: it may be a pasted key
            raise ValueError('key must be {"env": "NAME"} or {"command": [argv]} or absent')
        if cfg["transport"] != "openai_compat":
            raise ValueError("only the openai_compat transport takes a key")
    if cfg["transport"] == "anthropic_command":
        cfg.setdefault("command", ["anthropic-api"])
        if not (isinstance(cfg["command"], list) and cfg["command"] and all(isinstance(a, str) for a in cfg["command"])):
            raise ValueError("command must be a non-empty list of strings")
    if "env_unset" in cfg:
        eu = cfg["env_unset"]
        if not (isinstance(eu, list) and all(isinstance(n, str) and n for n in eu)):
            raise ValueError("env_unset must be a list of environment variable names")
        if cfg["transport"] != "anthropic_command":
            raise ValueError("only the anthropic_command transport takes env_unset")
    if cfg["transport"] == "openai_compat" and not cfg.get("base_url"):
        raise ValueError("openai_compat needs base_url")
    return cfg


class ModelDecider:
    """A Decider backed by a model: prompt v1, the gateway, parse_reply, one reminder retry."""

    accepts_context = True

    def __init__(self, cfg: dict[str, Any], transport: Transport, gateway: Gateway):
        self.cfg = cfg
        self.id: str = cfg["id"]
        self.model: str = cfg["model"]
        self.params: dict = dict(cfg["params"])
        self.prompt_version: str = cfg["prompt_version"]
        self.transport = transport
        self.gateway = gateway
        self.last_raw = ""
        self.last_parsed: dict | None = None

    def decide(self, inp: DecisionInput, context: dict[str, Any] | None = None) -> str:
        ctx = {**(context or {}), "case_id": inp.case_id}
        messages = build_prompt(inp, self.prompt_version)
        reply = self.gateway.call(self.cfg, self.transport, messages, repeat=inp.repeat,
                                  context={**ctx, "reminder": False})
        parsed = parse_reply(reply.text)
        if parsed is None:
            reply = self.gateway.call(self.cfg, self.transport, with_reminder(messages), repeat=inp.repeat,
                                      context={**ctx, "reminder": True})
            parsed = parse_reply(reply.text)
        self.last_raw, self.last_parsed = reply.text, parsed
        return parsed["action"] if parsed else PARSE_ERROR


def load_decider(path_or_dict: str | Path | dict[str, Any], gateway: Gateway, *, runner: Runner | None = None,
                 opener: Opener | None = None, sleep: Callable[[float], None] | None = None,
                 environ: dict[str, str] | None = None) -> ModelDecider:
    """A ModelDecider from a config file or dict. runner, opener, sleep and environ are test seams."""
    cfg = load_config(path_or_dict)
    gateway.check_decider(cfg)
    transport = make_transport(cfg, runner=runner, opener=opener, sleep=sleep, environ=environ)
    return ModelDecider(cfg, transport, gateway)


def decider_spec(decider: Decider) -> dict[str, Any]:
    """The manifest block {id, model, params}; model deciders add transport, prompt_version, price and the key
    source kind. Never key material: a key is only ever named by its source ("env" or "command")."""
    spec: dict[str, Any] = {"id": decider.id, "model": decider.model, "params": dict(decider.params)}
    cfg = getattr(decider, "cfg", None)
    if isinstance(cfg, dict):
        spec["transport"] = cfg["transport"]
        spec["prompt_version"] = cfg["prompt_version"]
        spec["price"] = copy.deepcopy(cfg.get("price"))
        spec["key_source"] = next(iter(cfg["key"])) if cfg.get("key") else None
    return spec
