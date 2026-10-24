"""Quality & smell audit (the CONCERNS.md registry) and the 0-100 codebase health score.

Concern categories: Tarjan SCC Cycle, Layer Violation, God Module, Smart Controller, Complex Function, Large File,
Duplicated Code, Possibly Unused File, Hardcoded Secret, Missing Tests, Documentation Drift, TODO Debt.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import PurePosixPath

LAYER_RANK = {"ui": 4, "api": 3, "service": 2, "data": 1}

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Hardcoded password", re.compile(r"""(?i)\b(password|passwd|pwd|secret|api[_-]?key|client[_-]?secret)\b\s*[:=]\s*["']([^"'\s$%{}<>]{6,})["']""")),
    ("Connection string with password", re.compile(r"(?i)\b(?:jdbc:|postgres(?:ql)?://|mysql://|mongodb(?:\+srv)?://|redis://)[^\s\"']*:[^\s\"'@/]{4,}@")),
]
PLACEHOLDER = re.compile(r"(?i)^(x+|\*+|changeme|change-me|password|secret|example|dummy|test|your[_-].*|<.*>|\$\{.*\}|placeholder|null|none|todo)$")


def _concern(severity: str, category: str, title: str, path: str = "", detail: str = "", evidence: str = "") -> dict:
    return {"severity": severity, "category": category, "title": title[:500], "path": path, "detail": detail[:4000], "evidence": evidence[:2000]}


def audit(files: list[dict], symbols: list[dict], graph, macro, containers_by_file: dict[str, str]) -> tuple[list[dict], dict]:
    concerns: list[dict] = []
    by_path = {item["path"]: item for item in files}
    source = [item for item in files if item["is_source"]]

    # Tarjan SCC cycles
    for index, component in enumerate(graph.sccs, start=1):
        severity = "critical" if len(component) > 4 else "warning"
        cut = _suggest_cut(component, graph)
        concerns.append(_concern(
            severity, "Tarjan SCC Cycle", f"Circular dependency #{index} across {len(component)} files",
            component[0], "These files depend on each other in a cycle, so none can be changed, tested or deployed in isolation.",
            " ⟷ ".join(component[:12]) + (f"\nSuggested cut-point: {cut}" if cut else ""),
        ))

    # Layer violations
    violations: dict[tuple[str, str], list[str]] = defaultdict(list)
    for edge in graph.edges:
        if edge.kind not in ("IMPORTS", "INJECTS", "CALLS"):
            continue
        source_file, target_file = by_path.get(edge.source), by_path.get(edge.target)
        if not source_file or not target_file or source_file["is_test"] or target_file["is_test"] or source_file["is_generated"]:
            continue
        a, b = source_file["layer"], target_file["layer"]
        if a in LAYER_RANK and b in LAYER_RANK and LAYER_RANK[a] < LAYER_RANK[b]:
            violations[(a, b)].append(f"{edge.source} → {edge.target}")
        elif a == "ui" and b == "data" and containers_by_file.get(edge.source) == containers_by_file.get(edge.target):
            violations[(a, b)].append(f"{edge.source} → {edge.target}")
    for (a, b), items in sorted(violations.items(), key=lambda kv: -len(kv[1])):
        severity = "critical" if (a == "data" and b in ("api", "ui")) else "warning"
        title = f"Layer violation: {a} layer depends on {b} layer ({len(items)} edge{'s' if len(items) != 1 else ''})"
        if a == "ui" and b == "data":
            title = f"UI bypasses the API/service layers and reaches the data layer ({len(items)} edges)"
        concerns.append(_concern(severity, "Layer Violation", title, items[0].split(" → ")[0],
                                 "Dependencies should point from ui → api → service → data. Upward dependencies couple low-level code to high-level policy.",
                                 "\n".join(items[:20])))

    # God modules, large files, smart controllers
    fan_in_sorted = sorted(item["fan_in"] for item in source) or [0]
    fan_in_p95 = fan_in_sorted[int(len(fan_in_sorted) * 0.95)] if len(fan_in_sorted) > 20 else 15
    for item in source:
        loc, symbol_count, fan_in = item["code_lines"], item["symbol_count"], item["fan_in"]
        if item["is_test"] or item["is_generated"]:
            continue
        if loc > 1000 or symbol_count > 60 or (fan_in >= max(15, fan_in_p95) and loc > 500):
            severity = "critical" if loc > 2000 or symbol_count > 120 else "warning"
            concerns.append(_concern(severity, "God Module", f"God module: {PurePosixPath(item['path']).name}", item["path"],
                                     "One file carries too many responsibilities; split it by cohesive responsibilities.",
                                     f"{loc} code lines · {symbol_count} symbols · fan-in {fan_in} · fan-out {item['fan_out']}"))
        elif loc > 600:
            concerns.append(_concern("info", "Large File", f"Large file ({loc} code lines)", item["path"], "Consider splitting for readability.", f"{loc} code lines"))
        if item["layer"] == "api" and item["role"].startswith(("Controller", "API Router")):
            leaks = [c for c in item["concepts"] if c in ("db-read", "db-write", "tx-scope")]
            if item["complexity"] > 40 or len(leaks) >= 2:
                concerns.append(_concern("warning", "Smart Controller", f"Smart controller: {PurePosixPath(item['path']).name}", item["path"],
                                         "The controller holds business or persistence logic that belongs in a service or repository.",
                                         f"complexity {item['complexity']}" + (f" · leaked concerns: {', '.join(leaks)}" if leaks else "")))

    # Complex functions
    complex_symbols = sorted(
        (
            s for s in symbols
            if s["kind"] in ("function", "method", "constructor", "procedure", "paragraph") and s["complexity"] > 15
            and not by_path.get(s["path"], {}).get("is_test") and not by_path.get(s["path"], {}).get("is_generated")
        ),
        key=lambda s: -s["complexity"],
    )
    for symbol in complex_symbols[:60]:
        severity = "critical" if symbol["complexity"] > 30 else "warning"
        concerns.append(_concern(severity, "Complex Function", f"High cyclomatic complexity: {symbol['qualified_name']} ({symbol['complexity']})",
                                 symbol["path"], "Many branches make this hard to test and change; extract smaller functions.",
                                 f"lines {symbol['line_start']}-{symbol['line_end']} · McCabe {symbol['complexity']}"))

    # Possibly unused files
    entry = set(macro.entry_points)
    unused = []
    for item in source:
        if item["fan_in"] or item["is_test"] or item["path"] in entry or item["is_entry"] or item["category"] != "code":
            continue
        if item["language"] not in ("Python", "JavaScript", "TypeScript", "Java", "Kotlin", "Go", "Rust", "C#"):
            continue
        if item["role"] in ("UI Page / Screen", "Migration / Schema Script", "Configuration", "CLI Command", "Build / Tooling Script", "Infrastructure / Deployment", "Entry Point", "Controller / Route Handler", "Background Job / Scheduler", "Event Consumer / Listener", "Dependency Wiring / Module"):
            continue
        name = PurePosixPath(item["path"]).name
        if name.startswith(("__init__", "index.", "conftest")) or name.endswith(".d.ts"):
            continue
        unused.append(item["path"])
    if unused:
        concerns.append(_concern("info", "Possibly Unused File", f"{len(unused)} source file(s) are never imported", unused[0],
                                 "No other file imports these. They may be dead code, framework-discovered (by naming convention), or loaded dynamically.",
                                 "\n".join(unused[:40])))

    # Duplicated code
    dup_lines, dup_pairs = _duplicates(source)
    total_code = sum(item["code_lines"] for item in source) or 1
    duplication = round(100 * dup_lines / total_code, 1)
    for (a, b), count in dup_pairs[:15]:
        concerns.append(_concern("warning" if count >= 30 else "info", "Duplicated Code", f"~{count} duplicated lines between two files", a,
                                 "Copy-pasted blocks drift apart over time; extract a shared function.", f"{a}\n{b}"))

    # Secrets
    secret_count = 0
    for item in files:
        text = item.get("text")
        if not text or item["category"] in ("doc",) or item["is_test"]:
            continue
        for label, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if label == "Hardcoded password" and PLACEHOLDER.match(match.group(2) or ""):
                    continue
                if label == "Connection string with password" and re.search(r"(?i)://[^:/\s]*:(pass|password|pwd|secret|changeme|\*+|xxx+|<[^>]*>|\$\{[^}]*\}|\{\w+\})@", match.group(0)):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                secret_count += 1
                concerns.append(_concern("critical", "Hardcoded Secret", f"Possible {label.lower()} in source", item["path"],
                                         "Credentials in source control leak to everyone with read access. Move them to a secret store and rotate them.",
                                         f"line {line} (value masked)"))
                break
        if secret_count > 40:
            break

    # Missing tests per container
    tests_by_container: dict[str, int] = defaultdict(int)
    source_by_container: dict[str, int] = defaultdict(int)
    for item in source:
        container = containers_by_file.get(item["path"], "")
        if item["is_test"]:
            tests_by_container[container] += 1
        elif item["category"] == "code":
            source_by_container[container] += 1
    for container, count in source_by_container.items():
        if count >= 5 and tests_by_container.get(container, 0) == 0:
            concerns.append(_concern("warning", "Missing Tests", f"No automated tests found in container '{container or 'root'}'", "",
                                     "Changes cannot be verified automatically.", f"{count} source files, 0 test files"))

    # Documentation drift
    for item in macro.drift:
        concerns.append(_concern("info", "Documentation Drift", f"README claims {item['claim']} but the manifests do not confirm it", "README.md", item["detail"], "Stage 2 macro discovery"))

    # TODO / FIXME debt
    todo_files = []
    todo_total = 0
    for item in source:
        text = item.get("text") or ""
        count = len(re.findall(r"\b(TODO|FIXME|HACK|XXX)\b", text))
        if count:
            todo_total += count
            todo_files.append((item["path"], count))
    if todo_total >= 10:
        todo_files.sort(key=lambda kv: -kv[1])
        concerns.append(_concern("info", "TODO Debt", f"{todo_total} TODO/FIXME markers", todo_files[0][0], "Unfinished work recorded in comments.",
                                 "\n".join(f"{path}: {count}" for path, count in todo_files[:15])))

    # Health score
    code_files = [item for item in source if item["category"] == "code" and not item["is_test"]]
    functions = [s for s in symbols if s["kind"] in ("function", "method", "constructor", "procedure")]
    avg_complexity = round(sum(s["complexity"] for s in functions) / len(functions), 2) if functions else 0.0
    cyclic_files = sum(len(component) for component in graph.sccs)
    test_files = sum(1 for item in source if item["is_test"])
    test_ratio = test_files / max(1, len(code_files))
    violation_edges = sum(len(items) for items in violations.values())
    god_modules = sum(1 for c in concerns if c["category"] == "God Module")
    penalties = {
        "cycles": min(20.0, 100 * cyclic_files / max(1, len(code_files)) * 0.6 + len(graph.sccs) * 1.5),
        "layering": min(15.0, violation_edges * 0.75),
        "god_modules": min(12.0, god_modules * 3.0),
        "complexity": min(15.0, max(0.0, (avg_complexity - 4) * 2.5) + len([s for s in complex_symbols if s["complexity"] > 30]) * 0.3),
        "duplication": min(12.0, duplication * 0.8),
        "secrets": min(15.0, secret_count * 5.0),
        "tests": 10.0 if test_ratio == 0 and len(code_files) >= 5 else max(0.0, 6.0 - test_ratio * 20),
        "unused": min(5.0, len(unused) * 0.25),
    }
    health = round(max(0.0, min(100.0, 100.0 - sum(penalties.values()))), 1)
    severity_counts = defaultdict(int)
    for concern in concerns:
        severity_counts[concern["severity"]] += 1
    metrics = {
        "health_score": health,
        "penalties": {key: round(value, 1) for key, value in penalties.items()},
        "avg_complexity": avg_complexity,
        "max_complexity": max((s["complexity"] for s in functions), default=0),
        "duplication_pct": duplication,
        "cycles": len(graph.sccs),
        "cyclic_files": cyclic_files,
        "layer_violations": violation_edges,
        "god_modules": god_modules,
        "secrets": secret_count,
        "test_files": test_files,
        "test_ratio": round(test_ratio, 3),
        "unused_files": len(unused),
        "todo_markers": todo_total,
        "concerns_by_severity": dict(severity_counts),
    }
    return concerns, metrics


def _suggest_cut(component: list[str], graph) -> str:
    """The edge inside a cycle whose removal breaks the most dependency paths (lowest-weight back edge heuristic)."""
    members = set(component)
    best = None
    for edge in graph.edges:
        if edge.source in members and edge.target in members:
            score = graph.fan_in.get(edge.source, 0) - graph.fan_in.get(edge.target, 0) - edge.weight * 0.1
            if best is None or score > best[0]:
                best = (score, edge)
    if not best:
        return ""
    return f"{best[1].source} → {best[1].target} ({best[1].kind})"


def _duplicates(files: list[dict], window: int = 6) -> tuple[int, list[tuple[tuple[str, str], int]]]:
    """jscpd-style clone detection with rolling hashes over normalised 6-line windows."""
    seen: dict[str, tuple[str, int]] = {}
    duplicated: set[tuple[str, int]] = set()
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for item in files:
        if item["is_test"] or item["category"] != "code" or item["is_generated"]:
            continue
        text = item.get("text") or ""
        lines = [re.sub(r"\s+", " ", line.strip()) for line in text.splitlines()]
        meaningful = [(index, line) for index, line in enumerate(lines) if len(line) > 3 and not re.match(r"^(import|from|using|package|#include|[{}()\[\];,]+|//|#|\*|/\*|\*/)", line)]
        for start in range(0, max(0, len(meaningful) - window + 1)):
            chunk = "\n".join(line for _, line in meaningful[start : start + window])
            digest = hashlib.blake2b(chunk.encode(), digest_size=10).hexdigest()
            line_no = meaningful[start][0]
            if digest in seen:
                other_path, _ = seen[digest]
                if other_path != item["path"]:
                    key = tuple(sorted((other_path, item["path"])))
                    pairs[key] += 1
                for offset in range(window):
                    duplicated.add((item["path"], meaningful[start + offset][0]))
            else:
                seen[digest] = (item["path"], line_no)
        if len(seen) > 3_000_000:
            break
    ranked = sorted(((pair, count + window - 1) for pair, count in pairs.items() if count >= 2), key=lambda kv: -kv[1])
    return len(duplicated), ranked
