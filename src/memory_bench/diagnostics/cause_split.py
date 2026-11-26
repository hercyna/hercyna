"""A2 Diagnostic: offline post-ingest store dump and 4-way cause split for wrong & withheld actions.

Strictly $0 and offline:
- Replays recorded Phase 1 proxy responses from `results/v2/cache/proxy_<product>.sqlite` and
  `memory_calls.jsonl` through an `OfflineReplayProxy` that NEVER opens an outbound socket,
  NEVER reads an API key, and refuses any cache miss with HTTP 400.
- Dumps each memory product's store after ingest through its public API:
    * Mem0:     `memory.get_all(filters={"user_id": ns}, limit=1000)`
    * LangMem:  `store.search(ns, limit=1000)` (no `query=`, zero embedding calls)
    * Graphiti: `EntityEdge.get_by_group_ids(driver, [group])` (zero embedding calls)
- Kept strictly outside the blind adapters (`src/memory_bench/adapters/`) and does not touch
  any file frozen in `protocol/v2.json` Steps 1-9.
- Assigns one mutually exclusive cause to every gated wrong action and withheld action:
    * `not_stored`:          deciding fact is absent from the dumped post-ingest store;
    * `stored_not_returned`: deciding fact is in the dumped store, absent from the budget-cut view;
    * `returned_not_used`:   deciding fact is in the budget-cut view (without a conflicting pair);
    * `conflict_returned`:   both old and new (or both opposing HU2) values appear in the view.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..adapters.base import Retrieved, enforce_budget, records_of
from ..cases.schema import Case, REQUEST_INFO, read_jsonl
from ..gateway.proxy import Scope, load_memory_side, request_key
from ..scoring.acbi import exact_id_matcher
from ..scoring.bootstrap import Boot
from ..scoring.gates import is_committed
from ..scoring.metrics import cell_name

CAUSE_SCHEMA = "memory_bench.cause_split/1"
CAUSE_KEYS = (
    "not_stored",
    "stored_not_returned",
    "returned_not_used",
    "conflict_returned",
    "by_rule",
)
LOW_FIDELITY_THRESHOLD = 0.90

# Canonical semantic signatures derived directly from tracks/chat_agent/generator.py _SLOT_SPECS.
PREF_PATTERNS: dict[str, tuple[str, ...]] = {
    "pdf_attachment": (r"\bpdf\b",),
    "markdown_inline": (r"\bmarkdown\b",),
    "csv_digest": (r"\bcsv\b",),
    "rail_standard": (r"\brail\b", r"standard[- ]class"),
    "economy_nonstop": (r"nonstop\s+economy", r"economy\s+flights?", r"nonstop\s+flights?"),
    "flexible_refundable": (r"\brefundable\b", r"flexible\s+fares?"),
    "sftp_encrypted": (r"\bsftp\b",),
    "local_zip": (r"\bzip\b",),
    "ledger_json": (r"\bjson\b.*ledger", r"ledger.*\bjson\b", r"structured\s+json"),
    "morning_utc": (r"09:00", r"11:30", r"morning\s+slots?"),
    "afternoon_utc": (r"14:00", r"16:30", r"afternoon\s+slots?"),
    "async_brief": (r"asynchronous", r"\basync\b.*brief", r"written\s+brief"),
    "anonymized_parquet": (r"\bparquet\b",),
    "aggregated_json": (r"aggregated\s+summary\s+json", r"summary\s+json", r"aggregated.*\bjson\b"),
    "hashed_csv": (r"hashed[- ]key\s+csv", r"hashed.*\bcsv\b", r"\bcsv\s+tables?\b"),
    "procurement_ticket": (r"procurement\s+ticket",),
    "direct_card_under_250": (r"workspace\s+card", r"direct.*card", r"\b250\s*usd\b", r"\$250"),
    "vendor_po_batch": (r"purchase[- ]order\s+batch", r"vendor\s+purchase[- ]order", r"\bpo\s+batch\b"),
}

PROHIBITION_PATTERNS: dict[str, tuple[str, ...]] = {
    "report_format": (
        r"outside\s+the\s+internal\s+workspace",
        r"never\s+send.*outside",
        r"internal\s+workspace\s+channel\s+only",
        r"prohibit.*external",
    ),
    "travel_policy": (
        r"freeze\s+override",
        r"never\s+finalize",
        r"never\s+charge.*travel",
        r"written\s+freeze",
    ),
    "invoice_export": (
        r"audit\s+lock",
        r"quarterly\s+audit",
        r"never\s+export\s+raw",
    ),
    "meeting_window": (
        r"\bfridays?\b.*(?:never|no|prohibit|block|forbid|not)",
        r"(?:never|no|prohibit|block|forbid|not).*\bfridays?\b",
        r"external\s+sync.*friday",
    ),
    "dataset_export": (
        r"privacy\s+sign[- ]off",
        r"schema\s+privacy",
        r"never\s+export\s+raw",
    ),
    "purchase_approval": (
        r"budget\s+freeze",
        r"never\s+submit.*purchase",
        r"never\s+approve.*purchase",
    ),
}

CONFLICT_RULE_PATTERNS: dict[str, tuple[str, ...]] = {
    "compact_bullets": (r"five\s+bullet", r"5\s+bullet", r"without\s+tables?", r"no\s+tables?"),
    "full_metric_table": (r"multi[- ]column\s+kpi", r"complete.*kpi\s+table", r"kpi\s+table"),
    "monday_morning": (r"08:00", r"monday"),
    "friday_close": (r"17:00", r"friday"),
    "cheapest_carrier": (r"lowest[- ]cost\s+carrier", r"cheapest\s+carrier", r"regardless\s+of\s+connection"),
    "nonstop_only": (r"nonstop\s+itinerar", r"reject\s+multi[- ]leg", r"nonstop.*only"),
    "downtown_walkable": (r"walking\s+distance", r"walkable"),
    "strict_nightly_cap": (r"160\s*usd", r"\$160", r"under\s+160"),
    "single_archive": (r"consolidated\s+monthly", r"one\s+consolidated", r"bundle.*invoice"),
    "per_vendor_files": (r"per[- ]vendor\s+files?", r"separate.*vendor", r"never\s+bundle"),
    "include_line_notes": (r"verbose\s+line[- ]item", r"include.*line[- ]item\s+annotations?"),
    "strip_all_notes": (r"strip\s+all.*notes?", r"remove.*line[- ]item\s+notes?"),
    "mornings_only": (r"before\s+12:00", r"afternoons?\s+blocked", r"mornings?\s+only"),
    "afternoons_only": (r"after\s+14:00", r"mornings?\s+blocked", r"afternoons?\s+only"),
    "thirty_min_hard_cap": (r"25\s*min", r"cap.*25", r"no\s+extensions?"),
    "sixty_min_workshop": (r"60[- ]minute", r"60\s*min", r"working\s+sessions?"),
    "internal_vault_only": (r"internal\s+vault", r"zero\s+external\s+mirror", r"exclusively.*vault"),
    "mirror_partner_bucket": (r"partner\s+bucket", r"mirror.*partner", r"shared\s+partner"),
    "daily_incremental": (r"daily\s+incremental", r"incremental\s+deltas?\s+only"),
    "full_weekly_rebuild": (r"complete\s+weekly\s+snapshots?", r"without\s+incremental", r"weekly\s+rebuild"),
    "annual_prepay": (r"annual\s+prepay", r"annual\s+contracts?"),
    "monthly_only": (r"month[- ]to[- ]month", r"avoid\s+annual"),
    "consolidate_single_po": (r"one\s+quarterly\s+po", r"quarterly\s+po", r"consolidate.*po"),
    "immediate_per_item": (r"individual\s+order", r"immediately\s+as\s+an\s+individual"),
}


class OfflineCacheMissError(RuntimeError):
    """Raised when an offline replay encounters an uncached request; zero paid calls are allowed."""


class OfflineReplayProxy:
    """Strictly offline local HTTP server replaying cached ProductProxy SQLite replies.

    Never reads an API key and never opens an outbound network connection.
    Matches exact `request_key` hash in the read-only SQLite cache first, then falls back strictly
    to the next recorded HTTP-200 `(active_case, route)` reply from the earliest `call_log_paths`
    file that contains that case (so clean `_view_builds` logs take precedence over earlier partial logs).
    """

    def __init__(
        self,
        memory_side_cfg: dict[str, Any],
        cache_path: Path,
        call_log_paths: Sequence[Path],
    ) -> None:
        self.cfg = memory_side_cfg
        self.upstreams = {"chat": memory_side_cfg["chat"], "embeddings": memory_side_cfg["embeddings"]}
        self.lock = threading.Lock()
        self.active_case: str | None = None
        self._consumed: dict[tuple[str, str], set[int]] = {}
        self.exact_hits = 0
        self.seq_hits = 0
        self.misses = 0
        self.miss_details: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

        con = sqlite3.connect(f"file:{Path(cache_path)}?mode=ro", uri=True)
        self._cache_by_key: dict[str, bytes] = {
            str(k): str(rep).encode("utf-8")
            for k, rep in con.execute("SELECT key, reply FROM calls")
        }
        con.close()

        def _reply_shape(route_name: str, rep_bytes: bytes) -> tuple[frozenset[str] | None, dict[str, int]]:
            try:
                obj = json.loads(rep_bytes)
                if route_name == "embeddings":
                    data_list = obj.get("data")
                    return (None, {"data": len(data_list)}) if isinstance(data_list, list) else (None, {})
                msg = (obj.get("choices") or [{}])[0].get("message") or {}
                parsed = json.loads(msg.get("content") or "{}")
                if isinstance(parsed, dict):
                    lens = {k: len(v) for k, v in parsed.items() if isinstance(v, list)}
                    return frozenset(parsed.keys()), lens
            except (ValueError, TypeError, IndexError):
                pass
            return None, {}

        self._seq_by_case: dict[str, dict[str, list[tuple[str, bytes, frozenset[str] | None, dict[str, int]]]]] = {}
        for lp in call_log_paths:
            p = Path(lp)
            if not p.is_file():
                continue
            per_file: dict[str, dict[str, list[tuple[str, bytes, frozenset[str] | None, dict[str, int]]]]] = {}
            for raw_line in p.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                row = json.loads(raw_line)
                if row.get("status") != 200 or row.get("phase") != "ingest":
                    continue
                cid = str(row.get("case_id") or "")
                route = str(row.get("route") or "")
                rk = str(row.get("request_sha256") or "")
                if not cid or route not in ("chat", "embeddings") or rk not in self._cache_by_key:
                    continue
                if cid in self._seq_by_case:
                    continue  # earlier clean log already supplied this case
                rep_b = self._cache_by_key[rk]
                rkeys, rlens = _reply_shape(route, rep_b)
                per_file.setdefault(cid, {"chat": [], "embeddings": []})[route].append(
                    (rk, rep_b, rkeys, rlens)
                )
            for cid, qs in per_file.items():
                self._seq_by_case[cid] = qs

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self._server is not None:
            return
        proxy = self

        class _H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
                pass

            def _read_body(self) -> bytes:
                if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    chunks = []
                    while True:
                        size = int(self.rfile.readline().split(b";", 1)[0].strip() or b"0", 16)
                        if size == 0:
                            while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                                pass
                            break
                        chunks.append(self.rfile.read(size))
                        self.rfile.readline()
                    return b"".join(chunks)
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n > 0 else b""

            def do_POST(self) -> None:  # noqa: N802
                raw = self._read_body()
                try:
                    req = json.loads(raw)
                except ValueError:
                    self._send(400, b'{"error":{"message":"invalid json"}}')
                    return
                path = self.path.split("?", 1)[0].rstrip("/")
                if path.endswith("chat/completions"):
                    route = "chat"
                elif path.endswith("embeddings"):
                    route = "embeddings"
                else:
                    self._send(404, b'{"error":{"message":"not found"}}')
                    return
                up = proxy.upstreams[route]
                req["model"] = up["model"]
                key = request_key(up, route, req)
                want_props: frozenset[str] | None = None
                want_emb_n: int | None = None
                if route == "embeddings":
                    inp = req.get("input")
                    if isinstance(inp, list):
                        want_emb_n = len(inp)
                    elif isinstance(inp, str):
                        want_emb_n = 1
                else:
                    rf = req.get("response_format")
                    if isinstance(rf, dict):
                        js = rf.get("json_schema")
                        if isinstance(js, dict) and isinstance(js.get("schema"), dict):
                            props = js["schema"].get("properties")
                            if isinstance(props, dict) and props:
                                want_props = frozenset(props.keys())
                with proxy.lock:
                    cid = proxy.active_case or ""
                    q = proxy._seq_by_case.get(cid, {}).get(route, [])
                    used = proxy._consumed.setdefault((cid, route), set())
                    if key in proxy._cache_by_key:
                        proxy.exact_hits += 1
                        for idx, entry in enumerate(q):
                            if idx not in used and entry[0] == key:
                                used.add(idx)
                                break
                        rep = proxy._cache_by_key[key]
                    else:
                        pick = None
                        for idx, entry in enumerate(q):
                            if idx in used:
                                continue
                            if want_props is not None and entry[2] is not None and entry[2] != want_props:
                                continue
                            if want_emb_n is not None and entry[3].get("data") is not None and entry[3].get("data") != want_emb_n:
                                continue
                            pick = idx
                            break
                        if pick is None and route == "embeddings" and want_emb_n is not None and want_emb_n > 0:
                            # If an episode's filtered batch size differs by +/-1 edge from the cached replay,
                            # resize a recorded cached embedding list from this same case (never a chat reply).
                            donor_bytes = next((entry[1] for idx, entry in enumerate(q) if idx not in used), None)
                            if donor_bytes is None and q:
                                donor_bytes = q[0][1]
                            if donor_bytes is not None:
                                try:
                                    dobj = json.loads(donor_bytes)
                                    dlist = dobj.get("data") or []
                                    if isinstance(dlist, list) and dlist:
                                        resized = [
                                            {**dlist[i % len(dlist)], "index": i}
                                            for i in range(want_emb_n)
                                        ]
                                        dobj["data"] = resized
                                        proxy.seq_hits += 1
                                        rep = json.dumps(dobj).encode("utf-8")
                                    else:
                                        rep = None
                                except ValueError:
                                    rep = None
                            else:
                                rep = None
                            if rep is None:
                                proxy.misses += 1
                                proxy.miss_details.append({"case_id": cid, "route": route, "key": key})
                        else:
                            if pick is None:
                                for idx in range(len(q)):
                                    if idx not in used:
                                        pick = idx
                                        break
                            if pick is not None:
                                used.add(pick)
                                proxy.seq_hits += 1
                                rep = q[pick][1]
                            else:
                                proxy.misses += 1
                                proxy.miss_details.append({"case_id": cid, "route": route, "key": key})
                                rep = None
                if rep is None:
                    self._send(400, b'{"error":{"message":"offline cache miss; paid calls forbidden"}}')
                else:
                    self._send(200, rep)

            def _send(self, code: int, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        srv, self._server = self._server, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()
            if self._thread is not None:
                self._thread.join(timeout=10)

    def __enter__(self) -> "OfflineReplayProxy":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def scope(self, adapter: str, config_label: str, case_id: str) -> Scope:
        with self.lock:
            self.active_case = case_id
        return Scope(self, f"offline-{adapter}-{case_id}", adapter, config_label, case_id)

    def _unregister(self, scope: Scope) -> None:
        scope.closed = True


def dump_product_store(
    *,
    product: str,
    cases_path: Path,
    adapter_config_path: Path,
    memory_side_path: Path,
    cache_path: Path,
    call_log_paths: Sequence[Path],
    shared_views_path: Path,
    out_path: Path,
    limit: int | None = None,
    resume: bool = True,
) -> dict[str, Any]:
    """Replay offline cached ingest for `product` and dump each case's store via its public API."""
    cases = list(read_jsonl(cases_path))
    view_case_ids: set[str] = set()
    if shared_views_path.is_file():
        for line in shared_views_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                view_case_ids.add(str(json.loads(line)["case_id"]))
    already_dumped: set[str] = set()
    if resume and out_path.is_file():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    already_dumped.add(str(json.loads(line)["case_id"]))
                except ValueError:
                    pass
    target_cases = [
        c for c in cases if (not view_case_ids or c.case_id in view_case_ids) and c.case_id not in already_dumped
    ]
    if limit is not None:
        target_cases = target_cases[:limit]

    acfg = json.loads(adapter_config_path.read_text(encoding="utf-8"))
    ms_cfg = load_memory_side(memory_side_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    dumped = len(already_dumped)
    errors: list[dict[str, str]] = []
    mode = "a" if resume and out_path.is_file() else "w"
    with OfflineReplayProxy(ms_cfg, cache_path=cache_path, call_log_paths=call_log_paths) as proxy:
        with out_path.open(mode, encoding="utf-8", newline="\n") as fh:
            for case in target_cases:
                cid = case.case_id
                recs = records_of(case)
                last_exc: Exception | None = None
                items: list[str] = []
                max_attempts = 3 if product == "graphiti" else 1
                for attempt in range(max_attempts):
                    if attempt > 0:
                        with proxy.lock:
                            proxy._consumed.pop((cid, "chat"), None)
                            proxy._consumed.pop((cid, "embeddings"), None)
                    try:
                        if product == "mem0":
                            from ..adapters.mem0 import Mem0Adapter  # noqa: PLC0415

                            ad = Mem0Adapter(proxy=proxy, config_label=acfg["config_label"], **acfg["config"])
                            try:
                                ad.reset(cid)
                                ad.ingest(recs)
                                raw_all = ad._memory.get_all(filters={"user_id": ad._ns}, limit=1000)
                                rows = raw_all.get("results", raw_all) if isinstance(raw_all, dict) else raw_all
                                items = [str(x.get("memory", "")) for x in rows if isinstance(x, dict) and x.get("memory")]
                            finally:
                                ad.close()
                        elif product == "langmem":
                            from ..adapters.langmem import LangMemAdapter, memory_text  # noqa: PLC0415

                            ad = LangMemAdapter(proxy=proxy, config_label=acfg["config_label"], **acfg["config"])
                            try:
                                ad.reset(cid)
                                ad.ingest(recs)
                                hits = ad._store.search(ad._ns, limit=1000)
                                items = [t for t in (memory_text(h) for h in hits) if t]
                            finally:
                                ad.close()
                        elif product == "graphiti":
                            from graphiti_core.edges import EntityEdge  # noqa: PLC0415
                            from ..adapters.graphiti import GraphitiAdapter  # noqa: PLC0415

                            ad = GraphitiAdapter(proxy=proxy, config_label=acfg["config_label"], **acfg["config"])
                            try:
                                ad.reset(cid)
                                ad.ingest(recs)
                                try:
                                    edges = ad._run(EntityEdge.get_by_group_ids(ad._driver, [ad._group]))
                                except Exception as edge_exc:
                                    if type(edge_exc).__name__ == "GroupsEdgesNotFoundError":
                                        edges = []
                                    else:
                                        raise
                                items = [
                                    str(e.fact)
                                    for e in edges
                                    if isinstance(getattr(e, "fact", None), str) and e.fact
                                ]
                            finally:
                                ad.close()
                        else:
                            raise ValueError(f"Unsupported product {product!r}")

                        last_exc = None
                        break
                    except ValueError as exc:
                        last_exc = exc
                        if attempt + 1 < max_attempts:
                            continue
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                        break

                if last_exc is not None:
                    errors.append({"case_id": cid, "error": f"{type(last_exc).__name__}: {last_exc}"})
                    continue

                rec = {
                    "case_id": cid,
                    "system": product,
                    "config_label": acfg["config_label"],
                    "n_store_items": len(items),
                    "store_items": items,
                    "store_text": "\n".join(items),
                }
                fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=True) + "\n")
                fh.flush()
                dumped += 1

        return {
            "product": product,
            "target_cases": len(target_cases),
            "dumped": dumped,
            "errors": len(errors),
            "error_samples": errors[:5],
            "exact_hits": proxy.exact_hits,
            "seq_hits": proxy.seq_hits,
            "misses": proxy.misses,
            "live_paid_calls": 0,
            "usd": 0.0,
            "out_path": str(out_path),
        }


