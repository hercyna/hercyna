---
name: New memory product submission
about: Submit a verified blind-adapter run for exploratory publication
title: "[Vendor] <adapter_name> (<date>)"
labels: submission
---

## Adapter Metadata
- Adapter name (`adapters/<name>/adapter.py`):
- Product version & config label:
- Track & protocol (`chat_agent` / `protocol/v2.json`):

## Verification Evidence
- Paste final line of `bench verify submissions/<name>/<date>-<slug> --cases data/v2/chat_agent/table_cases.jsonl`:
- Confirm `uv run bench freeze --check --protocol protocol/v2.json` passes untouched.
- Confirm zero private `views.jsonl` or raw case text is present.

## Terms Acceptance
- [ ] I have read `TERMS.md` and `LEGAL.md` and accept exploratory publication beside wrong action, withheld action, right action, and left-out ingest-error counts.
- [ ] I understand neutral evaluation badges (`Evaluated on Hercyna v2`) never link to a single score or imply endorsement.
