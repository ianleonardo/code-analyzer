"""Deterministic impact engine for one pull request (or any two commits): diff × code graph × risk signals.

Inputs are the parsed diff plus two stored analyses (merge-base and head). Everything here is pure computation over
those facts, so the same commits always give the same result (RULESET_VERSION is stored with every run).

  classify_files      change kinds per file (code, test, config, infra, CI, manifest, migration, docs, generated)
  changed_symbols     hunks mapped onto symbols of both sides: added / removed / modified, signature and behaviour
                      tag changes (db-write, http-call, ...) and module-level changes
  propagate           symbol-level impact walk over the reverse dependency graph: a dependant is impacted only when its
                      code references an affected symbol (or inherits from it); the symbols that hold the reference
                      become the affected symbols of the next hop, so impact follows real usage, not just imports
  entry_points        endpoints, jobs, listeners, screens and main programs reached (direct, same file, transitive,
                      through catalogue links such as screen → endpoint), plus added / removed entry points
  test_impact         affected tests and changed functions no test reaches (gaps)
  graph_delta         added / removed edges, new layer violations, new cycles, new outbound behaviour, containers touched
  dependency_diff     added / removed / bumped packages with major bumps flagged
  risk                explainable score: every factor with its value, weight and contribution; label LOW → CRITICAL
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Callable

from app.services.analyzer import classify, config_impact
from app.services.analyzer.diff import FileDiff
from app.services.analyzer.graph import tarjan

RULESET_VERSION = "1.0"
FUNCTION_KINDS = ("function", "method", "constructor", "procedure", "paragraph", "subroutine")
TYPE_KINDS = ("class", "interface", "struct", "enum", "trait", "record", "object", "type", "module", "program")
ENTRY_KINDS = ("endpoint", "job", "listener", "screen")
LAYER_RANK = {"ui": 4, "api": 3, "service": 2, "data": 1}
MANIFESTS = {"package.json", "pom.xml", "build.gradle", "build.gradle.kts", "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "go.mod", "cargo.toml",
             "gemfile", "composer.json", "pipfile", "settings.gradle", "settings.gradle.kts", "packages.config", "directory.packages.props"}
SENSITIVE = re.compile(r"(auth|security|login|logout|password|passwd|credential|payment|billing|checkout|invoice|refund|crypto|encrypt|decrypt|cipher|token|session|"
                       r"permission|privilege|acl|rbac|oauth|saml|jwt|secret|migration|schema|ledger|transfer|account)", re.I)
STRONG_TAGS = {"db-write", "http-call", "messaging", "auth", "tx-scope"}
MAX_VISITS = 5000


# --- inputs -----------------------------------------------------------------------------------------------------


@dataclass
class Side:
    """One stored analysis, loaded for impact computation."""

    analysis_id: str
    files: dict[str, dict]  # path -> layer, role, is_test, is_source, is_entry, concepts, pagerank, container, language, complexity
    symbols: dict[str, list[dict]]  # path -> symbols
    edges: list[tuple[str, str, str]]  # source, target, kind
    content: Callable[[str], str]
    catalog: list[dict] = field(default_factory=list)  # item_key, kind, name, path, line_start, line_end, layer, details
    links: list[tuple[str, str, str]] = field(default_factory=list)  # source_key, target_key, kind
    concerns: list[dict] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)

    def reverse(self) -> dict[str, list[tuple[str, str]]]:
        out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for source, target, kind in self.edges:
            out[target].append((source, kind))
        return out

    def forward(self) -> dict[str, list[tuple[str, str]]]:
        out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for source, target, kind in self.edges:
            out[source].append((target, kind))
        return out


# --- change kinds -----------------------------------------------------------------------------------------------


def change_kinds(path: str, row: dict | None) -> list[str]:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    kinds: list[str] = []
    row = row or {}
    if row.get("is_test") or classify.TEST_PATTERN.search(path):
        kinds.append("test")
    if name.endswith((".md", ".rst", ".adoc", ".txt")) or path.startswith(("docs/", "doc/")):
        kinds.append("docs")
    if config_impact.is_ci(path):
        kinds.append("ci")
    elif config_impact.is_dockerfile(path) or config_impact.is_terraform(path) or re.search(r"(^|/)(k8s|kubernetes|helm|charts|deploy|deployment|manifests|kustomize|overlays)/", path) or re.match(r"^(docker-)?compose[\w.-]*\.ya?ml$", name):
        kinds.append("infra")
    if name in MANIFESTS or name.endswith((".csproj", ".fsproj", ".vbproj")):
        kinds.append("manifest")
    elif config_impact.is_config(path) and "infra" not in kinds:
        kinds.append("config")
    if re.search(r"(^|/)(migrations?|db/migrate|flyway|liquibase|changelog|alembic/versions)/|(^|/)V\d+(_\d+)*__\w+\.sql$", path, re.I):
        kinds.append("migration")
    elif name.endswith(".sql"):
        kinds.append("sql")
    if row.get("is_generated"):
        kinds.append("generated")
    if row.get("is_source") and not kinds:
        kinds.append("code")
    if not kinds:
        kinds.append("asset" if not row.get("is_source") else "code")
    return kinds


def classify_files(diffs: list[FileDiff], base: Side, head: Side) -> list[dict]:
    rows = []
    for fd in diffs:
        row = head.files.get(fd.path) if fd.status != "D" else base.files.get(fd.path)
        row = row or base.files.get(fd.old_path or fd.path) or {}
        kinds = change_kinds(fd.path, row)
        before_tags = set((base.files.get(fd.old_path or fd.path) or {}).get("concepts") or [])
        after_tags = set((head.files.get(fd.path) or {}).get("concepts") or []) if fd.status != "D" else set()
        rows.append({
            "path": fd.path, "old_path": fd.old_path, "status": fd.status, "additions": fd.additions, "deletions": fd.deletions, "binary": fd.binary,
            "layer": row.get("layer", ""), "role": row.get("role", ""), "language": row.get("language", ""), "container": row.get("container", ""),
            "change_kinds": kinds, "tags_added": sorted(after_tags - before_tags), "tags_removed": sorted(before_tags - after_tags),
            "sensitive": bool(SENSITIVE.search(fd.path)),
        })
    return rows


# --- symbols ----------------------------------------------------------------------------------------------------


def _norm_sig(signature: str) -> str:
    return re.sub(r"\s+", " ", (signature or "").strip())


def _sig_core(signature: str) -> str:
    """Signature without annotations / decorators and whitespace: parameter and return types are the contract."""
    text = re.sub(r"@[\w.]+(\([^()]*\))?", "", signature or "")
    return re.sub(r"\s+", "", text)


def _body(text: str, start: int, end: int, limit: int = 300) -> str:
    if not text or start <= 0:
        return ""
    lines = text.splitlines()
    end = max(start, min(end or start, start + limit - 1))
    return "\n".join(lines[start - 1:end])


def _tags(code: str) -> set[str]:
    return {name for name, pattern in classify.CONCEPTS if pattern.search(code or "")}


def changed_symbols(diffs: list[FileDiff], base: Side, head: Side, file_rows: dict[str, dict]) -> tuple[list[dict], dict[str, dict]]:
    """Returns (changed symbols, per-file facts {module_level, touched_new, touched_old})."""
    out: list[dict] = []
    facts: dict[str, dict] = {}
    for fd in diffs:
        row = file_rows.get(fd.path, {})
        if fd.binary or not ({"code", "test", "sql", "migration"} & set(row.get("change_kinds", []))):
            continue
        old_path = fd.old_path or fd.path
        head_syms = head.symbols.get(fd.path, []) if fd.status != "D" else []
        base_syms = base.symbols.get(old_path, []) if fd.status != "A" else []
        head_text = head.content(fd.path) if fd.status != "D" else ""
        base_text = base.content(old_path) if fd.status != "A" else ""
        new_lines, old_lines = fd.new_lines(), fd.old_lines()
        pure_deletions = {h.new_start for h in fd.hunks if h.new_count == 0}
        head_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for sym in head_syms:
            head_index[(sym["qualified_name"], sym["kind"])].append(sym)
        base_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for sym in base_syms:
            base_index[(sym["qualified_name"], sym["kind"])].append(sym)

        def member_ranges(syms: list[dict], parent: dict) -> list[tuple[int, int]]:
            return [(s["line_start"], s["line_end"]) for s in syms if s is not parent and s["line_start"] >= parent["line_start"] and s["line_end"] <= parent["line_end"]
                    and s["kind"] in FUNCTION_KINDS + TYPE_KINDS]

        def own_touch(sym: dict, lines: set[int], syms: list[dict], extra: set[int]) -> bool:
            start, end = sym["line_start"], sym["line_end"] or sym["line_start"]
            hits = {n for n in lines | extra if start <= n <= end}
            if not hits:
                return False
            if sym["kind"] in TYPE_KINDS:
                inner = member_ranges(syms, sym)
                hits = {n for n in hits if not any(a <= n <= b for a, b in inner)}
            return bool(hits)

        covered_new: set[int] = set()
        for sym in head_syms:
            if sym["kind"] in FUNCTION_KINDS + TYPE_KINDS:
                covered_new.update(range(sym["line_start"], (sym["line_end"] or sym["line_start"]) + 1))
        covered_old: set[int] = set()
        for sym in base_syms:
            if sym["kind"] in FUNCTION_KINDS + TYPE_KINDS:
                covered_old.update(range(sym["line_start"], (sym["line_end"] or sym["line_start"]) + 1))
        module_level = bool((new_lines - covered_new) and fd.status in ("M", "R")) or bool((old_lines - covered_old) and fd.status in ("M", "R"))
        facts[fd.path] = {"module_level": module_level or fd.status in ("A", "D"), "status": fd.status}

        matched_base: set[int] = set()
        for key, syms in head_index.items():
            befores = base_index.get(key, [])
            for i, sym in enumerate(syms):
                before = befores[i] if i < len(befores) else None
                if before is not None:
                    matched_base.add(id(before))
                if fd.status == "A" or before is None:
                    change = "added"
                elif own_touch(sym, new_lines, head_syms, pure_deletions) or own_touch(before, old_lines, base_syms, set()):
                    change = "modified"
                else:
                    continue
                after_body = _body(head_text, sym["line_start"], sym["line_end"])
                before_body = _body(base_text, before["line_start"], before["line_end"]) if before else ""
                if change == "modified" and before is not None and after_body.strip() == before_body.strip() and _norm_sig(sym["signature"]) == _norm_sig(before["signature"]):
                    continue  # only moved
                out.append(_symbol_change(fd.path, sym, before, change, before_body, after_body))
        for key, syms in base_index.items():
            for sym in syms:
                if id(sym) in matched_base:
                    continue
                if key in head_index and fd.status != "D" and len(head_index[key]) >= len(syms):
                    continue
                before_body = _body(base_text, sym["line_start"], sym["line_end"])
                out.append(_symbol_change(fd.path, None, sym, "removed", before_body, ""))
    # A type that only changed through its members is represented by the members.
    return out, facts


def _symbol_change(path: str, after: dict | None, before: dict | None, change: str, before_body: str, after_body: str) -> dict:
    sym = after or before or {}
    sig_before, sig_after = (before or {}).get("signature", ""), (after or {}).get("signature", "")
    exported = bool((after or {}).get("exported") or (before or {}).get("exported"))
    tags_before, tags_after = _tags(before_body), _tags(after_body)
    flags: list[str] = []
    contract_kind = sym.get("kind") in FUNCTION_KINDS + TYPE_KINDS + ("table", "view")
    if change == "modified" and _norm_sig(sig_before) != _norm_sig(sig_after):
        flags.append("signature")
    if exported:
        flags.append("exported")
    core_changed = "signature" in flags and _sig_core(sig_before) != _sig_core(sig_after)
    if contract_kind and ((change == "removed" and exported) or (core_changed and exported)):
        flags.append("contract")
    for tag in sorted(tags_after - tags_before):
        flags.append(f"+{tag}")
    for tag in sorted(tags_before - tags_after):
        flags.append(f"-{tag}")
    if SENSITIVE.search(sym.get("qualified_name", "")):
        flags.append("sensitive")
    if change == "modified":
        a_branches, b_branches = classify_branches(before_body), classify_branches(after_body)
        if a_branches != b_branches:
            flags.append(f"branches {a_branches}→{b_branches}")
        if _raises(before_body) != _raises(after_body):
            flags.append("error-handling")
    return {
        "path": path, "qualified_name": sym.get("qualified_name", ""), "name": sym.get("name", ""), "kind": sym.get("kind", ""), "parent": sym.get("parent", ""),
        "change_type": change, "signature_before": sig_before, "signature_after": sig_after, "exported": exported,
        "line_start": sym.get("line_start", 0), "line_end": sym.get("line_end", 0),
        "before_start": (before or {}).get("line_start", 0), "before_end": (before or {}).get("line_end", 0),
        "flags": flags, "tags_before": sorted(tags_before), "tags_after": sorted(tags_after),
        "content_hash": hashlib.sha256(f"{before_body}\0{after_body}".encode("utf-8", "replace")).hexdigest(),
        "before_body": before_body, "after_body": after_body,
    }


_BRANCH = re.compile(r"\b(if|elif|else if|case|when|catch|except|while|for|switch|\?\s*[^:]+:)\b")


def classify_branches(code: str) -> int:
    return len(_BRANCH.findall(code or ""))


def _raises(code: str) -> int:
    return len(re.findall(r"\b(raise|throw|throws|except|catch|rescue|panic|Err\()", code or ""))


# --- propagation ------------------------------------------------------------------------------------------------


def _word(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")


def _names_from(changes: list[dict]) -> set[str]:
    names = set()
    for change in changes:
        if change["kind"] in FUNCTION_KINDS + TYPE_KINDS and len(change["name"]) >= 3:
            names.add(change["name"])
            if change["kind"] == "constructor" and change.get("parent"):
                names.add(change["parent"])
    return names


def _containing_symbols(symbols: list[dict], lines: list[int]) -> list[dict]:
    hits = []
    for sym in symbols:
        if sym["kind"] not in FUNCTION_KINDS + TYPE_KINDS:
            continue
        if any(sym["line_start"] <= n <= (sym["line_end"] or sym["line_start"]) for n in lines):
            hits.append(sym)
    # innermost first: a method rather than its class
    hits.sort(key=lambda s: (s["line_end"] or s["line_start"]) - s["line_start"])
    chosen, taken = [], set()
    for sym in hits:
        inside = [n for n in lines if sym["line_start"] <= n <= (sym["line_end"] or sym["line_start"]) and n not in taken]
        if inside:
            chosen.append(sym)
            taken.update(inside)
    return chosen


def propagate(head: Side, base: Side, changes: list[dict], facts: dict[str, dict], file_rows: dict[str, dict], extra_seeds: dict[str, dict] | None = None,
              max_depth: int = 8) -> tuple[dict[str, dict], dict[str, list[tuple[int, int]]]]:
    """Symbol-level reverse walk. Returns (impacted path -> record, affected line ranges per file)."""
    reverse_head, reverse_base = head.reverse(), base.reverse()
    by_file: dict[str, list[dict]] = defaultdict(list)
    for change in changes:
        by_file[change["path"]].append(change)
    affected_names: dict[str, set[str]] = {}
    affected_ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    strength: dict[str, int] = {}
    queue: deque[tuple[str, int]] = deque()
    changed_paths = {row["path"] for row in file_rows.values()}
    for path, items in by_file.items():
        row = file_rows.get(path, {})
        if "test" in row.get("change_kinds", []):
            continue
        affected_names[path] = _names_from(items)
        for item in items:
            if item["change_type"] != "removed":
                affected_ranges[path].append((item["line_start"], item["line_end"] or item["line_start"]))
        contract = any("contract" in item["flags"] or item["change_type"] == "removed" for item in items)
        strength[path] = max_depth if contract else max(3, max_depth - 2)
        queue.append((path, 0))
    for path, fact in facts.items():
        if path in affected_names or "test" in file_rows.get(path, {}).get("change_kinds", []):
            continue
        if fact.get("module_level"):
            affected_names[path] = set()
            strength[path] = 2
            queue.append((path, 0))
    for path, seed in (extra_seeds or {}).items():
        # config readers: the key changed, so code that reads it behaves differently
        if path in affected_names:
            continue
        affected_names[path] = set(seed.get("names", []))
        affected_ranges[path] += seed.get("ranges", [])
        strength[path] = seed.get("depth", 4)
        queue.append((path, 0))
    impacted: dict[str, dict] = {}
    for path, seed in (extra_seeds or {}).items():
        if path not in changed_paths:
            impacted[path] = {"path": path, "depth": 1, "via_path": seed.get("via", ""), "via_kind": "READS_CONFIG", "symbols": seed.get("names", [])[:12],
                              "confidence": "high", "reason": seed.get("reason", "reads a changed configuration key")}
    visits = 0
    while queue and visits < MAX_VISITS:
        path, depth = queue.popleft()
        visits += 1
        names = affected_names.get(path, set())
        fact = facts.get(path, {})
        removed_file = fact.get("status") == "D"
        renamed_file = fact.get("status") == "R"
        dependants = list(reverse_base.get(path, [])) if removed_file else list(reverse_head.get(path, []))
        if renamed_file:
            old = file_rows.get(path, {}).get("old_path")
            dependants += [d for d in reverse_base.get(old, []) if d not in dependants]
        for dependant, kind in dependants:
            if dependant == path or dependant in changed_paths:
                continue  # changed files are seeds of their own
            text = head.content(dependant) if dependant in head.files else base.content(dependant)
            refs = sorted(n for n in names if _word(n).search(text or ""))
            confidence, reason, next_names, ranges = "", "", set(), []
            if refs:
                lines = [text.count("\n", 0, m.start()) + 1 for n in refs for m in list(_word(n).finditer(text))[:20]]
                holders = _containing_symbols(head.symbols.get(dependant, []) if dependant in head.files else base.symbols.get(dependant, []), lines)
                if holders:
                    next_names = {s["name"] for s in holders if len(s["name"]) >= 3}
                    ranges = [(s["line_start"], s["line_end"] or s["line_start"]) for s in holders]
                else:
                    exported = [s for s in head.symbols.get(dependant, []) if s.get("exported") and s["kind"] in FUNCTION_KINDS + TYPE_KINDS]
                    next_names = {s["name"] for s in exported if len(s["name"]) >= 3}
                    ranges = [(n, n) for n in lines[:20]]
                confidence = "high" if depth == 0 else "medium"
                reason = f"uses {', '.join(refs[:4])}{'…' if len(refs) > 4 else ''} from {PurePosixPath(path).name}"
            elif kind in ("EXTENDS", "IMPLEMENTS") and names:
                confidence, reason = "high", f"{kind.lower()} a changed type in {PurePosixPath(path).name}"
                next_names = {s["name"] for s in head.symbols.get(dependant, []) if s["kind"] in TYPE_KINDS}
            elif removed_file:
                confidence, reason = "high", f"depends on removed file {PurePosixPath(path).name}"
            elif renamed_file and dependant in reverse_base.get(file_rows.get(path, {}).get("old_path"), []):
                confidence, reason = "medium", f"depended on the old path of renamed {PurePosixPath(path).name}"
            elif fact.get("module_level") and depth == 0:
                confidence, reason = "low", f"imports {PurePosixPath(path).name} (module-level code changed)"
            else:
                continue
            existing = impacted.get(dependant)
            better = existing is None or existing["depth"] > depth + 1 or (existing["depth"] == depth + 1 and _conf_rank(confidence) < _conf_rank(existing["confidence"]))
            if better:
                impacted[dependant] = {"path": dependant, "depth": depth + 1, "via_path": path, "via_kind": kind, "symbols": refs[:12], "confidence": confidence, "reason": reason}
            elif refs:
                existing["symbols"] = sorted(set(existing["symbols"]) | set(refs))[:12]
            if ranges:
                affected_ranges[dependant] += ranges
            limit = strength.get(path, max_depth)
            if confidence in ("high", "medium") and depth + 1 < limit and next_names:
                merged = affected_names.get(dependant, set()) | next_names
                if merged != affected_names.get(dependant):
                    affected_names[dependant] = merged
                    strength[dependant] = limit
                    queue.append((dependant, depth + 1))
    for path in changed_paths:
        impacted.pop(path, None)
    for record in impacted.values():
        row = head.files.get(record["path"]) or base.files.get(record["path"]) or {}
        record.update(layer=row.get("layer", ""), role=row.get("role", ""), is_test=bool(row.get("is_test")), pagerank=float(row.get("pagerank") or 0),
                      container=row.get("container", ""))
    return impacted, affected_ranges


def _conf_rank(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


# --- entry points -----------------------------------------------------------------------------------------------

# Entry points the layer catalogue does not extract (it covers Spring / JAX-RS, Android, iOS, Express / NestJS / Next.js
# and React): Python, Go, .NET, PHP and Ruby routes, scheduled jobs, message listeners and CLI commands.
_CODE_ENTRY_RULES: list[tuple[str, str, re.Pattern, str]] = [
    ("Python", "endpoint", re.compile(r"@\s*(?:\w+\.)*(?P<obj>\w+)\.(?P<method>get|post|put|delete|patch|head|options|api_route|route|websocket)\s*\(\s*[\"'](?P<route>[^\"']*)[\"'](?P<rest>[^)\n]*)"), "FastAPI / Flask"),
    ("Python", "endpoint", re.compile(r"\b(?:re_)?path\s*\(\s*r?[\"'](?P<route>[^\"']*)[\"']\s*,\s*(?P<view>[\w.]+)"), "Django URLconf"),
    ("Python", "job", re.compile(r"@\s*(?:\w+\.)*(?:task|shared_task|periodic_task|scheduled_job|cron|job|schedule)\b(?P<rest>[^\n]*)"), "Celery / APScheduler"),
    ("Python", "listener", re.compile(r"@\s*(?:\w+\.)*(?:subscriber|consumer|agent|topic|on_message|listener|KafkaListener|handler)\s*\((?P<rest>[^\n]*)"), "message consumer"),
    ("Python", "cli", re.compile(r"@\s*(?:\w+\.)*command\s*\((?P<rest>[^\n]*)\)"), "Click / Typer"),
    ("Go", "endpoint", re.compile(r"\b(?:\w+)\.(?P<method>GET|POST|PUT|DELETE|PATCH|Get|Post|Put|Delete|Patch|HandleFunc|Handle)\s*\(\s*\"(?P<route>/[^\"]*)\""), "net/http / Gin / Echo / chi"),
    ("C#", "endpoint", re.compile(r"\[(?:Http(?P<method>Get|Post|Put|Delete|Patch))(?:\(\s*\"(?P<route>[^\"]*)\"\s*\))?\]|\.Map(?P<method2>Get|Post|Put|Delete|Patch)\s*\(\s*\"(?P<route2>[^\"]*)\""), "ASP.NET"),
    ("C#", "job", re.compile(r"class\s+\w+\s*:\s*BackgroundService|\[(?:AutomaticRetry|DisableConcurrentExecution)|RecurringJob\.AddOrUpdate"), "Hosted service / Hangfire"),
    ("PHP", "endpoint", re.compile(r"Route::(?P<method>get|post|put|delete|patch|any)\s*\(\s*['\"](?P<route>[^'\"]*)['\"]"), "Laravel"),
    ("Ruby", "endpoint", re.compile(r"^\s*(?P<method>get|post|put|patch|delete)\s+['\"](?P<route>/[^'\"]*)['\"]", re.M), "Rails / Sinatra"),
]
_LANG_ALIASES = {"Python": ("Python",), "Go": ("Go",), "C#": ("C#",), "PHP": ("PHP",), "Ruby": ("Ruby",)}


def code_entry_points(side: Side) -> list[dict]:
    """Entry points found directly in code, keyed stably by file + name so base and head can be compared."""
    out: dict[str, dict] = {}
    by_lang: dict[str, list[tuple[str, re.Pattern, str]]] = defaultdict(list)
    for language, kind, pattern, framework in _CODE_ENTRY_RULES:
        by_lang[language].append((kind, pattern, framework))
    for path, row in side.files.items():
        rules = by_lang.get(row.get("language", ""))
        if not rules or row.get("is_test") or not row.get("is_source"):
            continue
        text = side.content(path)
        if not text:
            continue
        prefix = ""
        if row["language"] == "Python":
            match = re.search(r"(?:APIRouter|Blueprint)\s*\([^)]*?(?:prefix|url_prefix)\s*=\s*[\"']([^\"']+)[\"']", text)
            prefix = match.group(1).rstrip("/") if match else ""
        symbols = [s for s in side.symbols.get(path, []) if s["kind"] in FUNCTION_KINDS + TYPE_KINDS]
        for kind, pattern, framework in rules:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                groups = match.groupdict()
                owner = next((s for s in symbols if line <= s["line_start"] <= line + 4), None) or next(
                    (s for s in sorted(symbols, key=lambda s: s["line_end"] - s["line_start"]) if s["line_start"] <= line <= (s["line_end"] or s["line_start"])), None)
                start, end = (owner["line_start"], owner["line_end"] or owner["line_start"]) if owner else (line, line)
                start = min(start, line)
                if kind == "endpoint":
                    method = (groups.get("method") or groups.get("method2") or "").upper()
                    route = groups.get("route") if groups.get("route") is not None else groups.get("route2") or ""
                    if method in ("ROUTE", "API_ROUTE", "HANDLEFUNC", "HANDLE", ""):
                        methods = re.search(r"methods\s*=\s*\[([^\]]*)\]", groups.get("rest") or "")
                        method = ",".join(m.strip(" '\"").upper() for m in methods.group(1).split(",")) if methods else ("ANY" if framework != "Django URLconf" else "ANY")
                    if framework == "Django URLconf":
                        view = groups.get("view") or ""
                        start, end = line, line
                        name = f"ANY /{route.lstrip('^').rstrip('$').lstrip('/')}" + (f" → {view}" if view else "")
                    else:
                        full = (prefix + "/" + route.lstrip("/")) if prefix else route
                        name = f"{method} {full if full.startswith('/') else '/' + full}"
                elif kind == "cli":
                    name = f"command {owner['name'] if owner else line}"
                else:
                    name = owner["qualified_name"] if owner else f"{PurePosixPath(path).name}:{line}"
                key = f"code:{kind}:{path}:{name}"
                if key in out:
                    continue
                out[key] = {"item_key": key, "kind": "job" if kind == "cli" else kind, "name": name, "path": path, "line_start": start, "line_end": end,
                            "layer": row.get("layer", ""), "framework": framework, "summary": "", "details": {}}
    return list(out.values())


def all_entries(side: Side) -> list[dict]:
    """Catalogue entry items plus entry points found in code that the catalogue does not cover."""
    items = [i for i in side.catalog]
    seen = {(i["path"], i.get("line_start", 0)) for i in items if i["kind"] in ENTRY_KINDS}
    for item in code_entry_points(side):
        if any(path == item["path"] and abs(line - item["line_start"]) <= 3 for path, line in seen):
            continue
        items.append(item)
    return items


def _overlaps(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    if start <= 0:
        return bool(ranges)
    end = end or start
    return any(a <= end and start <= b for a, b in ranges)


def entry_points(diffs: list[FileDiff], base: Side, head: Side, changes: list[dict], impacted: dict[str, dict], affected_ranges: dict[str, list[tuple[int, int]]],
                 file_rows: dict[str, dict]) -> list[dict]:
    by_path = {fd.path: fd for fd in diffs}
    changed_names: dict[str, set[str]] = defaultdict(set)
    for change in changes:
        changed_names[change["path"]] |= _names_from([change])
    head_items = {item["item_key"]: item for item in all_entries(head)}
    base_items = {item["item_key"]: item for item in all_entries(base)}
    results: dict[str, dict] = {}

    def add(item: dict, reach: str, depth: int, via: str, symbols: list[str], side: str = "head") -> None:
        key = item["item_key"]
        current = results.get(key)
        order = {"removed": 0, "added": 1, "direct": 2, "same file": 3, "transitive": 4, "linked": 5}
        if current and (order[current["reach"]] < order[reach] or (current["reach"] == reach and current["depth"] <= depth)):
            return
        results[key] = {"item_key": key, "kind": item["kind"], "name": item["name"], "path": item["path"], "line": item.get("line_start", 0), "layer": item.get("layer", ""),
                        "reach": reach, "depth": depth, "via": via, "symbols": symbols[:10], "side": side, "framework": item.get("framework", ""),
                        "summary": (item.get("summary") or "")[:300]}

    for key, item in head_items.items():
        if item["kind"] not in ENTRY_KINDS:
            continue
        path, start, end = item["path"], item.get("line_start", 0), item.get("line_end", 0)
        fd = by_path.get(path)
        if base_items and key not in base_items and fd is not None:
            add(item, "added", 0, path, [])
            continue
        if fd is not None:
            if fd.status == "A" or start <= 0 or fd.touches_new(start, end or start):
                add(item, "direct", 0, path, sorted(changed_names.get(path, set()))[:10])
                continue
            text = head.content(path)
            code = _body(text, start, end or start, 400)
            refs = sorted(n for n in changed_names.get(path, set()) if _word(n).search(code))
            if refs:
                add(item, "same file", 0, path, refs)
                continue
        record = impacted.get(path)
        if record and record["confidence"] in ("high", "medium") and _overlaps(affected_ranges.get(path, []), start, end):
            add(item, "transitive", record["depth"], record["via_path"], record.get("symbols", []))
    for key, item in base_items.items():
        if item["kind"] in ENTRY_KINDS and key not in head_items and (item["path"] in by_path or any(fd.old_path == item["path"] for fd in diffs)):
            add(item, "removed", 0, item["path"], [], side="base")
    # catalogue links: a screen that calls an affected endpoint, a job that runs an affected service, ...
    touched_items: dict[str, tuple[int, str]] = {}
    for key, item in head_items.items():
        path, start, end = item["path"], item.get("line_start", 0), item.get("line_end", 0)
        if not path:
            continue
        fd = by_path.get(path)
        if fd is not None and (fd.status == "A" or start <= 0 or fd.touches_new(start, end or start)):
            touched_items[key] = (0, path)
        elif path in impacted and impacted[path]["confidence"] in ("high", "medium") and _overlaps(affected_ranges.get(path, []), start, end):
            touched_items[key] = (impacted[path]["depth"], path)
    incoming: dict[str, list[str]] = defaultdict(list)
    for source, target, kind in head.links:
        if kind in ("calls", "submits", "runs", "uses", "renders", "implemented by", "delivers to", "accesses", "reads config", "redirects to"):
            incoming[target].append(source)
    frontier = deque((key, depth, 0) for key, (depth, _) in touched_items.items())
    seen = set(touched_items)
    while frontier:
        key, depth, hops = frontier.popleft()
        if hops >= 3:
            continue
        for source in incoming.get(key, []):
            if source in seen:
                continue
            seen.add(source)
            item = head_items.get(source)
            if not item:
                continue
            if item["kind"] in ENTRY_KINDS:
                add(item, "linked", depth + hops + 1, head_items.get(key, {}).get("name", key), [])
            frontier.append((source, depth, hops + 1))
    # main programs / CLI entry files without catalogue items
    for path, record in list(impacted.items()) + [(p, {"depth": 0, "via_path": p, "symbols": []}) for p in by_path]:
        row = head.files.get(path) or {}
        if row.get("is_entry") and not row.get("is_test") and not any(r["path"] == path for r in results.values()):
            results[f"file:{path}"] = {"item_key": f"file:{path}", "kind": "main", "name": PurePosixPath(path).name, "path": path, "line": 0, "layer": row.get("layer", ""),
                                       "reach": "direct" if path in by_path else "transitive", "depth": record["depth"], "via": record.get("via_path", ""),
                                       "symbols": record.get("symbols", [])[:10], "side": "head", "framework": "", "summary": ""}
    order = {"removed": 0, "added": 1, "direct": 2, "same file": 3, "transitive": 4, "linked": 5}
    return sorted(results.values(), key=lambda r: (order[r["reach"]], r["depth"], r["kind"], r["name"]))[:1000]


def match_features(entries: list[dict], features: list[dict]) -> None:
    """Adds feature_ids to each entry point. Patterns: {type: path|route|item|name, value}."""
    for entry in entries:
        ids = []
        route = re.sub(r"^[A-Z]+\s+", "", entry["name"]) if entry["kind"] == "endpoint" else ""
        for feature in features:
            for pattern in feature.get("patterns", []):
                ptype, value = pattern.get("type", "path"), str(pattern.get("value", "")).strip()
                if not value:
                    continue
                hit = (ptype == "path" and fnmatch(entry["path"], value)) or (ptype == "item" and entry["item_key"] == value) or (
                    ptype == "route" and route and (route == value or route.startswith(value.rstrip("/") + "/") or fnmatch(route, value))) or (
                    ptype == "name" and re.search(value, entry["name"], re.I) is not None if _valid_regex(value) else False)
                if hit:
                    ids.append(feature["feature_id"])
                    break
        entry["feature_ids"] = ids


def _valid_regex(value: str) -> bool:
    try:
        re.compile(value)
        return True
    except re.error:
        return False


# --- tests ------------------------------------------------------------------------------------------------------


def test_impact(head: Side, changes: list[dict], impacted: dict[str, dict], file_rows: dict[str, dict], max_depth: int = 6) -> dict:
    reverse = head.reverse()
    tests: dict[str, dict] = {}
    for row in file_rows.values():
        if "test" in row["change_kinds"] and row["status"] != "D":
            tests[row["path"]] = {"path": row["path"], "reason": "changed in this PR", "depth": 0}
    for record in impacted.values():
        if record["is_test"] and record["path"] not in tests:
            tests[record["path"]] = {"path": record["path"], "reason": record["reason"], "depth": record["depth"]}
    reach_cache: dict[str, dict[str, int]] = {}

    def tests_reaching(path: str) -> dict[str, int]:
        if path in reach_cache:
            return reach_cache[path]
        seen = {path: 0}
        queue = deque([path])
        found: dict[str, int] = {}
        while queue:
            node = queue.popleft()
            if seen[node] >= max_depth:
                continue
            for dependant, _kind in reverse.get(node, []):
                if dependant in seen:
                    continue
                seen[dependant] = seen[node] + 1
                if (head.files.get(dependant) or {}).get("is_test"):
                    found[dependant] = seen[dependant]
                else:
                    queue.append(dependant)
        reach_cache[path] = found
        return found

    covered, indirect, gaps = [], [], []
    for change in changes:
        row = file_rows.get(change["path"], {})
        if change["change_type"] == "removed" or change["kind"] not in FUNCTION_KINDS or "test" in row.get("change_kinds", []) or "code" not in row.get("change_kinds", []):
            continue
        reaching = tests_reaching(change["path"])
        naming = [t for t in reaching if _word(change["name"]).search(head.content(t) or "")] if len(change["name"]) >= 3 else []
        entry = {"path": change["path"], "symbol": change["qualified_name"], "change_type": change["change_type"]}
        if naming:
            covered.append({**entry, "tests": naming[:5]})
        elif reaching:
            indirect.append({**entry, "tests": sorted(reaching, key=lambda t: reaching[t])[:5]})
        else:
            gaps.append(entry)
        for test in naming or list(reaching)[:3]:
            tests.setdefault(test, {"path": test, "reason": f"reaches {change['qualified_name']}", "depth": reaching.get(test, 1)})
    total = len(covered) + len(indirect) + len(gaps)
    return {
        "tests": sorted(tests.values(), key=lambda t: (t["depth"], t["path"]))[:300], "covered": covered[:300], "indirect": indirect[:300], "gaps": gaps[:300],
        "functions": total, "gap_ratio": round(len(gaps) / total, 3) if total else 0.0,
    }


# --- graph delta ------------------------------------------------------------------------------------------------


def graph_delta(base: Side, head: Side, file_rows: dict[str, dict], impacted: dict[str, dict]) -> dict:
    changed = set(file_rows) | {row["old_path"] for row in file_rows.values() if row.get("old_path")}
    before = {(s, t, k) for s, t, k in base.edges if s in changed or t in changed}
    after = {(s, t, k) for s, t, k in head.edges if s in changed or t in changed}
    added, removed = sorted(after - before), sorted(before - after)
    violations = []
    for source, target, kind in added:
        if kind not in ("IMPORTS", "INJECTS", "CALLS"):
            continue
        a, b = head.files.get(source) or {}, head.files.get(target) or {}
        if a.get("is_test") or b.get("is_test"):
            continue
        la, lb = a.get("layer"), b.get("layer")
        if la in LAYER_RANK and lb in LAYER_RANK and LAYER_RANK[la] < LAYER_RANK[lb] or (la == "ui" and lb == "data"):
            violations.append({"source": source, "target": target, "kind": kind, "from_layer": la, "to_layer": lb,
                               "severity": "critical" if la == "data" and lb in ("api", "ui") else "warning"})

    def cycles(side: Side) -> set[tuple[str, ...]]:
        nodes = sorted(side.files)
        adjacency: dict[str, set[str]] = {n: set() for n in nodes}
        for s, t, _k in side.edges:
            if s in adjacency and t in adjacency:
                adjacency[s].add(t)
        return {tuple(sorted(c)) for c in tarjan(nodes, adjacency) if len(c) > 1}

    base_cycles, head_cycles = cycles(base), cycles(head)
    new_cycles = [list(c) for c in sorted(head_cycles - base_cycles) if set(c) & changed]
    resolved_cycles = [list(c) for c in sorted(base_cycles - head_cycles) if set(c) & changed]
    behaviour = []
    for row in file_rows.values():
        if {"test", "generated"} & set(row.get("change_kinds", [])):
            continue
        for tag in row.get("tags_added", []):
            if tag in STRONG_TAGS or tag in ("cache", "file-io", "env-access"):
                behaviour.append({"path": row["path"], "tag": tag, "change": "added"})
        for tag in row.get("tags_removed", []):
            if tag in STRONG_TAGS:
                behaviour.append({"path": row["path"], "tag": tag, "change": "removed"})
    containers: dict[str, dict] = {}
    for path, row in file_rows.items():
        name = row.get("container") or "(root)"
        containers.setdefault(name, {"container": name, "changed": 0, "impacted": 0})["changed"] += 1
    for record in impacted.values():
        name = record.get("container") or "(root)"
        containers.setdefault(name, {"container": name, "changed": 0, "impacted": 0})["impacted"] += 1
    return {
        "added_edges": [{"source": s, "target": t, "kind": k} for s, t, k in added[:300]], "removed_edges": [{"source": s, "target": t, "kind": k} for s, t, k in removed[:300]],
        "added_count": len(added), "removed_count": len(removed), "layer_violations": violations[:100], "new_cycles": new_cycles[:30], "resolved_cycles": resolved_cycles[:30],
        "behaviour": behaviour[:200], "containers": sorted(containers.values(), key=lambda c: -(c["changed"] + c["impacted"])),
    }


# --- dependencies / concerns ------------------------------------------------------------------------------------


def _major(version: str) -> int | None:
    match = re.search(r"(\d+)", version or "")
    return int(match.group(1)) if match else None


def dependency_diff(base: Side, head: Side) -> dict:
    def keyed(side: Side) -> dict[tuple[str, str], dict]:
        return {(d["ecosystem"], d["name"].lower()): d for d in side.dependencies}

    a, b = keyed(base), keyed(head)
    bumps = []
    for key in sorted(set(a) & set(b)):
        va, vb = a[key].get("version", ""), b[key].get("version", "")
        if va != vb:
            ma, mb = _major(va.lstrip("^~>=< ")), _major(vb.lstrip("^~>=< "))
            bumps.append({"name": b[key]["name"], "ecosystem": key[0], "before": va, "after": vb, "container": b[key].get("container", ""),
                          "major": ma is not None and mb is not None and mb != ma, "downgrade": ma is not None and mb is not None and mb < ma})
    return {
        "added": [{"name": b[k]["name"], "ecosystem": k[0], "version": b[k].get("version", ""), "scope": b[k].get("scope", ""), "container": b[k].get("container", "")} for k in sorted(set(b) - set(a))][:200],
        "removed": [{"name": a[k]["name"], "ecosystem": k[0], "version": a[k].get("version", ""), "container": a[k].get("container", "")} for k in sorted(set(a) - set(b))][:200],
        "changed": bumps[:200],
    }


def concern_diff(base: Side, head: Side, scope: set[str]) -> dict:
    def keyed(side: Side) -> dict[tuple[str, str, str], dict]:
        return {(c["category"], c["path"], c["title"]): c for c in side.concerns}

    a, b = keyed(base), keyed(head)
    new = [b[k] for k in sorted(set(b) - set(a))]
    resolved = [a[k] for k in sorted(set(a) - set(b))]
    return {"new": new[:200], "resolved": resolved[:200], "new_in_scope": [c for c in new if c["path"] in scope][:200]}


# --- risk -------------------------------------------------------------------------------------------------------

WEIGHTS = {
    "blast_radius": 0.16, "centrality": 0.08, "entry_points": 0.14, "layers": 0.05, "change_severity": 0.12, "configuration": 0.12, "test_gaps": 0.10,
    "sensitive": 0.08, "dependencies": 0.05, "size": 0.05, "new_concerns": 0.05,
}
SEVERITY_VALUE = {"critical": 1.0, "high": 0.7, "medium": 0.4, "low": 0.15, "info": 0.0}


def risk(file_rows: list[dict], changes: list[dict], impacted: dict[str, dict], entries: list[dict], tests: dict, config: dict, deps: dict, concerns: dict,
         head: Side, delta: dict, history: dict[str, dict] | None = None) -> dict:
    source_total = max(1, sum(1 for f in head.files.values() if f.get("is_source") and not f.get("is_test")))
    strong = [r for r in impacted.values() if not r["is_test"] and r["confidence"] in ("high", "medium")]
    code_changed = [f for f in file_rows if "code" in f["change_kinds"]]
    factors: list[dict] = []

    def factor(name: str, value: float, detail: str) -> None:
        value = max(0.0, min(1.0, value))
        factors.append({"factor": name, "value": round(value, 3), "weight": WEIGHTS[name], "contribution": round(value * WEIGHTS[name], 4), "detail": detail})

    factor("blast_radius", len(strong) / max(1.0, 0.15 * source_total), f"{len(strong)} impacted file(s) of {source_total} source files ({sum(1 for r in impacted.values() if r['confidence'] == 'low')} weak links not counted)")
    rank_share = sum((head.files.get(f["path"]) or {}).get("pagerank", 0) or 0 for f in code_changed) + sum(r.get("pagerank", 0) for r in strong)
    factor("centrality", rank_share * 4, f"changed and impacted files hold {rank_share * 100:.1f}% of the PageRank")
    direct = sum(1 for e in entries if e["reach"] in ("direct", "same file", "removed"))
    reached = len(entries)
    factor("entry_points", (direct * 2 + (reached - direct)) / 8, f"{reached} entry point(s) affected, {direct} directly or removed")
    layers = {f["layer"] for f in code_changed if f["layer"]} | {r["layer"] for r in strong if r["layer"]}
    factor("layers", (len(layers) - 1) / 3 if layers else 0, f"layers touched: {', '.join(sorted(layers)) or 'none'}")
    severity, why = 0.0, "documentation, tests or formatting only"
    kinds_of = {f["path"]: f["change_kinds"] for f in file_rows}
    for change in changes:
        if {"test", "generated"} & set(kinds_of.get(change["path"], [])):
            continue
        if "contract" in change["flags"]:
            severity, why = max(severity, 1.0), f"public contract changed: {change['qualified_name']}"
        elif any(flag in (f"+{t}" for t in STRONG_TAGS) for flag in change["flags"]):
            if severity < 0.8:
                severity, why = 0.8, f"new behaviour in {change['qualified_name']}: {', '.join(f for f in change['flags'] if f.startswith('+'))}"
        elif change["change_type"] in ("modified", "added") and change["kind"] in FUNCTION_KINDS and severity < 0.45:
            severity, why = 0.45, "function bodies changed"
    if any("migration" in f["change_kinds"] for f in file_rows):
        severity, why = max(severity, 0.8), "database migration included" if severity <= 0.8 else why
    factor("change_severity", severity, why)
    findings = config.get("findings", [])
    worst = max((SEVERITY_VALUE.get(f["severity"], 0) for f in findings), default=0.0)
    factor("configuration", worst + min(0.3, 0.03 * len(findings)) if findings else 0.0,
           f"{len(findings)} config / infra / CI finding(s)" + (f", worst {max(findings, key=lambda f: SEVERITY_VALUE.get(f['severity'], 0))['severity']}" if findings else ""))
    gap_ratio = tests.get("gap_ratio", 0.0)
    factor("test_gaps", gap_ratio if tests.get("functions") else 0.0, f"{len(tests.get('gaps', []))} of {tests.get('functions', 0)} changed function(s) reached by no test")
    sensitive_hits = [f["path"] for f in file_rows if f.get("sensitive")] + [c["qualified_name"] for c in changes if "sensitive" in c["flags"]]
    sensitive_hits += [f"config:{c['key']}" for c in config.get("keys", []) if c.get("category") in ("security", "database connection") or c.get("secret")]
    factor("sensitive", min(1.0, 0.4 + 0.15 * len(sensitive_hits)) if sensitive_hits else 0.0, ", ".join(sensitive_hits[:4]) or "no sensitive areas")
    major = [d for d in deps.get("changed", []) if d["major"]]
    factor("dependencies", min(1.0, 0.35 * len(major) + 0.1 * len(deps.get("added", [])) + 0.05 * len(deps.get("removed", []))),
           f"{len(deps.get('added', []))} added, {len(deps.get('removed', []))} removed, {len(major)} major bump(s)")
    lines = sum(f["additions"] + f["deletions"] for f in file_rows if "generated" not in f["change_kinds"])
    factor("size", lines / 1000, f"{lines} changed line(s) in {len(file_rows)} file(s)")
    new_concerns = concerns.get("new", [])
    factor("new_concerns", sum(SEVERITY_VALUE.get(c["severity"] if c["severity"] != "warning" else "medium", 0.15) for c in new_concerns) / 2,
           f"{len(new_concerns)} new concern(s) ({sum(1 for c in new_concerns if c['severity'] == 'critical')} critical); {len(delta.get('layer_violations', []))} new layer violation(s), {len(delta.get('new_cycles', []))} new cycle(s)")
    score = sum(f["contribution"] for f in factors)
    overrides = []
    if any(f["rule"] == "secret-committed" for f in findings):
        overrides.append(("HIGH", "a secret is committed in the diff"))
    if any(e["reach"] == "removed" and e["kind"] == "endpoint" for e in entries):
        overrides.append(("HIGH", "an endpoint is removed"))
    if any(f["severity"] == "critical" for f in findings):
        overrides.append(("HIGH", "a critical configuration finding"))
    label = label_for(score)
    order = ["LOW", "MODERATE", "HIGH", "CRITICAL"]
    for floor, reason in overrides:
        if order.index(label) < order.index(floor):
            label = floor
            score = max(score, 0.5)
    hotspots = []
    for path, stats in (history or {}).items():
        if stats["commits"] >= 5:
            hotspots.append({"path": path, "commits": stats["commits"]})
    return {"score": round(score, 3), "label": label, "factors": sorted(factors, key=lambda f: -f["contribution"]), "overrides": [r for _, r in overrides],
            "hotspots": sorted(hotspots, key=lambda h: -h["commits"])[:20]}


def label_for(score: float) -> str:
    return "LOW" if score < 0.25 else "MODERATE" if score < 0.5 else "HIGH" if score < 0.75 else "CRITICAL"


# --- reviewers --------------------------------------------------------------------------------------------------


def reviewers(file_rows: list[dict], codeowners: list[tuple[str, list[str]]], history: dict[str, dict], impacted: dict[str, dict]) -> list[dict]:
    from app.services.analyzer.diff import codeowners_for

    people: dict[str, dict] = {}
    for row in file_rows:
        for owner in codeowners_for(codeowners, row["path"]):
            entry = people.setdefault(owner, {"name": owner, "source": "CODEOWNERS", "files": [], "score": 0})
            entry["files"].append(row["path"])
            entry["score"] += 3
    for record in list(impacted.values())[:200]:
        if record["confidence"] != "high":
            continue
        for owner in codeowners_for(codeowners, record["path"]):
            entry = people.setdefault(owner, {"name": owner, "source": "CODEOWNERS", "files": [], "score": 0})
            if record["path"] not in entry["files"]:
                entry["files"].append(record["path"])
                entry["score"] += 1
    for path, stats in history.items():
        for author, count in stats["authors"].items():
            entry = people.setdefault(author, {"name": author, "source": "recent commits", "files": [], "score": 0})
            if entry["source"] == "CODEOWNERS" and "recent commits" not in entry["source"]:
                entry["source"] = "CODEOWNERS + recent commits"
            if path not in entry["files"]:
                entry["files"].append(path)
            entry["score"] += count
    ranked = sorted(people.values(), key=lambda p: -p["score"])[:12]
    for person in ranked:
        person["files"] = person["files"][:8]
    return ranked
