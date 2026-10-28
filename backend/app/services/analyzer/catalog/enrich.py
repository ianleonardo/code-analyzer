"""LLM enrichment of catalogue items: purpose plus business / processing rules with line citations.

Only what code cannot state directly is asked for; the deterministic facts are sent with the excerpt so the model
explains rather than invents. Results are cached by item content hash across analyses of the same repository.
Items are batched (several per call) and processed most-important first, up to the configured limit.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from app.config import SYNTHESIS_RULES
from app.services import llm
from app.services.analyzer.catalog.model import Catalog, Item

logger = logging.getLogger(__name__)
PRIORITY = {"endpoint": 0, "transaction": 0, "screen": 1, "service": 2, "program": 2, "job": 3, "listener": 3, "table": 4, "repository": 5, "view_model": 5, "api_client": 6, "procedure": 6,
            "integration": 7, "security": 7, "query": 8}
BATCH = 5
EXCERPT = 4500


def _facts(item: Item) -> dict:
    d = item.details
    keep = {
        "endpoint": ("method", "path", "params", "security", "errors", "rules", "calls", "status_codes"),
        "screen": ("platform", "type", "routes", "fields", "actions", "backend_calls", "navigation", "rules"),
        "service": ("dependencies", "methods"),
        "job": ("schedule", "human", "calls", "steps", "rules"),
        "listener": ("transport", "destinations", "calls", "rules"),
        "table": ("columns", "foreign_keys", "crud", "comment"),
        "repository": ("entity", "methods"),
        "view_model": ("state", "methods", "dependencies"),
        "procedure": ("params", "tables"),
        "program": ("dialect", "role", "entry", "calls", "files", "sql", "screens", "units"),
        "transaction": ("transid", "program", "description", "source"),
        "copybook": ("records", "dclgen", "prototypes"),
        "file": ("organization", "ddnames", "records", "accessed_by"),
    }.get(item.kind, tuple(d)[:8])
    out = {}
    for k in keep:
        if k in d and d[k]:
            out[k] = d[k]
    text = json.dumps(out, default=str)
    if len(text) > 3500:
        for k in list(out):
            if isinstance(out[k], list):
                out[k] = out[k][:12]
    return out


def _prompt(batch: list[Item]) -> str:
    parts = []
    for item in batch:
        parts.append(
            f"### ITEM {item.key}\nLAYER: {item.layer} KIND: {item.kind} NAME: {item.name}\nLOCATION: {item.path}:{item.line_start}\nSIGNATURE: {item.signature}\n"
            f"FACTS (extracted from code, trustworthy): {json.dumps(_facts(item), default=str)[:3500]}\n"
            f"CODE (line numbers start at {item.line_start}):\n{item.code[:EXCERPT]}\n"
        )
    return (
        "Explain each catalogue item below for a solution analyst. Use only what the code and facts show; never invent behaviour.\n\n"
        + "\n".join(parts)
        + '\nReturn JSON: {"items": [{"key": "<ITEM key exactly>", "purpose": "1-2 sentences in business terms", '
        '"rules": [{"rule": "one business / processing / validation rule in plain English", "line": <absolute line number or 0>}], '
        '"notes": "optional caveat (empty if none)"}]}. Up to 8 rules per item, most important first; omit trivial getters.'
    )


def enrich(cat: Catalog, limit: int, cache: dict[str, dict], log=lambda _m: None) -> int:
    """Fills item.summary / details['llm_rules']. Returns the number produced by the LLM (cache hits excluded)."""
    candidates = sorted((i for i in cat.items.values() if i.kind in PRIORITY and i.code), key=lambda i: (PRIORITY[i.kind], i.key))
    hits = 0
    pending: list[Item] = []
    for item in candidates:
        cached = cache.get(item.content_hash())
        if cached:
            item.summary, item.summary_source = cached.get("summary", ""), "llm"
            if cached.get("rules"):
                item.details["llm_rules"] = cached["rules"]
            hits += 1
        elif len(pending) < limit:
            pending.append(item)
    if hits:
        log(f"Layer catalogue: reused {hits} cached LLM explanations")
    if not pending or not llm.configured():
        return 0
    batches = [pending[i : i + BATCH] for i in range(0, len(pending), BATCH)]
    produced = 0

    def work(batch: list[Item]) -> int:
        try:
            data = llm.complete_json(SYNTHESIS_RULES, _prompt(batch), 0.1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Catalogue enrichment failed: %s", exc)
            return 0
        by_key = {i.key: i for i in batch}
        count = 0
        for entry in data.get("items") or []:
            if not isinstance(entry, dict):
                continue
            item = by_key.get(str(entry.get("key", "")))
            if item is None:
                continue
            purpose = str(entry.get("purpose") or "").strip()
            rules = []
            for rule in entry.get("rules") or []:
                if isinstance(rule, dict) and rule.get("rule"):
                    try:
                        line = int(rule.get("line") or 0)
                    except (TypeError, ValueError):
                        line = 0
                    if line and not (item.line_start - 5 <= line <= max(item.line_end, item.line_start) + 5):
                        line = 0  # citation outside the item: drop it rather than mislead
                    rules.append({"rule": str(rule["rule"])[:400], "line": line})
            if purpose:
                item.summary, item.summary_source = purpose[:1200], "llm"
                count += 1
            if rules:
                item.details["llm_rules"] = rules[:8]
            if entry.get("notes"):
                item.details["llm_notes"] = str(entry["notes"])[:400]
        return count

    with ThreadPoolExecutor(max_workers=4) as pool:
        for index, count in enumerate(pool.map(work, batches)):
            produced += count
            log(f"Layer catalogue: LLM explained {min(len(pending), (index + 1) * BATCH)}/{len(pending)} items")
    return produced
