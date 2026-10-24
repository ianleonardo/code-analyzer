"""Runs one analysis end to end and stores every result in the database for offline viewing and chat.

Stage 1  Ingestion           clone / extract / mount, hygiene gates, SHA-256 hashing, Merkle root
Stage 2  Macro discovery     manifests, frameworks, containers, architecture style, docs vs manifest drift
Stage 3  Micro parsing       symbols, imports, calls, complexity, line counts
Stage 4  Code graph          IMPORTS / EXTENDS / IMPLEMENTS / INJECTS / CALLS, Tarjan SCC, PageRank
Stage 5  Classification      layers, file roles, concept tags, quality audit, health score
Stage 6  Synthesis           LLM summaries bottom-up over the condensed DAG (cached by content hash), C4 diagrams
Stage 7  Layer catalogue     endpoints, services, data, screens and shared items with cross-layer links
Stage 8  Indexing            content-addressed chunks + embeddings (reused when unchanged)
Stage 9  Saving results      everything above is written to the database
"""

from __future__ import annotations

import json
import logging
import re
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from app import config, db
from app.errors import AppError
from app.services import git_credentials, git_hosts, llm, llm_settings
from app.services.analyzer import (
    c4,
    chunks,
    classify,
    graph,
    ingest,
    macro,
    parsers,
    quality,
    synthesis,
)
from app.services.analyzer.languages import count_lines

logger = logging.getLogger(__name__)
REMOTE_SOURCES = (git_hosts.GITHUB, git_hosts.BITBUCKET, git_hosts.BITBUCKET_SERVER, git_hosts.GIT)
PARSED_CATEGORIES = ("code", "markup", "script")
STORED_CONTENT_CATEGORIES = ("code", "markup", "script", "style", "config", "doc")


class Cancelled(Exception):
    pass


class Run:
    def __init__(self, analysis_id: str, is_cancelled):
        self.analysis_id = analysis_id
        self.is_cancelled = is_cancelled
        self._last_progress_write = 0.0
        self.stage = "queued"

    def check(self) -> None:
        if self.is_cancelled(self.analysis_id):
            raise Cancelled()

    def log(self, message: str, progress: float | None = None, level: str = "info", stage: str | None = None) -> None:
        if stage:
            self.stage = stage
        db.insert(
            "analysis_events",
            {"event_id": str(uuid.uuid4()), "analysis_id": self.analysis_id, "ts": db.now(), "stage": self.stage, "level": level, "message": message[:2000]},
        )
        self.progress(progress, message)

    def progress(self, value: float | None, message: str | None = None, force: bool = True) -> None:
        now = time.time()
        if not force and now - self._last_progress_write < 1.0:
            return
        self._last_progress_write = now
        sets, params = ["stage = ?"], [self.stage]
        if value is not None:
            sets.append("progress = ?")
            params.append(round(min(100.0, max(0.0, value)), 1))
        if message is not None:
            sets.append("message = ?")
            params.append(message[:500])
        params.append(self.analysis_id)
        db.exec_sql(f"UPDATE analyses SET {', '.join(sets)} WHERE analysis_id = ?", params)


def _uploads_dir(repo_id: str) -> Path:
    return config.data_dir() / "uploads" / repo_id