def _matches_any(text_low: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pat, text_low) for pat in patterns)


def _date_variants(iso_date: str) -> tuple[str, ...]:
    """Match an ISO expiry date (`2026-04-13`) in ISO, short (`04-13`), or prose (`April 13`)."""
    out = [re.escape(iso_date.lower())]
    try:
        d = dt.date.fromisoformat(iso_date)
        mon_full = d.strftime("%B").lower()
        mon_abbr = d.strftime("%b").lower()
        day = str(d.day)
        day0 = f"{d.day:02d}"
        out.extend([
            rf"\b{mon_full}\s+0?{day}\b",
            rf"\b{mon_abbr}\.?\s+0?{day}\b",
            rf"\b0?{day}\s+{mon_full}\b",
            rf"\b{d.month:02d}-{day0}\b",
        ])
    except ValueError:
        pass
    return tuple(out)


def _slot_events(case: Case) -> list[Any]:
    meta = getattr(case, "metadata", None) or {}
    slot = str(meta.get("slot") or "")
    return [ev for ev in case.events if str((ev.fields or {}).get("slot") or "") == slot]


def _pref_present(text_low: str, value_code: str, ev_id: str | None = None) -> bool:
    if ev_id and ev_id.lower() in text_low:
        return True
    pats = PREF_PATTERNS.get(value_code)
    if pats and _matches_any(text_low, pats):
        return True
    return value_code.lower() in text_low


