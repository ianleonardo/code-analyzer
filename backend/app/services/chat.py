"""Chat with Analyst: conversations grounded in a stored analysis (or a comparison of two), answered by the active LLM.

Every answer is built from the database only — repository facts, file profiles, concerns and code chunks found by
hybrid retrieval — so it works offline long after the clone is gone. Sessions and messages are stored per user.
"""

from __future__ import annotations

import json
import re
import uuid

from app import db
from app.config import CHAT_RULES
from app.errors import AppError
from app.permissions import can
from app.services import llm, llm_settings, retrieval

HISTORY_TURNS = 8
FACT_FILES = 12

GUARDRAIL_REFUSAL = (
    "I can only answer questions related to this project repository's codebase, architecture, and functionalities. "
    "Please ask a question about the analysed repository (for example: its business purpose, modules, files, APIs, "
    "data flows, dependencies, or code quality)."
)

_INJECTION_PATTERNS = (
    re.compile(r"\b(ignore|disregard|forget|bypass|override)\b.{0,50}\b(instruction|instructions|prompt|prompts|rule|rules|guardrail|guardrails|system)\b", re.I | re.S),
    re.compile(r"\b(reveal|show|print|repeat|dump)\b.{0,40}\b(system prompt|hidden prompt|initial instructions|developer message)\b", re.I | re.S),
    re.compile(r"\b(you are now|act as a|act as an|pretend to be|roleplay as)\b(?!.{0,25}\b(architect|analyst|reviewer|developer)\b)", re.I | re.S),
)

_OFF_TOPIC_PATTERNS = (
    re.compile(r"\b(tell me a joke|write a joke|say a joke|write a poem|write a haiku|write a song|write a story|write an essay|once upon a time)\b", re.I),
    re.compile(r"\b(weather in|weather forecast|temperature in|recipe for|how to cook|how to bake|how do i bake|how do i cook|bake a cake|cook pasta)\b", re.I),
    re.compile(r"\b(capital of|president of|prime minister of|king of|queen of|population of|who won the|world cup|super bowl|olympics)\b", re.I),
    re.compile(r"\b(horoscope|zodiac|astrology|stock price|bitcoin price|crypto price|medical advice|legal advice|tax advice)\b", re.I),
    re.compile(r"\b(solve this riddle|write a cover letter|write a resume|plan a trip|travel itinerary)\b", re.I),
)

_REPO_DOMAIN_TERMS = {
    "repo", "repository", "repositories", "code", "codebase", "codes", "project", "app", "application",
    "system", "systems", "service", "services", "module", "modules", "package", "packages", "container",
    "containers", "component", "components", "layer", "layers", "file", "files", "folder", "folders",
    "directory", "directories", "path", "paths", "class", "classes", "function", "functions", "method",
    "methods", "symbol", "symbols", "variable", "variables", "constant", "constants", "type", "types",
    "interface", "interfaces", "struct", "enum", "api", "apis", "endpoint", "endpoints", "route", "routes",
    "router", "controller", "controllers", "handler", "handlers", "request", "requests", "response",
    "responses", "http", "rest", "graphql", "grpc", "webhook", "database", "databases", "db", "table",
    "tables", "column", "columns", "schema", "schemas", "model", "models", "entity", "entities", "dao",
    "orm", "query", "queries", "sql", "migration", "migrations", "ui", "screen", "screens", "page", "pages",
    "view", "views", "form", "forms", "dialog", "modal", "state", "hook", "hooks", "props", "render",
    "flow", "flows", "architecture", "architectural", "c4", "diagram", "diagrams", "topology", "graph",
    "dependency", "dependencies", "depend", "depends", "dependent", "import", "imports", "export", "exports",
    "call", "calls", "caller", "callee", "inject", "injects", "cycle", "cycles", "circular", "scc",
    "violation", "violations", "concern", "concerns", "issue", "issues", "risk", "risks", "risky",
    "riskiest", "security", "secret", "secrets", "vulnerability", "vulnerabilities", "auth",
    "authentication", "authorisation", "authorization", "login", "session", "user", "users", "role",
    "roles", "permission", "permissions", "rbac", "token", "jwt", "ldap", "oauth", "test", "tests",
    "testing", "spec", "complexity", "duplication", "health", "score", "metric", "metrics", "pagerank",
    "central", "blast", "radius", "impact", "impacted", "fan", "entry", "entrypoint", "entrypoints",
    "main", "config", "configuration", "setting", "settings", "env", "environment", "deploy", "deployment",
    "docker", "ci", "pipeline", "build", "run", "runtime", "framework", "frameworks", "library", "libraries",
    "stack", "tech", "language", "languages", "version", "versions", "branch", "branches", "commit",
    "commits", "diff", "compare", "comparison", "left", "right", "side", "sides", "added", "removed",
    "changed", "change", "changes", "modified", "breaking", "purpose", "business", "capability",
    "capabilities", "feature", "features", "functionality", "functionalities", "domain", "logic",
    "workflow", "process", "data", "payload", "input", "output", "error", "errors", "exception",
    "exceptions", "log", "logs", "logging", "audit", "cache", "caching", "queue", "job", "jobs", "worker",
    "workers", "listener", "listeners", "event", "events", "client", "clients", "server", "backend",
    "frontend", "summary", "summarise", "summarize", "overview", "catalogue", "catalog", "inspector",
    "line", "lines", "loc", "unused", "dead", "todo", "god", "drift", "readme", "manifest",
    "implementation", "implemented", "work", "works", "handle", "handles",
}