def _prepare_source(run: Run, analysis: dict, repo: dict, scratch: Path) -> Path:
    source = repo["source_type"]
    if analysis.get("source_dir"):
        # Prepared by a PR impact run: one commit exported from the PR workspace with `git archive` (no .git, no credentials).
        root = Path(analysis["source_dir"])
        if not root.is_dir():
            raise AppError(400, "The prepared commit snapshot is missing; run the impact analysis again.")
        run.log(f"Reading commit {(analysis.get('commit_sha') or '')[:10]} exported from the pull-request workspace", 8)
        return root
    if source in REMOTE_SOURCES:
        ref = git_hosts.parse_url(repo["url"])
        credential = git_credentials.credential_for(analysis["started_by"], ref)
        branch = analysis["branch"] or repo["default_branch"]
        run.log(f"Cloning {ref.name} @ {branch or 'default branch'} (shallow, depth 1){' with your ' + git_hosts.PROVIDER_LABELS[ref.provider] + ' connection' if credential else ''}", 2)
        facts = git_hosts.clone(ref, credential, branch, scratch / "repo")
        db.exec_sql(
            "UPDATE analyses SET commit_sha = ?, commit_author = ?, commit_date = ?, commit_message = ? WHERE analysis_id = ?",
            [facts["commit_sha"], facts["commit_author"][:250], facts["commit_date"][:60], facts["commit_message"][:2000], run.analysis_id],
        )
        run.log(f"Checked out commit {facts['commit_sha'][:10]} — {facts['commit_message'][:120]}", 8)
        return scratch / "repo"
    if source == "zip":
        archives = sorted(_uploads_dir(repo["repo_id"]).glob(f"{analysis['analysis_id']}.*")) or sorted(_uploads_dir(repo["repo_id"]).glob("latest.*"))
        if not archives:
            raise AppError(400, "The uploaded archive is missing; upload it again.")
        run.log(f"Extracting archive {archives[0].name} with Zip-Slip and decompression-bomb guards", 3)
        return ingest.extract_archive(archives[0], scratch / "repo")
    if source == "local":
        archives = sorted(_uploads_dir(repo["repo_id"]).glob(f"{analysis['analysis_id']}.*")) or sorted(_uploads_dir(repo["repo_id"]).glob("latest.*"))
        if archives:
            run.log(f"Extracting uploaded local folder {repo['name']}", 3)
            return ingest.extract_archive(archives[0], scratch / "repo")
        root = ingest.check_local_path(repo["url"])
        run.log(f"Reading local folder {root} (read-only)", 3)
        return root
    raise AppError(400, f"Unknown source type {source}")


def _container_of(path: str, container_paths: list[str]) -> str:
    best = ""
    for candidate in container_paths:
        if candidate and (path == candidate or path.startswith(candidate + "/")) and len(candidate) > len(best):
            best = candidate
    return best


def execute(analysis_id: str, is_cancelled=lambda _id: False) -> None:
    analysis = db.fetchone("SELECT * FROM analyses WHERE analysis_id = ?", [analysis_id])
    if analysis is None:
        return
    repo = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [analysis["repo_id"]])
    run = Run(analysis_id, is_cancelled)
    db.exec_sql("UPDATE analyses SET status = 'running', started_at = ?, error = '' WHERE analysis_id = ?", [db.now(), analysis_id])
    scratch = config.workspace_dir() / analysis_id
    started = time.time()
    try:
        if repo is None:
            raise AppError(404, "Repository not found")
        _run_stages(run, analysis, repo, scratch, started)
    except Cancelled:
        _clear_results(analysis_id)
        db.exec_sql("UPDATE analyses SET status = 'cancelled', finished_at = ?, message = 'Cancelled by user' WHERE analysis_id = ?", [db.now(), analysis_id])
        run.log("Analysis cancelled", None, "warn")
    except AppError as exc:
        _fail(run, analysis_id, exc.detail)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Analysis %s failed", analysis_id)
        _fail(run, analysis_id, f"{exc.__class__.__name__}: {exc}", traceback.format_exc()[-3000:])
    finally:
        if repo is None or repo["source_type"] != "local":
            ingest.cleanup(scratch)


def _fail(run: Run, analysis_id: str, message: str, detail: str = "") -> None:
    _clear_results(analysis_id)
    db.exec_sql("UPDATE analyses SET status = 'failed', finished_at = ?, error = ?, message = ? WHERE analysis_id = ?", [db.now(), (message + ("\n" + detail if detail else ""))[:8000], message[:500], analysis_id])
    try:
        run.log(f"Failed: {message}", None, "error")
    except Exception:  # noqa: BLE001
        logger.exception("Could not log failure")