def _rule_present(text_low: str, rule_code: str, ev_id: str | None = None) -> bool:
    if ev_id and ev_id.lower() in text_low:
        return True
    pats = CONFLICT_RULE_PATTERNS.get(rule_code)
    if pats and _matches_any(text_low, pats):
        return True
    return rule_code.lower() in text_low


def _expiry_present(text_low: str, expires_on: str, ev_id: str | None = None) -> bool:
    if ev_id and ev_id.lower() in text_low:
        return True
    return _matches_any(text_low, _date_variants(expires_on))


def _prohibition_present(text_low: str, slot: str, ev_id: str | None = None) -> bool:
    if ev_id and ev_id.lower() in text_low:
        return True
    pats = PROHIBITION_PATTERNS.get(slot)
    return bool(pats and _matches_any(text_low, pats))


def classify_case_cause(
    case: Case,
    store_text: str,
    view_text: str,
    *,
    acbi_bucket: str | None = None,
) -> str:
    """Assign one mutually exclusive cause from `CAUSE_KEYS` for a wrong or withheld action."""
    st_low = (store_text or "").lower()
    vw_low = (view_text or "").lower()
    ev_by_id = {ev.id: ev for ev in case.events}
    meta = getattr(case, "metadata", None) or {}
    slot = str(meta.get("slot") or "")
    mech = case.mechanism

    if mech == "HU1":
        return "by_rule"

    if mech == "H0":
        pref_evs = [ev for ev in _slot_events(case) if ev.type == "preference"]
        if not pref_evs:
            return "by_rule"
        ev = pref_evs[-1]
        val = str((ev.fields or {}).get("value") or "")
        if _pref_present(vw_low, val, ev.id):
            return "returned_not_used"
        if _pref_present(st_low, val, ev.id):
            return "stored_not_returned"
        return "not_stored"

    if mech == "H1":
        old_ev = ev_by_id.get(case.d_star[0]) if len(case.d_star) >= 1 else None
        new_ev = ev_by_id.get(case.d_star[1]) if len(case.d_star) >= 2 else None
        old_val = str((old_ev.fields or {}).get("value") or "") if old_ev else ""
        new_val = str((new_ev.fields or {}).get("value") or "") if new_ev else ""
        old_in_view = bool(old_val and _pref_present(vw_low, old_val, old_ev.id if old_ev else None))
        new_in_view = bool(new_val and _pref_present(vw_low, new_val, new_ev.id if new_ev else None))
        if old_in_view and new_in_view:
            return "conflict_returned"
        if new_in_view:
            return "returned_not_used"
        new_in_store = bool(new_val and _pref_present(st_low, new_val, new_ev.id if new_ev else None))
        return "stored_not_returned" if new_in_store else "not_stored"

    if mech == "H2":
        grant_ev = ev_by_id.get(case.d_star[0]) if case.d_star else None
        expires_on = str((grant_ev.fields or {}).get("expires_on") or "") if grant_ev else ""
        other_grants = [
            ev
            for ev in _slot_events(case)
            if ev.type == "permission" and grant_ev is not None and ev.id != grant_ev.id
        ]
        exp_in_view = bool(expires_on and _expiry_present(vw_low, expires_on, grant_ev.id if grant_ev else None))
        other_in_view = any(
            _expiry_present(vw_low, str((g.fields or {}).get("expires_on") or ""), g.id)
            for g in other_grants
            if (g.fields or {}).get("expires_on")
        )
        if exp_in_view and other_in_view:
            return "conflict_returned"
        if exp_in_view:
            return "returned_not_used"
        exp_in_store = bool(expires_on and _expiry_present(st_low, expires_on, grant_ev.id if grant_ev else None))
        return "stored_not_returned" if exp_in_store else "not_stored"

    if mech == "H3":
        proh_ev = ev_by_id.get(case.d_star[0]) if case.d_star else None
        old_pref_evs = [ev for ev in _slot_events(case) if ev.type == "preference"]
        old_val = str((old_pref_evs[-1].fields or {}).get("value") or "") if old_pref_evs else ""
        proh_in_view = _prohibition_present(vw_low, slot, proh_ev.id if proh_ev else None)
        old_in_view = bool(old_val and _pref_present(vw_low, old_val, old_pref_evs[-1].id if old_pref_evs else None))
        if proh_in_view and old_in_view:
            return "conflict_returned"
        if proh_in_view:
            return "returned_not_used"
        proh_in_store = _prohibition_present(st_low, slot, proh_ev.id if proh_ev else None)
        return "stored_not_returned" if proh_in_store else "not_stored"

    if mech == "HU2":
        if acbi_bucket == "both":
            return "conflict_returned"
        ev_a = ev_by_id.get(case.d_star[0]) if len(case.d_star) >= 1 else None
        ev_b = ev_by_id.get(case.d_star[1]) if len(case.d_star) >= 2 else None
        code_a = str((ev_a.fields or {}).get("rule_value") or "") if ev_a else ""
        code_b = str((ev_b.fields or {}).get("rule_value") or "") if ev_b else ""
        a_view = bool(code_a and _rule_present(vw_low, code_a, ev_a.id if ev_a else None))
        b_view = bool(code_b and _rule_present(vw_low, code_b, ev_b.id if ev_b else None))
        if a_view and b_view:
            return "conflict_returned"
        a_store = bool(code_a and _rule_present(st_low, code_a, ev_a.id if ev_a else None))
        b_store = bool(code_b and _rule_present(st_low, code_b, ev_b.id if ev_b else None))
        if a_store and b_store:
            return "stored_not_returned"
        return "not_stored"

    return "by_rule"