_STOP_WORDS = {
    "the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "was", "were", "for", "on", "with",
    "what", "how", "does", "do", "did", "which", "where", "when", "why", "who", "whom", "whose",
    "this", "that", "these", "those", "it", "its", "be", "been", "being", "by", "as", "from", "at",
    "can", "could", "would", "should", "will", "shall", "may", "might", "must", "i", "we", "you", "he",
    "she", "they", "me", "us", "him", "her", "them", "my", "our", "your", "their", "show", "explain",
    "tell", "give", "write", "make", "create", "list", "describe", "about", "there", "any", "all",
    "some", "most", "more", "less", "many", "much", "please", "help", "into", "over", "under", "between",
}


def _ensure_schema() -> None:
    try:
        db.exec_sql("ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE")
    except Exception:
        pass


def _require_chat(user: dict) -> None:
    if not (can(user, "insights", "read") and can(user, "repositories", "read")):
        raise AppError(403, "Forbidden")


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _scope_title(scope_type: str, scope_id: str) -> str:
    if scope_type == "analysis":
        row = db.fetchone("SELECT r.name, a.branch, a.status FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id WHERE a.analysis_id = ?", [scope_id])
        if row is None:
            raise AppError(404, "Analysis not found")
        if row["status"] != "completed":
            raise AppError(409, "Chat is available once the analysis completes")
        return f"{row['name']}@{row['branch'] or '-'}"
    row = db.fetchone("SELECT title FROM comparisons WHERE comparison_id = ?", [scope_id])
    if row is None:
        raise AppError(404, "Comparison not found")
    return row["title"]


def _session(user: dict, session_id: str, require_owner: bool = False) -> dict:
    _ensure_schema()
    row = db.fetchone(
        "SELECT s.*, COALESCE(s.is_public, FALSE) AS is_public, u.username AS owner_name "
        "FROM chat_sessions s LEFT JOIN users u ON u.user_id = s.user_id "
        "WHERE s.session_id = ?",
        [session_id],
    )
    if row is None:
        raise AppError(404, "Conversation not found")
    is_owner = row["user_id"] == user["user_id"]
    is_public = bool(row.get("is_public"))
    if not is_owner and not is_public:
        raise AppError(404, "Conversation not found")
    if require_owner and not is_owner:
        raise AppError(403, "Only the conversation owner can modify or send messages in this conversation")
    row["is_public"] = is_public
    row["is_owner"] = is_owner
    row["owner_name"] = row.get("owner_name") or row["user_id"]
    return row