def _clear_results(analysis_id: str) -> None:
    for table in db.ANALYSIS_TABLES:
        if table == "analysis_events":
            continue
        db.exec_sql(f"DELETE FROM {table} WHERE analysis_id = ?", [analysis_id])


def _previous(repo_id: str, branch: str, exclude: str) -> dict | None:
    return db.fetchone(
        "SELECT * FROM analyses WHERE repo_id = ? AND branch = ? AND status = 'completed' AND analysis_id <> ? AND coalesce(purpose, 'branch') = 'branch' ORDER BY finished_at DESC LIMIT 1",
        [repo_id, branch, exclude],
    )


def _run_stages(run: Run, analysis: dict, repo: dict, scratch: Path, started: float) -> None:
    analysis_id = analysis["analysis_id"]
    # ---------------- Stage 1: ingestion ----------------
    run.log("Stage 1: multi-source ingestion and workspace normalisation", 1, stage="Stage 1 · Ingestion")
    root = _prepare_source(run, analysis, repo, scratch)
    run.check()
    workspace = ingest.walk(root, lambda: run.is_cancelled(analysis_id), lambda n: run.progress(10 + min(4, n / 2000), f"Walking files… {n} kept", force=False))
    text_files = workspace.text_files
    skipped_total = sum(workspace.skipped.values())
    run.log(f"Kept {len(workspace.files)} files ({len(text_files)} readable text) · skipped {skipped_total} by hygiene gates · Merkle root {workspace.merkle_root[:12]}…", 15)
    previous = _previous(repo["repo_id"], analysis["branch"], analysis_id)
    if previous and previous["merkle_root"] == workspace.merkle_root:
        run.log(f"Merkle root matches the previous analysis ({previous['analysis_id'][:8]}); cached summaries and embeddings will be reused for every file", 15)
    elif previous:
        run.log("Merkle root changed since the previous analysis; only changed files need new LLM summaries and embeddings", 15)
    run.check()

    # ---------------- Stage 2: macro discovery ----------------
    run.log("Stage 2: deterministic macro discovery (manifests, frameworks, containers, docs)", 16, stage="Stage 2 · Macro discovery")
    macro_result = macro.discover(workspace.files)
    run.log(
        f"Architecture: {macro_result.architecture_style} · {len(macro_result.containers)} container(s) · "
        f"{len(macro_result.frameworks)} framework(s): {', '.join(fw['name'] for fw in macro_result.frameworks[:8])}",
        22,
    )
    run.check()

    # ---------------- Stage 3: micro parsing ----------------
    run.log("Stage 3: syntactic extraction of symbols, imports, calls and complexity", 23, stage="Stage 3 · Micro parsing")
    parsed: dict[str, parsers.ParseResult] = {}
    languages: dict[str, str] = {}
    line_counts: dict[str, tuple[int, int, int]] = {}
    parse_modes: dict[str, int] = {}
    parse_errors = 0
    total = max(1, len(text_files))
    for index, item in enumerate(text_files):
        if index % 200 == 0:
            run.check()
            run.progress(23 + 22 * index / total, f"Parsing {index}/{total} files…", force=False)
        languages[item.path] = item.language
        line_counts[item.path] = count_lines(item.text or "", item.language)
        if item.category in PARSED_CATEGORIES:
            try:
                result = parsers.parse(item.text or "", item.language)
            except Exception:  # noqa: BLE001 - a parser bug must not stop the analysis
                logger.exception("Parser failed for %s", item.path)
                result = parsers.ParseResult(errors=1)
            parsed[item.path] = result
            parse_modes[result.parse_mode] = parse_modes.get(result.parse_mode, 0) + 1
            parse_errors += result.errors
    symbol_total = sum(len(result.symbols) for result in parsed.values())
    run.log(f"Parsed {len(parsed)} source files · {symbol_total} symbols · {sum(len(r.imports) for r in parsed.values())} import statements", 45)
    run.check()

    # ---------------- Stage 4: graph ----------------
    run.log("Building the code context graph (IMPORTS, EXTENDS, IMPLEMENTS, INJECTS, CALLS)", 46, stage="Stage 4 · Code graph")
    go_modules = {}
    for item in workspace.files:
        if PurePosixPath(item.path).name == "go.mod" and item.text:
            match = re.search(r"^module\s+(\S+)", item.text, re.M)
            if match:
                parent = str(PurePosixPath(item.path).parent)
                go_modules["" if parent == "." else parent] = match.group(1)
    graph_result = graph.build(parsed, languages, macro_result.path_aliases, go_modules)
    run.log(
        f"{len(graph_result.edges)} edges · {graph_result.resolved} imports resolved, {graph_result.unresolved} unresolved · "
        f"Tarjan SCC: {len(graph_result.sccs)} cycle(s) condensed into super-nodes",
        55,
    )
    run.check()

    # ---------------- Stage 5: classification & quality ----------------
    run.log("Classifying layers, file roles and behavioural concepts", 56, stage="Stage 5 · Classification")
    container_paths = [c.path for c in macro_result.containers]
    containers_by_file = {item.path: _container_of(item.path, container_paths) for item in workspace.files}
    entry_points = set(macro_result.entry_points)
    files: dict[str, dict] = {}
    symbols: list[dict] = []
    for item in workspace.files:
        result = parsed.get(item.path)
        code, comment, blank = line_counts.get(item.path, (0, 0, 0))
        text = item.text or ""
        is_source = item.text is not None and item.category in ("code", "markup", "style", "script")
        cls = classify.classify(item.path, item.language, item.category, text, result) if item.text is not None else classify.Classification("shared", {"binary": "Binary Asset", "lockfile": "Lockfile"}.get(item.category, "Asset"), 0.9)
        file_symbols = []
        if result:
            for symbol in result.symbols:
                file_symbols.append({
                    "symbol_id": str(uuid.uuid4()), "path": item.path, "name": symbol.name[:500],
                    "qualified_name": symbol.qualified[:1000], "kind": symbol.kind, "parent": symbol.parent[:500],
                    "line_start": symbol.line_start, "line_end": symbol.line_end, "complexity": symbol.complexity,
                    "signature": symbol.signature[:1000], "exported": symbol.exported,
                })
        symbols += file_symbols
        functions = [s for s in file_symbols if s["kind"] in ("function", "method", "constructor", "procedure", "paragraph", "subroutine")]
        files[item.path] = {
            "file_id": str(uuid.uuid4()), "path": item.path, "directory": "" if str(PurePosixPath(item.path).parent) == "." else str(PurePosixPath(item.path).parent),
            "language": item.language, "category": item.category, "container": containers_by_file.get(item.path, ""),
            "layer": cls.layer, "role": "Generated Code" if item.generated and is_source else cls.role, "role_confidence": cls.confidence,
            "size_bytes": item.size, "loc": code + comment + blank, "code_lines": code, "comment_lines": comment, "blank_lines": blank,
            "complexity": result.complexity if result else 0, "max_function_complexity": max((s["complexity"] for s in functions), default=0),
            "symbol_count": len(file_symbols), "fan_in": graph_result.fan_in.get(item.path, 0), "fan_out": graph_result.fan_out.get(item.path, 0),
            "pagerank": graph_result.pagerank.get(item.path, 0.0), "scc_id": graph_result.scc_of.get(item.path),
            "is_entry": item.path in entry_points or bool(result and result.has_main), "is_test": cls.is_test, "is_generated": item.generated,
            "is_source": is_source, "sha256": item.sha256, "concepts": cls.concepts, "evidence": cls.evidence + ([f"routes: {', '.join(result.routes[:6])}"] if result and result.routes else []),
            "summary": "", "summary_source": "", "text": item.text,
        }
    concerns, metrics = quality.audit(list(files.values()), symbols, graph_result, macro_result, containers_by_file)
    run.log(f"Quality audit: {len(concerns)} concern(s) · health score {metrics['health_score']}/100", 66)
    run.check()

    # ---------------- Stage 6: synthesis ----------------
    run.log("Stage 6: semantic synthesis (bottom-up over the condensed DAG)", 67, stage="Stage 6 · Synthesis")
    settings = llm_settings.settings_row()
    llm_ready = llm.configured()
    cache = _summary_cache(repo["repo_id"]) if llm_ready else {}
    symbols_by_file: dict[str, list[dict]] = {}
    for symbol in symbols:
        symbols_by_file.setdefault(symbol["path"], []).append(symbol)
    produced = 0
    if llm_ready:
        run.log(f"Using {llm.provider_name()} for file, container and repository summaries (limit {settings.get('llm_file_limit', 40)} files)", 68)
        produced = synthesis.summarise_files(
            graph_result.topo_order, files, symbols_by_file, graph_result.adjacency, int(settings.get("llm_file_limit") or 0), cache,
            lambda message: (run.check(), run.progress(None, message, force=False)),
        )
        for path, item in files.items():
            if item["summary"]:
                item["summary_source"] = "llm"
    else:
        run.log("No LLM configured — deterministic summaries only (configure Gemini or Spirare under Settings > LLM)", 68, "warn")
    for path, item in files.items():
        if not item["summary"] and item["is_source"]:
            item["summary"] = synthesis.deterministic_file_summary(item, symbols_by_file.get(path, []))
            item["summary_source"] = "rules"
    run.check()
    if llm_ready:
        synthesis.summarise_containers(macro_result, files, containers_by_file)
    file_count = len(workspace.files)
    loc_total = sum(item["loc"] for item in files.values() if item["is_source"])
    narrative = synthesis.summarise_repository(repo["name"], macro_result, files, concerns, metrics) if llm_ready else {}
    summary = narrative.get("summary") or synthesis.deterministic_repo_summary(repo["name"], macro_result, file_count, loc_total, metrics)
    diagrams = c4.build(repo["name"], narrative.get("business_purpose", ""), macro_result, list(files.values()), graph_result.edges, containers_by_file)
    run.log(f"Synthesis complete · {produced} LLM file summaries · C4 diagrams (context, container, {len(diagrams['components'])} component, dynamic) validated", 80)
    run.check()

    # ---------------- Stage 7: layer catalogue ----------------
    run.log("Stage 7: layer catalogue — endpoints, services, data, screens and shared items with cross-layer links", 81, stage="Stage 7 · Layer catalogue")
    from app.services import catalog as catalog_service

    catalog_result = catalog_service.from_pipeline(files, macro_result, graph_result, lambda m: run.log(m, None))
    catalog_explained = 0
    if llm_ready:
        catalog_explained = catalog_service.enrich_catalog(catalog_result, repo["repo_id"], lambda m: (run.check(), run.progress(None, m, force=False)))
        run.log(f"Layer catalogue: {catalog_explained} item(s) explained by {llm.provider_name()} (purpose and business rules)", 84)
    run.check()

    # ---------------- Stage 8: indexing ----------------
    run.log("Indexing content-addressed chunks for Chat with Analyst", 85, stage="Stage 8 · Indexing")
    chunk_rows = _chunks(files, symbols_by_file) + catalog_service.chunk_rows(catalog_result)
    signature = llm.embedding_signature()
    embedded = reused = 0
    limit = int(settings.get("embedding_chunk_limit") or 0)
    if signature and limit > 0:
        embedded, reused = _embed(run, repo["repo_id"], chunk_rows, signature, limit, files)
        run.log(f"Embeddings: {embedded} new, {reused} reused by content hash ({signature})", 95)
    else:
        run.log("Embeddings not configured — chat uses BM25 lexical retrieval with graph expansion", 95)
    run.check()

    # ---------------- persist ----------------
    run.log("Stage 9: saving the analysis to the database for offline viewing", 96, stage="Stage 9 · Saving results")
    _persist(analysis_id, macro_result, files, symbols, graph_result, concerns, chunk_rows)
    catalog_service.persist(analysis_id, catalog_result)
    layer_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for item in files.values():
        if item["is_source"] and not item["is_test"]:
            layer_counts[item["layer"]] = layer_counts.get(item["layer"], 0) + 1
        if item["is_source"]:
            role_counts[item["role"]] = role_counts.get(item["role"], 0) + 1
    stats = {
        **metrics,
        "languages": macro_result.languages,
        "layers": layer_counts,
        "roles": dict(sorted(role_counts.items(), key=lambda kv: -kv[1])),
        "skipped": workspace.skipped,
        "files_total": file_count,
        "files_text": len(text_files),
        "files_source": sum(1 for f in files.values() if f["is_source"]),
        "files_parsed": len(parsed),
        "parse_modes": parse_modes,
        "parse_errors": parse_errors,
        "symbols": len(symbols),
        "edges": len(graph_result.edges),
        "edge_kinds": _edge_kinds(graph_result.edges),
        "imports_resolved": graph_result.resolved,
        "imports_unresolved": graph_result.unresolved,
        "chunks": len(chunk_rows),
        "embedded_chunks": embedded + reused,
        "embedding_signature": signature if (embedded + reused) else "",
        "llm_provider": llm.provider_name() if llm_ready else "",
        "llm_file_summaries": produced,
        "duration_seconds": round(time.time() - started, 1),
        "catalog": {**catalog_result.extra.get("stats", {}), "llm_explained": catalog_explained},
        "top_pagerank": [{"path": p, "score": round(s, 5)} for p, s in sorted(graph_result.pagerank.items(), key=lambda kv: -kv[1])[:15]],
        "sccs": graph_result.sccs[:50],
    }
    macro_payload = {
        "architecture_style": macro_result.architecture_style,
        "style_evidence": macro_result.style_evidence,
        "monorepo": macro_result.monorepo,
        "frameworks": macro_result.frameworks,
        "entry_points": macro_result.entry_points,
        "external_systems": macro_result.external_systems,
        "infrastructure": macro_result.infrastructure,
        "ci": macro_result.ci,
        "docs": sorted(macro_result.docs),
        "readme_excerpt": macro_result.readme_excerpt[:6000],
        "doc_claims": macro_result.doc_claims,
        "drift": macro_result.drift,
        "path_aliases": macro_result.path_aliases,
        "business_purpose": narrative.get("business_purpose", ""),
        "key_capabilities": narrative.get("key_capabilities", []),
        "architecture_notes": narrative.get("architecture_notes", []),
        "risks": narrative.get("risks", []),
        "external_packages": sorted({pkg for pkgs in graph_result.external_imports.values() for pkg in pkgs})[:400],
    }
    db.exec_sql(
        """
        UPDATE analyses SET status = 'completed', stage = 'Completed', progress = 100, message = ?, finished_at = ?,
            merkle_root = ?, file_count = ?, loc_total = ?, health_score = ?, architecture_style = ?, summary = ?,
            stats_json = ?, macro_json = ?, c4_json = ?, llm_used = ?
        WHERE analysis_id = ?
        """,
        [
            f"Completed in {stats['duration_seconds']}s", db.now(), workspace.merkle_root, file_count, loc_total,
            metrics["health_score"], macro_result.architecture_style, summary, json.dumps(stats, default=str),
            json.dumps(macro_payload, default=str), json.dumps(diagrams), llm_ready, analysis_id,
        ],
    )
    run.stage = "Completed"
    _feature_catalogue(run, repo["repo_id"], analysis_id, analysis.get("started_by") or "system")
    run.log(f"Analysis completed in {stats['duration_seconds']}s · health {metrics['health_score']}/100", 100)


