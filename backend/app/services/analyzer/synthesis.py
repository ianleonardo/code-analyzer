"""Stage 4 semantic synthesis with the configured LLM (Gemini or Spirare).

The LLM sees only pre-distilled structural facts: a file's role, layer, symbols, its dependencies' summaries and a
bounded excerpt. Files are visited bottom-up in the condensed DAG order so leaf modules are summarised before the
files that use them. Summaries are cached by content hash, so unchanged files are never re-summarised.
Without an LLM, deterministic summaries are produced from the same facts.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath

from app.config import SYNTHESIS_RULES
from app.services import llm

logger = logging.getLogger(__name__)
EXCERPT_CHARS = 6000


def deterministic_file_summary(item: dict, symbols: list[dict]) -> str:
    names = [s["name"] for s in symbols if not s["parent"]][:6]
    parts = [f"{item['role']} in the {item['layer']} layer ({item['language']}, {item['code_lines']} code lines)."]
    if names:
        parts.append("Defines " + ", ".join(f"`{n}`" for n in names) + ("…" if len(symbols) > 6 else "") + ".")
    if item["concepts"]:
        parts.append("Behaviour: " + ", ".join(item["concepts"][:5]) + ".")
    if item["fan_in"]:
        parts.append(f"Used by {item['fan_in']} file(s).")
    return " ".join(parts)


def deterministic_repo_summary(repo_name: str, macro, file_count: int, loc: int, metrics: dict) -> str:
    languages = list(macro.languages)[:4]
    web = [fw["name"] for fw in macro.frameworks if fw["category"] in ("web", "ui")][:5]
    data = [fw["name"] for fw in macro.frameworks if fw["category"] in ("orm", "database", "cache", "messaging")][:6]
    lines = [
        f"**{repo_name}** is a {macro.architecture_style.lower()} codebase written mainly in {', '.join(languages) or 'unknown languages'}, "
        f"with {file_count:,} analysed files and {loc:,} lines of code.",
    ]
    if web:
        lines.append(f"It is built with {', '.join(web)}" + (f" and uses {', '.join(data)} for data and integration." if data else "."))
    if len(macro.containers) > 1:
        lines.append(f"It is organised into {len(macro.containers)} containers: " + ", ".join(f"{c.name} ({c.kind})" for c in macro.containers[:8]) + ".")
    if macro.readme_excerpt:
        first = next((p.strip() for p in macro.readme_excerpt.split("\n\n") if p.strip() and not p.strip().startswith(("#", "!", "[", "<", "```"))), "")
        if first:
            lines.append(f"The README states: “{first[:400]}”")
    lines.append(f"Health score {metrics.get('health_score', 0)}/100 with {metrics.get('cycles', 0)} dependency cycle(s) and {metrics.get('layer_violations', 0)} layer-violating edge(s).")
    return "\n\n".join(lines)


def summarise_files(order: list[str], files: dict[str, dict], symbols_by_file: dict[str, list[dict]], dependencies: dict[str, set[str]], limit: int, cache: dict[str, str], log) -> int:
    """Summarise up to `limit` of the most central files, bottom-up. Returns how many the LLM produced."""
    if limit <= 0 or not llm.configured():
        return 0
    central = sorted((p for p in files if files[p]["is_source"] and not files[p]["is_test"] and files[p]["category"] == "code"), key=lambda p: -files[p]["pagerank"])[:limit]
    chosen = set(central)
    ordered = [path for path in order if path in chosen] + [p for p in central if p not in order]
    produced = 0
    # levels: a file can run once all of its chosen dependencies are done
    done: set[str] = set()
    remaining = list(ordered)
    with ThreadPoolExecutor(max_workers=4) as pool:
        while remaining:
            ready = [p for p in remaining if not ((dependencies.get(p, set()) & chosen) - done - {p})]
            if not ready:
                ready = remaining[:4]  # cycles: proceed anyway
            batch = ready[:16]
            for path in batch:
                remaining.remove(path)
            cached = [p for p in batch if files[p]["sha256"] in cache]
            for path in cached:
                files[path]["summary"] = cache[files[path]["sha256"]]
                done.add(path)
            todo = [p for p in batch if p not in cached]
            results = list(pool.map(lambda p: _summarise(p, files, symbols_by_file, dependencies), todo))
            for path, summary in zip(todo, results, strict=True):
                if summary:
                    files[path]["summary"] = summary
                    produced += 1
                done.add(path)
            log(f"LLM summarised {len(done)}/{len(ordered)} central files")
    return produced


def _summarise(path: str, files: dict[str, dict], symbols_by_file: dict[str, list[dict]], dependencies: dict[str, set[str]]) -> str:
    item = files[path]
    deps = []
    for dep in sorted(dependencies.get(path, set()))[:12]:
        summary = files.get(dep, {}).get("summary", "")
        deps.append(f"- {dep}: {summary[:200]}")
    symbols = [f"{s['kind']} {s['qualified_name']} (L{s['line_start']}-{s['line_end']}, complexity {s['complexity']})" for s in symbols_by_file.get(path, [])[:40]]
    prompt = (
        f"FILE: {path}\nLANGUAGE: {item['language']}\nLAYER: {item['layer']}\nDETECTED ROLE: {item['role']}\n"
        f"CONCEPTS: {', '.join(item['concepts']) or 'none'}\nFAN-IN: {item['fan_in']} FAN-OUT: {item['fan_out']}\n"
        f"SYMBOLS:\n" + "\n".join(symbols) + "\n\nDEPENDENCIES (already summarised):\n" + ("\n".join(deps) or "none") +
        f"\n\nCODE EXCERPT:\n{(item.get('text') or '')[:EXCERPT_CHARS]}\n\n"
        'Return JSON: {"summary": "2-3 sentences: what this file does and why it exists, naming its key symbols", '
        '"business_capability": "short phrase or empty"}'
    )
    try:
        data = llm.complete_json(SYNTHESIS_RULES, prompt, 0.1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("File summary failed for %s: %s", path, exc)
        return ""
    summary = str(data.get("summary") or "").strip()
    capability = str(data.get("business_capability") or "").strip()
    if capability and summary:
        summary = f"{summary} Capability: {capability}."
    return summary[:1500]


def summarise_containers(macro, files: dict[str, dict], containers_by_file: dict[str, str]) -> None:
    if not llm.configured():
        return
    for container in macro.containers:
        members = sorted((f for f in files.values() if containers_by_file.get(f["path"]) == container.path and f.get("summary")), key=lambda f: -f["pagerank"])[:15]
        if not members:
            continue
        prompt = (
            f"CONTAINER: {container.name} ({container.kind}, {container.ecosystem})\nFRAMEWORKS: {', '.join(fw['name'] for fw in container.frameworks)}\n"
            "KEY FILES:\n" + "\n".join(f"- {f['path']} [{f['role']}]: {f['summary'][:240]}" for f in members) +
            '\n\nReturn JSON: {"summary": "2-4 sentences describing the container\'s responsibility"}'
        )
        try:
            data = llm.complete_json(SYNTHESIS_RULES, prompt, 0.1)
            container.description = str(data.get("summary") or container.description)[:1200]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Container summary failed: %s", exc)


def summarise_repository(repo_name: str, macro, files: dict[str, dict], concerns: list[dict], metrics: dict) -> dict:
    """Repository-level narrative. Returns {} when the LLM is not configured or fails."""
    if not llm.configured():
        return {}
    central = sorted((f for f in files.values() if f.get("summary")), key=lambda f: -f["pagerank"])[:25]
    facts = {
        "repository": repo_name,
        "architecture_style": macro.architecture_style,
        "style_evidence": macro.style_evidence,
        "languages_loc": dict(list(macro.languages.items())[:8]),
        "frameworks": [f"{fw['name']} ({fw['category']})" for fw in macro.frameworks][:30],
        "containers": [{"name": c.name, "kind": c.kind, "path": c.path or "/", "summary": c.description[:300]} for c in macro.containers][:15],
        "entry_points": macro.entry_points[:15],
        "external_systems": [e["name"] for e in macro.external_systems][:15],
        "metrics": {k: v for k, v in metrics.items() if k != "penalties"},
        "top_concerns": [f"[{c['severity']}] {c['title']}" for c in concerns[:15]],
    }
    prompt = (
        "REPOSITORY FACTS:\n" + json.dumps(facts, indent=1, default=str) +
        "\n\nMOST CENTRAL FILES:\n" + "\n".join(f"- {f['path']} [{f['layer']}/{f['role']}]: {f['summary'][:260]}" for f in central) +
        "\n\nREADME (unverified claims):\n" + (macro.readme_excerpt[:3000] or "none") +
        '\n\nReturn JSON: {"business_purpose": "one sentence", "summary": "3-5 short Markdown paragraphs: purpose, architecture, key flows, '
        'data and integrations, notable risks", "key_capabilities": ["..."], "architecture_notes": ["..."], "risks": ["..."]}'
    )
    try:
        data = llm.complete_json(SYNTHESIS_RULES, prompt, 0.2)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Repository summary failed: %s", exc)
        return {}
    return {
        "business_purpose": str(data.get("business_purpose") or "")[:600],
        "summary": str(data.get("summary") or "")[:8000],
        "key_capabilities": [str(x)[:300] for x in (data.get("key_capabilities") or [])][:12],
        "architecture_notes": [str(x)[:300] for x in (data.get("architecture_notes") or [])][:12],
        "risks": [str(x)[:300] for x in (data.get("risks") or [])][:12],
    }


def display_name(path: str) -> str:
    return PurePosixPath(path).name