def list_sessions(user: dict, scope_type: str = "", scope_id: str = "") -> list[dict]:
    _require_chat(user)
    _ensure_schema()
    where, params = ["(s.user_id = ? OR COALESCE(s.is_public, FALSE) = TRUE)"], [user["user_id"]]
    if scope_type:
        where.append("s.scope_type = ?")
        params.append(scope_type)
    if scope_id:
        where.append("s.scope_id = ?")
        params.append(scope_id)
    rows = db.fetchall(
        f"SELECT s.*, COALESCE(s.is_public, FALSE) AS is_public, u.username AS owner_name "
        f"FROM chat_sessions s LEFT JOIN users u ON u.user_id = s.user_id "
        f"WHERE {' AND '.join(where)} ORDER BY s.updated_at DESC LIMIT 200",
        params,
    )
    for row in rows:
        row["is_public"] = bool(row.get("is_public"))
        row["is_owner"] = row["user_id"] == user["user_id"]
        row["owner_name"] = row.get("owner_name") or row["user_id"]
        row["message_count"] = int(db.scalar("SELECT count(*) AS c FROM chat_messages WHERE session_id = ?", [row["session_id"]]) or 0)
        try:
            row["scope_label"] = _scope_title(row["scope_type"], row["scope_id"])
        except AppError:
            row["scope_label"] = "(deleted)"
    return rows


def create_session(user: dict, body: dict) -> dict:
    _require_chat(user)
    _ensure_schema()
    scope_type = str(body.get("scope_type") or "analysis")
    if scope_type not in ("analysis", "comparison"):
        raise AppError(400, "Chat scope must be an analysis or a comparison")
    scope_id = str(body.get("scope_id") or "")
    label = _scope_title(scope_type, scope_id)
    session_id = str(uuid.uuid4())
    is_public = bool(body.get("is_public", False))
    now = db.now()
    db.insert(
        "chat_sessions",
        {"session_id": session_id, "user_id": user["user_id"], "scope_type": scope_type, "scope_id": scope_id,
         "title": str(body.get("title") or f"Chat · {label}")[:300], "is_public": is_public, "created_at": now, "updated_at": now},
    )
    return get_session(user, session_id)


def get_session(user: dict, session_id: str) -> dict:
    _require_chat(user)
    row = _session(user, session_id, require_owner=False)
    messages = db.fetchall("SELECT message_id, role, content, sources_json, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at", [session_id])
    for message in messages:
        message["sources"] = _loads(message.pop("sources_json"), [])
    row["messages"] = messages
    try:
        row["scope_label"] = _scope_title(row["scope_type"], row["scope_id"])
    except AppError:
        row["scope_label"] = "(deleted)"
    row["llm_configured"] = llm.configured()
    return row


def update_session(user: dict, session_id: str, body: dict) -> dict:
    _require_chat(user)
    _session(user, session_id, require_owner=True)
    if "title" in body and body.get("title") is not None:
        clean = " ".join(str(body.get("title") or "").split())[:300]
        if not clean:
            raise AppError(400, "Title is required")
        db.exec_sql("UPDATE chat_sessions SET title = ? WHERE session_id = ?", [clean, session_id])
    if "is_public" in body and body.get("is_public") is not None:
        db.exec_sql("UPDATE chat_sessions SET is_public = ? WHERE session_id = ?", [bool(body.get("is_public")), session_id])
    return get_session(user, session_id)


def rename_session(user: dict, session_id: str, title: str) -> dict:
    return update_session(user, session_id, {"title": title})


def delete_session(user: dict, session_id: str) -> None:
    _require_chat(user)
    _session(user, session_id, require_owner=True)
    db.exec_sql("DELETE FROM chat_messages WHERE session_id = ?", [session_id])
    db.exec_sql("DELETE FROM chat_sessions WHERE session_id = ?", [session_id])


# --- context building ----------------------------------------------------------------------------------------------