def _feature_catalogue(run: Run, repo_id: str, analysis_id: str, user_id: str) -> None:
    """Keeps the repository's feature catalogue (used by pull-request impact) up to date. Never fails the analysis."""
    from app.services import features

    try:
        result = features.auto_build(repo_id, analysis_id, user_id)
    except Exception:  # noqa: BLE001
        logger.exception("Automatic feature catalogue failed for %s", analysis_id)
        run.log("Feature catalogue could not be rebuilt automatically; use Propose features on the Features page", 100, "warn")
        return
    if result is not None:
        run.log(f"Feature catalogue rebuilt from {result['entries']} entry point(s): {result['created']} proposed, {result['kept']} confirmed kept", 100)


def _edge_kinds(edges) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges:
        counts[edge.kind] = counts.get(edge.kind, 0) + 1
    return counts


def _summary_cache(repo_id: str) -> dict[str, str]:
    rows = db.fetchall(
        """
        SELECT f.sha256, f.summary FROM analysis_files f JOIN analyses a ON a.analysis_id = f.analysis_id
        WHERE a.repo_id = ? AND a.status = 'completed' AND f.summary_source = 'llm' AND f.summary <> ''
        """,
        [repo_id],
    )
    return {row["sha256"]: row["summary"] for row in rows}