def compute_replay_fidelity(
    store_records: Iterable[Mapping[str, Any]],
    view_records: Iterable[Mapping[str, Any]],
    *,
    budget: int = 1000,
    low_fidelity_threshold: float = LOW_FIDELITY_THRESHOLD,
) -> dict[str, Any]:
    """Share of budget-cut view units found verbatim (whitespace-normalized) in the replayed store."""
    store_by_case = {str(r["case_id"]): str(r.get("store_text") or "") for r in store_records}
    matched = 0
    total = 0
    cases_checked = 0
    for vrec in view_records:
        cid = str(vrec.get("case_id") or "")
        if not cid or cid not in store_by_case:
            continue
        cases_checked += 1
        st_norm = " ".join(store_by_case[cid].lower().split())
        raw_units = [str(x) for x in (vrec.get("units") or [])]
        if budget > 0 and raw_units:
            units = enforce_budget(
                Retrieved(
                    text=str(vrec.get("text") or ""),
                    record_ids=[str(x) for x in (vrec.get("record_ids") or [])],
                    units=raw_units,
                ),
                budget,
            ).units
        else:
            units = raw_units
        for u in units:
            u_norm = " ".join(str(u).lower().split())
            if not u_norm:
                continue
            total += 1
            if u_norm in st_norm:
                matched += 1
    rate = round(matched / total, 4) if total > 0 else None
    low = bool(rate is not None and rate < low_fidelity_threshold)
    return {
        "method": "budget_cut_unit_in_store",
        "cases_checked": cases_checked,
        "matched_view_units": matched,
        "total_view_units": total,
        "view_unit_match_rate": rate,
        "low_fidelity_threshold": low_fidelity_threshold,
        "low_fidelity": low,
    }