def _analysis_facts(analysis_id: str, label: str = "") -> str:
    row = db.fetchone("SELECT a.*, r.name AS repo_name, r.url FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id WHERE a.analysis_id = ?", [analysis_id])
    stats, macro = _loads(row["stats_json"], {}), _loads(row["macro_json"], {})
    containers = db.fetchall("SELECT name, path, kind, ecosystem, frameworks_json, file_count, loc, summary FROM analysis_containers WHERE analysis_id = ?", [analysis_id])
    concerns = db.fetchall(
        "SELECT severity, category, title, path FROM analysis_concerns WHERE analysis_id = ? ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END LIMIT 25",
        [analysis_id],
    )
    central = db.fetchall(
        "SELECT path, layer, role, summary, fan_in, fan_out FROM analysis_files WHERE analysis_id = ? AND is_source = TRUE AND is_test = FALSE ORDER BY pagerank DESC LIMIT 15",
        [analysis_id],
    )
    lines = [
        f"{label}REPOSITORY: {row['repo_name']} ({row['url'] or 'uploaded'}) · branch {row['branch'] or '-'} · commit {(row['commit_sha'] or '')[:10]} · analysed {row['finished_at']}",
        f"ARCHITECTURE STYLE: {row['architecture_style']} ({'; '.join(macro.get('style_evidence', [])[:4])})",
        f"BUSINESS PURPOSE: {macro.get('business_purpose') or 'not synthesised'}",
        f"SUMMARY: {(row['summary'] or '')[:2500]}",
        f"SIZE: {row['file_count']} files, {row['loc_total']} lines · {stats.get('symbols', 0)} symbols · {stats.get('edges', 0)} dependency edges",
        f"LANGUAGES (lines): {json.dumps(dict(list((stats.get('languages') or {}).items())[:8]))}",
        "FRAMEWORKS: " + ", ".join(f"{fw['name']} [{fw['category']}]" for fw in macro.get("frameworks", [])[:40]),
        "LAYERS (files): " + json.dumps(stats.get("layers", {})) + " · ROLES: " + json.dumps(dict(list((stats.get("roles") or {}).items())[:15])),
        f"HEALTH: {row['health_score']}/100 · cycles {stats.get('cycles', 0)} · layer violations {stats.get('layer_violations', 0)} · avg complexity {stats.get('avg_complexity', 0)} · duplication {stats.get('duplication_pct', 0)}% · tests {stats.get('test_files', 0)} files",
        "ENTRY POINTS: " + ", ".join(macro.get("entry_points", [])[:15]),
        "EXTERNAL SYSTEMS: " + ", ".join(f"{e['name']} ({e['type']})" for e in macro.get("external_systems", [])[:15]),
        "CONTAINERS:",
    ]
    for container in containers[:15]:
        frameworks = ", ".join(fw["name"] for fw in _loads(container["frameworks_json"], [])[:8])
        lines.append(f"- {container['name']} [{container['kind']}, {container['ecosystem']}] at /{container['path']} · {container['file_count']} files · {frameworks} · {container['summary'][:300]}")
    lines.append("MOST CENTRAL FILES (PageRank):")
    for item in central:
        lines.append(f"- {item['path']} [{item['layer']}/{item['role']}] fan-in {item['fan_in']}, fan-out {item['fan_out']}: {item['summary'][:220]}")
    lines.append("TOP CONCERNS:")
    for concern in concerns:
        lines.append(f"- [{concern['severity']}] {concern['category']}: {concern['title']} {('(' + concern['path'] + ')') if concern['path'] else ''}")
    for key in ("key_capabilities", "risks"):
        if macro.get(key):
            lines.append(f"{key.upper().replace('_', ' ')}: " + "; ".join(macro[key][:8]))
    lines += _catalog_overview(analysis_id)
    return "\n".join(lines)


def _catalog_overview(analysis_id: str) -> list[str]:
    """Compact inventory from the layer catalogue so list-style questions ("which APIs / tables / screens") are complete."""
    rows = db.fetchall("SELECT layer, kind, name, summary FROM analysis_catalog WHERE analysis_id = ? AND kind IN ('endpoint', 'table', 'screen', 'service', 'job', 'database', 'listener', 'api_client') ORDER BY layer, kind, name", [analysis_id])
    if not rows:
        return []
    out = ["LAYER CATALOGUE (detailed facts per item are in the excerpts tagged 'catalog'):"]
    limits = {"endpoint": 120, "table": 80, "screen": 60, "service": 60, "job": 30, "database": 10, "listener": 30, "api_client": 60}
    for kind, limit in limits.items():
        names = [r["name"] for r in rows if r["kind"] == kind]
        if names:
            out.append(f"- {kind} ({len(names)}): " + "; ".join(names[:limit]) + (" …" if len(names) > limit else ""))
    return out