def _chunks(files: dict[str, dict], symbols_by_file: dict[str, list[dict]]) -> list[dict]:
    rows: list[dict] = []
    for path, item in files.items():
        text = item.get("text")
        if not text or item["category"] not in ("code", "script", "markup", "doc", "config") or item["is_generated"]:
            continue
        if item["category"] == "config" and item["size_bytes"] > 60_000:
            continue
        for chunk in chunks.chunk_file(path, text, item["category"], symbols_by_file.get(path, [])):
            rows.append({
                "chunk_id": str(uuid.uuid4()), "path": path, "scope": chunk.scope[:1000], "kind": chunk.kind,
                "line_start": chunk.line_start, "line_end": chunk.line_end, "content": chunk.content,
                "content_hash": chunk.content_hash, "embedding": None, "priority": item["pagerank"] + (0.5 if item["category"] == "doc" else 0),
            })
    return rows


def _embed(run: Run, repo_id: str, rows: list[dict], signature: str, limit: int, files: dict[str, dict]) -> tuple[int, int]:
    previous = db.fetchall(
        """
        SELECT c.content_hash, c.embedding FROM analysis_chunks c JOIN analyses a ON a.analysis_id = c.analysis_id
        WHERE a.repo_id = ? AND a.status = 'completed' AND c.embedding IS NOT NULL AND a.stats_json LIKE ?
        """,
        [repo_id, f'%"embedding_signature": "{signature}"%'],
    )
    known = {row["content_hash"]: row["embedding"] for row in previous}
    reused = 0
    for row in rows:
        if row["content_hash"] in known:
            row["embedding"] = known[row["content_hash"]]
            reused += 1
    pending = sorted((row for row in rows if row["embedding"] is None and not files.get(row["path"], {}).get("is_test")), key=lambda r: -r["priority"])[: max(0, limit - reused)]
    done = 0

    def work(row: dict) -> None:
        text = f"# {row['path']} :: {row['scope']}\n{row['content']}"
        vector = llm.embed(text)
        if vector:
            row["embedding"] = json.dumps([round(value, 6) for value in vector])

    with ThreadPoolExecutor(max_workers=6) as pool:
        for start in range(0, len(pending), 48):
            run.check()
            batch = pending[start : start + 48]
            list(pool.map(work, batch))
            done += sum(1 for row in batch if row["embedding"])
            run.progress(85 + 10 * (start + len(batch)) / max(1, len(pending)), f"Embedding chunks {start + len(batch)}/{len(pending)}…", force=False)
    return done, reused