def _cut_view_text(vrec: Mapping[str, Any] | None, budget: int) -> str:
    if vrec is None:
        return ""
    text = str(vrec.get("text") or "")
    units = list(vrec.get("units") or [])
    if budget > 0 and units:
        cut = enforce_budget(
            Retrieved(
                text=text,
                record_ids=[str(x) for x in (vrec.get("record_ids") or [])],
                units=[str(x) for x in units],
            ),
            budget,
        )
        return cut.text
    return text


def _bootstrap_cause_stats(
    case_ids: Sequence[str],
    cause_by_case: Mapping[str, str],
    rows_by_case: Mapping[str, list[dict[str, Any]]],
    *,
    kind_filter: str,
    seed: int = 11,
    reps: int = 1000,
) -> dict[str, Any]:
    """Compute counts, shares (of filtered decisions), and paired-bootstrap 95% CIs."""
    feats: dict[str, list[float]] = {"den": [], **{k: [] for k in CAUSE_KEYS}}
    for cid in case_ids:
        rs = rows_by_case.get(cid, ())
        matched = 0
        for r in rs:
            is_wrong = is_committed(r["action"]) and r["action"] != r["a_star"]
            is_held = r["action"] == REQUEST_INFO and r["action"] != r["a_star"]
            if kind_filter == "wrong" and is_wrong:
                matched += 1
            elif kind_filter == "withheld" and is_held:
                matched += 1
            elif kind_filter == "combined" and (is_wrong or is_held):
                matched += 1
        feats["den"].append(float(matched))
        c_key = cause_by_case[cid]
        for k in CAUSE_KEYS:
            feats[k].append(float(matched) if k == c_key else 0.0)

    boot = Boot(len(case_ids), reps=reps, seed=seed)
    cache = boot.resampled_sums(feats)
    full = Boot.full_sums(feats)
    total = int(full["den"])

    def _share(k: str):
        return lambda s: (s[k] / s["den"]) if s["den"] > 0 else None

    out: dict[str, Any] = {"total": total, "causes": {}}
    for k in CAUSE_KEYS:
        st = boot.stat(feats, _share(k), cache=cache)
        out["causes"][k] = {
            "count": int(full[k]),
            "share": st["value"],
            "ci95": st["ci95"],
        }
    return out