def _profiles(analysis_id: str, paths: list[str]) -> str:
    if not paths:
        return ""
    marks = ", ".join("?" for _ in paths)
    rows = db.fetchall(
        f"SELECT path, layer, role, language, code_lines, complexity, fan_in, fan_out, concepts_json, summary FROM analysis_files WHERE analysis_id = ? AND path IN ({marks})",
        [analysis_id, *paths],
    )
    return "\n".join(
        f"- {r['path']} [{r['layer']}/{r['role']}, {r['language']}, {r['code_lines']} lines, complexity {r['complexity']}, fan-in {r['fan_in']}, fan-out {r['fan_out']}, concepts {', '.join(_loads(r['concepts_json'], []))}]: {r['summary'][:400]}"
        for r in rows
    )


def _mentioned_paths(analysis_id: str, question: str) -> list[str]:
    candidates = re.findall(r"[\w@./-]+\.\w{1,6}", question)
    found: list[str] = []
    for candidate in candidates[:6]:
        row = db.fetchone(
            "SELECT path FROM analysis_files WHERE analysis_id = ? AND (path = ? OR path LIKE ?) ORDER BY length(path) LIMIT 1",
            [analysis_id, candidate, f"%{candidate}"],
        )
        if row:
            found.append(row["path"])
    return found


def _context_for(analysis_id: str, question: str, label: str = "", budget_chunks: int = 10) -> tuple[str, list[dict]]:
    facts = _analysis_facts(analysis_id, label)
    hits = retrieval.search(analysis_id, question, top=budget_chunks)
    mentioned = _mentioned_paths(analysis_id, question)
    profile_paths = list(dict.fromkeys(mentioned + [c["path"] for c in hits["chunks"]]))[:FACT_FILES]
    neighbour_lines = [f"- {e['source_path']} --{e['kind']}--> {e['target_path']}" for e in hits["neighbours"][:40]]
    excerpts = []
    sources = []
    for chunk in hits["chunks"]:
        excerpts.append(f"# {chunk['path']} :: {chunk['scope']} (lines {chunk['line_start']}-{chunk['line_end']})\n{chunk['content'][:3500]}")
        sources.append({"analysis_id": analysis_id, "path": chunk["path"], "scope": chunk["scope"], "line_start": chunk["line_start"], "line_end": chunk["line_end"], "score": chunk["score"]})
    for path in mentioned:
        if not any(s["path"] == path for s in sources):
            content = db.fetchone(
                "SELECT c.content FROM analysis_file_contents c JOIN analysis_files f ON f.file_id = c.file_id WHERE f.analysis_id = ? AND f.path = ?",
                [analysis_id, path],
            )
            if content:
                excerpts.insert(0, f"# {path} (full file, first 6000 chars)\n{content['content'][:6000]}")
                sources.insert(0, {"analysis_id": analysis_id, "path": path, "scope": "(file)", "line_start": 1, "line_end": 0, "score": 1})
    context = (
        f"{facts}\n\n{label}RELEVANT FILES:\n{_profiles(analysis_id, profile_paths)}\n\n{label}DEPENDENCY NEIGHBOURHOOD:\n" + "\n".join(neighbour_lines) +
        f"\n\n{label}CODE EXCERPTS (retrieval: {hits['mode']}):\n" + "\n\n".join(excerpts)
    )
    return context, sources


def _history(session_id: str) -> str:
    rows = db.fetchall("SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?", [session_id, HISTORY_TURNS])
    rows.reverse()
    return "\n".join(f"{row['role'].upper()}: {row['content'][:1500]}" for row in rows)


def _is_off_topic_question(question: str, analysis_ids: list[str], context: str, history: str) -> bool:
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(question):
            return True
    for pattern in _OFF_TOPIC_PATTERNS:
        if pattern.search(question):
            return True
    words = [w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]*", question)]
    meaningful = [w for w in words if w not in _STOP_WORDS and len(w) >= 2]
    if not meaningful:
        return True
    if any(w in _REPO_DOMAIN_TERMS or "." in w or "/" in w for w in meaningful):
        return False
    if any(bool(retrieval._index(aid).bm25(question, top=1)) for aid in analysis_ids if aid):
        return False
    context_lower = context.lower()
    if any(w in context_lower for w in meaningful if len(w) >= 3):
        return False
    if history and len(meaningful) <= 3:
        return False
    return True