def _persist(analysis_id: str, macro_result, files: dict[str, dict], symbols: list[dict], graph_result, concerns: list[dict], chunk_rows: list[dict]) -> None:
    max_content = config.max_file_bytes()
    containers = []
    for container in macro_result.containers:
        prefix = container.path
        members = [f for f in files.values() if f["container"] == prefix and f["is_source"]]
        containers.append((
            str(uuid.uuid4()), analysis_id, container.name[:255], container.path, container.ecosystem, container.kind, container.manifest,
            json.dumps(container.frameworks), len(members), sum(m["loc"] for m in members), container.description[:4000],
        ))
    db.insert_many("analysis_containers", ["container_id", "analysis_id", "name", "path", "ecosystem", "kind", "manifest", "frameworks_json", "file_count", "loc", "summary"], containers)
    file_columns = [
        "file_id", "analysis_id", "path", "directory", "language", "container", "layer", "role", "role_confidence", "size_bytes", "loc",
        "code_lines", "comment_lines", "blank_lines", "complexity", "max_function_complexity", "symbol_count", "fan_in", "fan_out",
        "pagerank", "scc_id", "is_entry", "is_test", "is_generated", "is_source", "sha256", "summary_source", "concepts_json", "evidence_json", "summary",
    ]
    file_rows, content_rows = [], []
    for item in files.values():
        file_rows.append((
            item["file_id"], analysis_id, item["path"], item["directory"], item["language"], item["container"], item["layer"], item["role"],
            item["role_confidence"], item["size_bytes"], item["loc"], item["code_lines"], item["comment_lines"], item["blank_lines"],
            item["complexity"], item["max_function_complexity"], item["symbol_count"], item["fan_in"], item["fan_out"], item["pagerank"],
            item["scc_id"], item["is_entry"], item["is_test"], item["is_generated"], item["is_source"], item["sha256"], item["summary_source"],
            json.dumps(item["concepts"]), json.dumps(item["evidence"]), item["summary"][:4000],
        ))
        if item.get("text") is not None and item["category"] in STORED_CONTENT_CATEGORIES and item["size_bytes"] <= max_content:
            content_rows.append((item["file_id"], analysis_id, item["text"].replace("\x00", "")))
    db.insert_many("analysis_files", file_columns, file_rows)
    db.insert_many("analysis_file_contents", ["file_id", "analysis_id", "content"], content_rows, chunk=200)
    db.insert_many(
        "analysis_symbols",
        ["symbol_id", "analysis_id", "path", "name", "qualified_name", "kind", "parent", "line_start", "line_end", "complexity", "signature", "exported"],
        [(s["symbol_id"], analysis_id, s["path"], s["name"], s["qualified_name"], s["kind"], s["parent"], s["line_start"], s["line_end"], s["complexity"], s["signature"], s["exported"]) for s in symbols],
    )
    db.insert_many(
        "analysis_edges",
        ["edge_id", "analysis_id", "source_path", "target_path", "kind", "weight", "detail"],
        [(str(uuid.uuid4()), analysis_id, e.source, e.target, e.kind, e.weight, e.detail) for e in graph_result.edges],
    )
    deps = []
    for container in macro_result.containers:
        for dep in container.dependencies:
            deps.append((str(uuid.uuid4()), analysis_id, container.name[:255], container.ecosystem, dep["name"][:300], dep.get("version", "")[:120], dep.get("scope", "runtime")[:20], container.manifest))
    db.insert_many("analysis_dependencies", ["dependency_id", "analysis_id", "container", "ecosystem", "name", "version", "scope", "manifest"], deps)
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    concerns.sort(key=lambda c: (severity_order.get(c["severity"], 3), c["category"]))
    db.insert_many(
        "analysis_concerns",
        ["concern_id", "analysis_id", "severity", "category", "title", "path", "detail", "evidence"],
        [(str(uuid.uuid4()), analysis_id, c["severity"], c["category"], c["title"], c["path"], c["detail"], c["evidence"]) for c in concerns],
    )
    db.insert_many(
        "analysis_chunks",
        ["chunk_id", "analysis_id", "path", "scope", "kind", "line_start", "line_end", "content", "content_hash", "embedding"],
        [(r["chunk_id"], analysis_id, r["path"], r["scope"], r["kind"], r["line_start"], r["line_end"], r["content"], r["content_hash"], r["embedding"]) for r in chunk_rows],
        chunk=200,
    )