def compute_cause_split(
    *,
    cases: Sequence[Case],
    store_records: Iterable[Mapping[str, Any]],
    view_records: Iterable[Mapping[str, Any]],
    metric_rows: Iterable[Mapping[str, Any]],
    acbi_data: Mapping[str, Any] | None = None,
    budget: int = 1000,
    seed: int = 11,
    reps: int = 1000,
) -> dict[str, Any]:
    """Compute text-free exploratory cause split per product and decider with 95% intervals."""
    store_list = list(store_records)
    view_list = list(view_records)
    case_map = {c.case_id: c for c in cases}
    store_by_case = {str(r["case_id"]): str(r.get("store_text") or "") for r in store_list}
    view_by_case = {str(r["case_id"]): r for r in view_list}
    fidelity = compute_replay_fidelity(store_list, view_list, budget=budget)

    acbi_summary_by_decider: dict[str, Any] = {}
    if isinstance(acbi_data, Mapping):
        for did, dd in (acbi_data.get("deciders") or {}).items():
            acbi_summary_by_decider[did] = dd.get("cells") or {}

    primary = [dict(r) for r in metric_rows if int(r.get("ingest_pass", 1)) == 1]
    by_decider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in primary:
        by_decider[str(r["decider"])].append(r)

    deciders_out: dict[str, Any] = {}
    for did, drs in sorted(by_decider.items()):
        gate_pass = {
            str(r["case_id"])
            for r in drs
            if r.get("kind") == "gate" and r.get("action") == r.get("a_star") and str(r["case_id"]) in case_map
        }
        rows_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
        cname_found = None
        for r in drs:
            if r.get("kind") != "view":
                continue
            cid = str(r["case_id"])
            if cid not in gate_pass:
                continue
            if budget and r.get("budget") not in (None, budget):
                continue
            rows_by_case[cid].append(r)
            if cname_found is None:
                cname_found = cell_name(r["system"], r["config_label"], r.get("budget"))

        scored_case_ids = sorted(cid for cid in rows_by_case if cid in case_map)
        cause_by_case: dict[str, str] = {}
        mech_cause_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        store_item_counts: list[int] = []
        view_char_counts: list[int] = []

        for cid in scored_case_ids:
            case = case_map[cid]
            st_txt = store_by_case.get(cid, "")
            vw_rec = view_by_case.get(cid)
            vw_txt = _cut_view_text(vw_rec, budget)
            view_char_counts.append(len(vw_txt))
            if cid in store_by_case:
                store_item_counts.append(len([x for x in st_txt.splitlines() if x.strip()]))

            acbi_bucket = None
            if case.mechanism == "HU2" and vw_rec is not None:
                hits = exact_id_matcher(case, {"text": vw_txt, "record_ids": vw_rec.get("record_ids") or []})
                if len(hits) >= len(set(case.d_star)):
                    acbi_bucket = "both"

            cause = classify_case_cause(case, st_txt, vw_txt, acbi_bucket=acbi_bucket)
            cause_by_case[cid] = cause

            for r in rows_by_case[cid]:
                is_wrong = is_committed(r["action"]) and r["action"] != r["a_star"]
                is_held = r["action"] == REQUEST_INFO and r["action"] != r["a_star"]
                if is_wrong:
                    mech_cause_counts[f"{case.mechanism}:wrong"][cause] += 1
                elif is_held:
                    mech_cause_counts[f"{case.mechanism}:withheld"][cause] += 1

        deciders_out[did] = {
            "cell": cname_found,
            "n_gated_cases": len(scored_case_ids),
            "n_decisions": sum(len(rows_by_case[c]) for c in scored_case_ids),
            "mean_store_items": round(sum(store_item_counts) / max(1, len(store_item_counts)), 2),
            "mean_view_chars": round(sum(view_char_counts) / max(1, len(view_char_counts)), 1),
            "acbi_exact_id_counts": (acbi_summary_by_decider.get(did, {}).get(cname_found or "") or {}).get("counts"),
            "wrong": _bootstrap_cause_stats(scored_case_ids, cause_by_case, rows_by_case, kind_filter="wrong", seed=seed, reps=reps),
            "withheld": _bootstrap_cause_stats(scored_case_ids, cause_by_case, rows_by_case, kind_filter="withheld", seed=seed, reps=reps),
            "combined": _bootstrap_cause_stats(scored_case_ids, cause_by_case, rows_by_case, kind_filter="combined", seed=seed, reps=reps),
            "by_mechanism": {k: dict(sorted(v.items())) for k, v in sorted(mech_cause_counts.items())},
        }

    return {
        "schema": CAUSE_SCHEMA,
        "exploratory": True,
        "budget": budget,
        "bootstrap": {"seed": seed, "reps": reps, "level": 0.95},
        "replay_fidelity": fidelity,
        "deciders": deciders_out,
    }