def ask(user: dict, session_id: str, content: str) -> dict:
    _require_chat(user)
    session = _session(user, session_id, require_owner=True)
    question = str(content or "").strip()
    if not question:
        raise AppError(400, "Ask a question")
    if len(question) > 8000:
        raise AppError(400, "Questions must be 8000 characters or fewer")
    history = _history(session_id)
    if session["scope_type"] == "analysis":
        analysis_ids = [session["scope_id"]]
        context, sources = _context_for(session["scope_id"], question)
    else:
        comparison = db.fetchone("SELECT * FROM comparisons WHERE comparison_id = ?", [session["scope_id"]])
        if comparison is None:
            raise AppError(404, "Comparison not found")
        analysis_ids = [comparison["left_analysis_id"], comparison["right_analysis_id"]]
        result = _loads(comparison["result_json"], {})
        left_context, left_sources = _context_for(comparison["left_analysis_id"], question, "LEFT ", 6)
        right_context, right_sources = _context_for(comparison["right_analysis_id"], question, "RIGHT ", 6)
        summary = {k: result.get(k) for k in ("metrics", "stack", "dependency_similarity_pct", "structure")}
        summary["files"] = {"counts": result.get("files", {}).get("counts"), "content_similarity_pct": result.get("files", {}).get("content_similarity_pct")}
        summary["concerns"] = {"new": result.get("concerns", {}).get("new", [])[:15], "resolved": result.get("concerns", {}).get("resolved", [])[:15]}
        context = (
            f"COMPARISON: {comparison['title']}\nCOMPARISON RESULT:\n{json.dumps(summary, default=str)[:15000]}\n"
            f"NARRATIVE:\n{comparison['narrative'][:4000]}\n\n=== LEFT SIDE ===\n{left_context}\n\n=== RIGHT SIDE ===\n{right_context}"
        )
        sources = [{**s, "side": "left"} for s in left_sources] + [{**s, "side": "right"} for s in right_sources]

    now = db.now()
    db.insert("chat_messages", {"message_id": str(uuid.uuid4()), "session_id": session_id, "role": "user", "content": question, "sources_json": "[]", "created_at": now})
    if _is_off_topic_question(question, analysis_ids, context, history):
        answer = GUARDRAIL_REFUSAL
        sources = []
    elif llm.configured():
        system = f"{llm_settings.settings_row().get('global_system_prompt', '')}\n\n{CHAT_RULES}"
        prompt = (
            f"{context}\n\nPREVIOUS CONVERSATION:\n{history or 'none'}\n\n"
            f"GUARDRAIL: Answer ONLY if the QUESTION is about the analysed project repository, its code, architecture, or functionalities. "
            f"If the QUESTION is unrelated to this repository, refuse with: \"{GUARDRAIL_REFUSAL}\"\n\n"
            f"QUESTION:\n{question}"
        )
        try:
            answer = llm.complete(system, prompt, 0.2).strip() or "The model returned an empty answer."
        except AppError as exc:
            answer = f"⚠️ {exc.detail}\n\n" + _offline_answer(sources)
    else:
        answer = "No LLM is configured, so here is what retrieval found in the stored analysis. Configure Gemini or Spirare under **Settings › LLM** for full answers.\n\n" + _offline_answer(sources)
    db.insert(
        "chat_messages",
        {"message_id": str(uuid.uuid4()), "session_id": session_id, "role": "assistant", "content": answer, "sources_json": json.dumps(sources[:16]), "created_at": db.now()},
    )
    title = session["title"]
    if title.startswith("Chat · ") and not db.scalar("SELECT count(*) AS c FROM chat_messages WHERE session_id = ? AND role = 'assistant'", [session_id]) > 1:
        title = question[:80] + ("…" if len(question) > 80 else "")
    db.exec_sql("UPDATE chat_sessions SET updated_at = ?, title = ? WHERE session_id = ?", [db.now(), title, session_id])
    return get_session(user, session_id)


def _offline_answer(sources: list[dict]) -> str:
    if not sources:
        return "No matching code was found for this question."
    lines = ["**Most relevant code locations:**"]
    for source in sources[:10]:
        side = f"[{source['side']}] " if source.get("side") else ""
        scope = "" if source["scope"].startswith("lines ") else f"{source['scope']} "
        lines.append(f"- {side}`{source['path']}` — {scope}(lines {source['line_start']}-{source['line_end']})")
    return "\n".join(lines)