def format_cause_split_markdown(result: Mapping[str, Any]) -> str:
    """Render a cause-split summary table with view-unit match rate and low-fidelity flag above it."""
    prod = str(result.get("product") or "product")
    rf = result.get("replay_fidelity") or {}
    rate = rf.get("view_unit_match_rate")
    matched = rf.get("matched_view_units", 0)
    total = rf.get("total_view_units", 0)
    low = bool(rf.get("low_fidelity"))
    rate_str = "-" if rate is None else f"{100.0 * float(rate):.1f}% ({matched}/{total})"
    fid_note = "low-fidelity (below 90% view-unit match)" if low else "high-fidelity"
    lines = [
        f"### {prod} (exploratory)",
        f"Replay view-unit match rate: {rate_str} [{fid_note}]",
        "",
        "| Decider | Decision | Total | Not stored | Stored, not returned | Returned, not used | Conflict returned | By rule |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for did, dd in sorted((result.get("deciders") or {}).items()):
        for kind, label in (("wrong", "Wrong action"), ("withheld", "Withheld action")):
            block = dd.get(kind) or {}
            causes = block.get("causes") or {}
            cells = []
            for ck in CAUSE_KEYS:
                entry = causes.get(ck) or {}
                cnt = entry.get("count", 0)
                sh = entry.get("share")
                ci = entry.get("ci95")
                if sh is None or ci is None:
                    cells.append(f"{cnt} (n/a)")
                else:
                    cells.append(f"{cnt} ({100*sh:.1f}% [{100*ci[0]:.1f}, {100*ci[1]:.1f}])")
            lines.append(f"| {did} | {label} | {block.get('total', 0)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline A2 store dump and cause-split diagnostic (strictly $0).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dp = sub.add_parser("dump", help="Replay offline cached ingest and dump product store via public API.")
    dp.add_argument("--product", required=True, choices=("mem0", "langmem", "graphiti"))
    dp.add_argument("--cases", required=True, type=Path)
    dp.add_argument("--adapter-config", required=True, type=Path)
    dp.add_argument("--memory-side", required=True, type=Path)
    dp.add_argument("--cache", required=True, type=Path)
    dp.add_argument("--call-logs", nargs="+", required=True, type=Path)
    dp.add_argument("--shared-views", required=True, type=Path)
    dp.add_argument("--out", required=True, type=Path)
    dp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("score", help="Compute exploratory cause-split JSON from dumped store, views, and metrics.")
    sp.add_argument("--product", required=True, choices=("mem0", "langmem", "graphiti"))
    sp.add_argument("--cases", required=True, type=Path)
    sp.add_argument("--store", required=True, type=Path)
    sp.add_argument("--views", required=True, type=Path)
    sp.add_argument("--table-root", required=True, type=Path)
    sp.add_argument("--out", required=True, type=Path)
    sp.add_argument("--budget", type=int, default=1000)
    sp.add_argument("--seed", type=int, default=11)
    sp.add_argument("--reps", type=int, default=1000)

    args = ap.parse_args(argv)
    if args.cmd == "dump":
        res = dump_product_store(
            product=args.product,
            cases_path=args.cases,
            adapter_config_path=args.adapter_config,
            memory_side_path=args.memory_side,
            cache_path=args.cache,
            call_log_paths=args.call_logs,
            shared_views_path=args.shared_views,
            out_path=args.out,
            limit=args.limit,
        )
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0 if res["errors"] == 0 and res["misses"] == 0 else 1

    cases = list(read_jsonl(args.cases))
    store_recs = [json.loads(l) for l in args.store.read_text(encoding="utf-8").splitlines() if l.strip()]
    view_recs = [json.loads(l) for l in args.views.read_text(encoding="utf-8").splitlines() if l.strip()]
    metric_rows: list[dict[str, Any]] = []
    acbi_merged: dict[str, Any] = {"deciders": {}}
    for dec_dir in sorted(p for p in args.table_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        sdir = dec_dir / args.product
        mp = sdir / "metrics.jsonl"
        if mp.is_file():
            for line in mp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    metric_rows.append(json.loads(line))
        ap_file = sdir / "acbi.json"
        if ap_file.is_file():
            ac = json.loads(ap_file.read_text(encoding="utf-8"))
            acbi_merged["deciders"].update(ac.get("deciders") or {})

    out = compute_cause_split(
        cases=cases,
        store_records=store_recs,
        view_records=view_recs,
        metric_rows=metric_rows,
        acbi_data=acbi_merged,
        budget=args.budget,
        seed=args.seed,
        reps=args.reps,
    )
    out["product"] = args.product
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(format_cause_split_markdown(out))
    print(f"wrote {args.out} ({len(out['deciders'])} deciders)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
