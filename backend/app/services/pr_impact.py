"""Pull-request impact runs: orchestration, persistence and reading.

A run compares the merge-base of a PR with its head (or any two refs of a repository):

  1  Fetch       remote: bare scratch repo, base branch + PR head ref (forks included), deepened until a merge-base
                 exists; local git folder: read in place with read-only commands
  2  Snapshots   `git archive` of the merge-base and the head (no .git, no credentials, safe extraction)
  3  Analyses    both snapshots go through the normal four-stage engine (purpose 'pr'); an analysis that already
                 exists for the same commit is reused, and LLM summaries / embeddings are reused by content hash
  4  Impact      diff × graph: changed symbols, symbol-level propagation, entry points and features, config / infra /
                 CI at key level, tests, graph delta, dependencies, concerns, reviewers, explainable risk
  5  Synthesis   LLM reading of the changed logic (cross-checked, cached) and the narrative; deterministic fallback

The PR head is never built, installed or executed.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import traceback
import uuid
from pathlib import Path

from app import config, db
from app.errors import AppError
from app.permissions import require
from app.services import features as features_service
from app.services import git_credentials, git_hosts, jobs, listing, llm, llm_settings, pr_logic
from app.services.analyzer import config_impact, diff, impact
from app.services.analyzer.languages import classify_path

logger = logging.getLogger(__name__)
REMOTE = (git_hosts.GITHUB, git_hosts.BITBUCKET, git_hosts.BITBUCKET_SERVER, git_hosts.GIT)
PR_HOSTS = (git_hosts.GITHUB, git_hosts.BITBUCKET, git_hosts.BITBUCKET_SERVER)
RUN_FIELDS = ("run_id", "repo_id", "pr_id", "mode", "title", "base_ref", "head_ref", "base_sha", "head_sha", "merge_base_sha", "base_analysis_id", "head_analysis_id",
              "status", "stage", "progress", "message", "error", "risk", "risk_label", "llm_used", "ruleset_version", "started_by", "created_at", "started_at", "finished_at")


class Cancelled(Exception):
    pass


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _repo(repo_id: str) -> dict:
    row = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [repo_id])
    if row is None:
        raise AppError(404, "Repository not found")
    return row


def _run_row(run_id: str) -> dict:
    row = db.fetchone("SELECT * FROM pr_impact_runs WHERE run_id = ?", [run_id])
    if row is None:
        raise AppError(404, "Impact run not found")
    return row


# --- listing pull requests and refs ------------------------------------------------------------------------------


def pull_requests(user: dict, repo_id: str) -> dict:
    require(user, "repositories", "read")
    repo = _repo(repo_id)
    latest = {}
    for run in db.fetchall(
        """
        SELECT r.run_id, r.status, r.risk, r.risk_label, r.head_sha, r.created_at, p.number FROM pr_impact_runs r JOIN pull_requests p ON p.pr_id = r.pr_id
        WHERE r.repo_id = ? ORDER BY r.created_at DESC
        """,
        [repo_id],
    ):
        latest.setdefault(run["number"], run)
    if repo["source_type"] not in PR_HOSTS:
        return {"supported": False, "provider": repo["source_type"], "pull_requests": [],
                "message": "This source has no pull requests. Compare two branches, tags or commits instead."}
    ref = git_hosts.parse_url(repo["url"])
    items = git_hosts.list_pull_requests(ref, git_credentials.credential_for(user["user_id"], ref))
    for item in items:
        item.pop("head_fetch", None)
        run = latest.get(item["number"])
        item["latest_run"] = run
        item["stale"] = bool(run and run["head_sha"] and item["head_sha"] and not run["head_sha"].startswith(item["head_sha"][:12]))
    return {"supported": True, "provider": repo["source_type"], "pull_requests": items}


def refs(user: dict, repo_id: str) -> dict:
    """Branches (and tags for local repositories) that can be compared."""
    require(user, "repositories", "read")
    repo = _repo(repo_id)
    if repo["source_type"] in REMOTE:
        ref = git_hosts.parse_url(repo["url"])
        listed = git_hosts.list_branches(ref, git_credentials.credential_for(user["user_id"], ref))
        return {"default_branch": listed["default_branch"] or repo["default_branch"], "refs": [{"name": b["name"], "sha": b["sha"], "type": "branch"} for b in listed["branches"]]}
    if repo["source_type"] == "local" and repo["url"]:
        from app.services.analyzer import ingest

        path = ingest.check_local_path(repo["url"])
        if not (path / ".git").exists():
            return {"default_branch": "", "refs": [], "message": "This folder is not a git repository."}
        workspace = diff.local_workspace(path)
        out = workspace.try_git("for-each-ref", "--sort=-committerdate", "--format=%(refname)\t%(objectname)", "refs/heads", "refs/tags") or ""
        head = (workspace.try_git("symbolic-ref", "--short", "HEAD") or "").strip()
        items = []
        for line in out.splitlines()[:1000]:
            name, _, sha = line.partition("\t")
            kind = "branch" if name.startswith("refs/heads/") else "tag"
            items.append({"name": name.removeprefix("refs/heads/").removeprefix("refs/tags/"), "sha": sha, "type": kind})
        return {"default_branch": head, "refs": items}
    return {"default_branch": "", "refs": [], "message": "Uploaded archives have no git history to compare."}


# --- starting -----------------------------------------------------------------------------------------------------


def start_run(user: dict, repo_id: str, body: dict) -> dict:
    require(user, "repositories", "write")
    repo = _repo(repo_id)
    number = body.get("pr_number")
    now = db.now()
    pr_id, title, mode = "", "", "pr"
    base_ref, head_ref = str(body.get("base_ref") or "").strip()[:255], str(body.get("head_ref") or "").strip()[:255]
    if number not in (None, ""):
        if repo["source_type"] not in PR_HOSTS:
            raise AppError(400, "This repository's host has no pull requests; compare two refs instead.")
        ref = git_hosts.parse_url(repo["url"])
        pr = git_hosts.get_pull_request(ref, git_credentials.credential_for(user["user_id"], ref), int(number))
        pr_id = _upsert_pr(repo_id, pr)
        title = f"#{pr['number']} {pr['title']}"
        base_ref, head_ref = pr["target_branch"], pr["source_branch"]
    else:
        mode = "refs"
        if not base_ref or not head_ref:
            raise AppError(400, "Choose a pull request, or a base and a head ref to compare")
        if base_ref == head_ref:
            raise AppError(400, "Choose two different refs")
        if repo["source_type"] == "zip" or (repo["source_type"] == "local" and not repo["url"]):
            raise AppError(400, "Uploaded archives and folders have no git history to compare. Register a git repository or a local git folder.")
        title = f"{head_ref} → {base_ref}"
    active = db.fetchone(
        "SELECT run_id FROM pr_impact_runs WHERE repo_id = ? AND status IN ('queued', 'running') AND ((pr_id <> '' AND pr_id = ?) OR (pr_id = '' AND base_ref = ? AND head_ref = ?))",
        [repo_id, pr_id, base_ref, head_ref],
    )
    if active:
        raise AppError(409, "An impact analysis of this change is already running", extra={"run_id": active["run_id"]})
    run_id = str(uuid.uuid4())
    db.insert("pr_impact_runs", {
        "run_id": run_id, "repo_id": repo_id, "pr_id": pr_id, "mode": mode, "title": title[:1000], "base_ref": base_ref, "head_ref": head_ref,
        "status": "queued", "stage": "Queued", "progress": 0, "message": "Waiting for a worker", "started_by": user["user_id"], "created_at": now,
        "ruleset_version": impact.RULESET_VERSION,
    })
    jobs.submit_impact(run_id)
    return _brief(_run_row(run_id))


def _upsert_pr(repo_id: str, pr: dict) -> str:
    existing = db.fetchone("SELECT pr_id FROM pull_requests WHERE repo_id = ? AND number = ?", [repo_id, pr["number"]])
    values = {"title": pr["title"][:1000], "author": pr["author"][:255], "description": pr["description"][:20000], "source_branch": pr["source_branch"][:255],
              "target_branch": pr["target_branch"][:255], "source_clone_url": pr["source_clone_url"][:1000], "url": pr["url"][:1000], "state": pr["state"][:30],
              "head_sha": pr["head_sha"][:64], "base_sha": pr["base_sha"][:64], "updated_at": db.now()}
    if existing:
        sets = ", ".join(f"{k} = ?" for k in values)
        db.exec_sql(f"UPDATE pull_requests SET {sets} WHERE pr_id = ?", [*values.values(), existing["pr_id"]])
        return existing["pr_id"]
    pr_id = str(uuid.uuid4())
    db.insert("pull_requests", {"pr_id": pr_id, "repo_id": repo_id, "number": pr["number"], "created_at": db.now(), **values})
    return pr_id


# --- execution ----------------------------------------------------------------------------------------------------


class Run:
    def __init__(self, run_id: str, is_cancelled):
        self.run_id = run_id
        self.is_cancelled = is_cancelled

    def check(self) -> None:
        if self.is_cancelled(self.run_id):
            raise Cancelled()

    def log(self, message: str, progress: float | None = None, level: str = "info", stage: str | None = None) -> None:
        db.insert("pr_run_events", {"event_id": str(uuid.uuid4()), "run_id": self.run_id, "ts": db.now(), "level": level, "message": message[:2000]})
        sets, params = ["message = ?"], [message[:500]]
        if stage:
            sets.append("stage = ?")
            params.append(stage)
        if progress is not None:
            sets.append("progress = ?")
            params.append(round(min(100.0, max(0.0, progress)), 1))
        db.exec_sql(f"UPDATE pr_impact_runs SET {', '.join(sets)} WHERE run_id = ?", [*params, self.run_id])


def execute(run_id: str, is_cancelled=lambda _id: False) -> None:
    row = db.fetchone("SELECT * FROM pr_impact_runs WHERE run_id = ?", [run_id])
    if row is None or row["status"] not in ("queued",):
        return
    run = Run(run_id, is_cancelled)
    db.exec_sql("UPDATE pr_impact_runs SET status = 'running', started_at = ?, error = '' WHERE run_id = ?", [db.now(), run_id])
    scratch = config.workspace_dir() / "pr" / run_id
    started = time.time()
    try:
        _execute(run, row, scratch, started)
    except Cancelled:
        db.exec_sql("UPDATE pr_impact_runs SET status = 'cancelled', finished_at = ?, message = 'Cancelled by user' WHERE run_id = ?", [db.now(), run_id])
        run.log("Impact analysis cancelled", None, "warn")
    except AppError as exc:
        _fail(run, exc.detail)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Impact run %s failed", run_id)
        _fail(run, f"{exc.__class__.__name__}: {exc}", traceback.format_exc()[-3000:])
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _fail(run: Run, message: str, detail: str = "") -> None:
    db.exec_sql("UPDATE pr_impact_runs SET status = 'failed', finished_at = ?, error = ?, message = ? WHERE run_id = ?",
                [db.now(), (message + ("\n" + detail if detail else ""))[:8000], message[:500], run.run_id])
    try:
        run.log(f"Failed: {message}", None, "error")
    except Exception:  # noqa: BLE001
        logger.exception("Could not log failure")


def _workspace(run: Run, row: dict, repo: dict, scratch: Path) -> tuple[diff.Workspace, str, str, dict]:
    """Returns (workspace, base ref name, head ref name inside it, pr dict)."""
    pr = db.fetchone("SELECT * FROM pull_requests WHERE pr_id = ?", [row["pr_id"]]) if row["pr_id"] else None
    if repo["source_type"] == "local":
        from app.services.analyzer import ingest

        path = ingest.check_local_path(repo["url"])
        run.log(f"Reading the local git repository {path} (read-only)", 3, stage="Fetch")
        return diff.local_workspace(path), row["base_ref"], row["head_ref"], {}
    ref = git_hosts.parse_url(repo["url"])
    credential = git_credentials.credential_for(row["started_by"], ref)
    label = git_hosts.PROVIDER_LABELS.get(ref.provider, "Git")
    if pr:
        number = int(pr["number"])
        head_fetch = {git_hosts.GITHUB: (ref.clone_url, f"refs/pull/{number}/head"), git_hosts.BITBUCKET_SERVER: (ref.clone_url, f"refs/pull-requests/{number}/from"),
                      git_hosts.BITBUCKET: (pr["source_clone_url"] or ref.clone_url, f"refs/heads/{pr['source_branch']}")}[ref.provider]
        run.log(f"Fetching {ref.name}: base {pr['target_branch']} and PR #{number} head from {label}{' (fork)' if head_fetch[0] != ref.clone_url else ''}", 3, stage="Fetch")
        fetches = [(ref.clone_url, f"refs/heads/{pr['target_branch']}", "refs/ca/base"), (head_fetch[0], head_fetch[1], "refs/ca/head")]
    else:
        run.log(f"Fetching {ref.name}: {row['base_ref']} and {row['head_ref']} from {label}", 3, stage="Fetch")
        fetches = [(ref.clone_url, _remote_ref(row["base_ref"]), "refs/ca/base"), (ref.clone_url, _remote_ref(row["head_ref"]), "refs/ca/head")]
    workspace = diff.remote_workspace(scratch / "git", ref, credential, fetches, lambda m: run.log(m))
    return workspace, "refs/ca/base", "refs/ca/head", dict(pr or {})


def _remote_ref(name: str) -> str:
    if name.startswith("refs/"):
        return name
    if name.startswith("tags/"):
        return f"refs/{name}"
    return f"refs/heads/{name}"


def _execute(run: Run, row: dict, scratch: Path, started: float) -> None:
    repo = _repo(row["repo_id"])
    scratch.mkdir(parents=True, exist_ok=True)
    workspace, base_name, head_name, pr = _workspace(run, row, repo, scratch)
    run.check()
    base_sha, head_sha = workspace.resolve(base_name), workspace.resolve(head_name)
    merge_base = workspace.merge_base(base_sha, head_sha)
    if not merge_base:
        raise AppError(409, "The two refs share no history (no merge-base), so there is no change set to analyse.")
    db.exec_sql("UPDATE pr_impact_runs SET base_sha = ?, head_sha = ?, merge_base_sha = ? WHERE run_id = ?", [base_sha, head_sha, merge_base, run.run_id])
    behind = len((workspace.try_git("rev-list", f"{merge_base}..{base_sha}") or "").split())
    ahead = len((workspace.try_git("rev-list", f"{merge_base}..{head_sha}") or "").split())
    run.log(f"Merge-base {merge_base[:10]} · head {head_sha[:10]} is {ahead} commit(s) ahead; the base moved {behind} commit(s) since (not part of this change)", 8)
    file_diffs = workspace.diff(merge_base, head_sha)
    if not file_diffs:
        raise AppError(409, "The head has no changes compared with the merge-base.")
    run.log(f"Diff: {len(file_diffs)} file(s), +{sum(f.additions for f in file_diffs)} −{sum(f.deletions for f in file_diffs)} lines", 10)
    run.check()
    base_dir, head_dir = workspace.export(merge_base, scratch / "base"), workspace.export(head_sha, scratch / "head")
    base_id = _analysis_for(run, repo, row, workspace, merge_base, base_dir, row["base_ref"], "merge-base", 12, 45)
    db.exec_sql("UPDATE pr_impact_runs SET base_analysis_id = ? WHERE run_id = ?", [base_id, run.run_id])
    run.check()
    head_id = _analysis_for(run, repo, row, workspace, head_sha, head_dir, row["head_ref"], "head", 46, 78)
    db.exec_sql("UPDATE pr_impact_runs SET head_analysis_id = ? WHERE run_id = ?", [head_id, run.run_id])
    run.check()

    run.log("Computing impact: changed symbols, symbol-level propagation, entry points, configuration, tests, graph delta", 80, stage="Impact")
    base, head = load_side(base_id, base_dir), load_side(head_id, head_dir)
    file_rows = impact.classify_files(file_diffs, base, head)
    rows_by_path = {r["path"]: r for r in file_rows}
    changes, facts = impact.changed_symbols(file_diffs, base, head, rows_by_path)
    run.log(f"{len(changes)} changed symbol(s): {sum(1 for c in changes if c['change_type'] == 'added')} added, {sum(1 for c in changes if c['change_type'] == 'modified')} modified, "
            f"{sum(1 for c in changes if c['change_type'] == 'removed')} removed · {sum(1 for c in changes if 'contract' in c['flags'])} public contract change(s)", 82)
    # configuration at key level, with readers in the head revision's code
    config_paths = [f for f in file_diffs if any(k in rows_by_path[f.path]["change_kinds"] for k in ("config", "infra", "ci", "manifest"))]
    base_text = {f.old_path or f.path: config_impact.read_text(base_dir, f.old_path or f.path) for f in config_paths if f.status != "A"}
    head_text = {f.path: config_impact.read_text(head_dir, f.path) for f in config_paths if f.status != "D"}
    head_code = {p: head.content(p) for p, f in head.files.items() if f.get("is_source") and not f.get("is_test") and classify_path(p)[1] in ("code", "script", "markup")}
    head_config_items = [i for i in head.catalog if i["kind"] == "config"]
    defined = {i["name"] for i in head_config_items}
    for path, text in head_text.items():
        defined |= {e.key for e in config_impact.parse(path, text)}
    catalog_used = {i["name"]: (i.get("details") or {}).get("used_by", []) for i in head_config_items}
    config_result = config_impact.analyse(config_paths, base_text, head_text, head_code, defined, catalog_used, file_diffs).to_dict()
    seeds = _config_seeds(config_result, head)
    impacted, ranges = impact.propagate(head, base, changes, facts, rows_by_path, seeds)
    entries = impact.entry_points(file_diffs, base, head, changes, impacted, ranges, rows_by_path)
    feature_list = features_service.all_for(repo["repo_id"])
    impact.match_features(entries, feature_list)
    _attach_contracts(entries, base_id, head_id)
    tests = impact.test_impact(head, changes, impacted, rows_by_path)
    delta = impact.graph_delta(base, head, rows_by_path, impacted)
    deps = impact.dependency_diff(base, head)
    concerns = impact.concern_diff(base, head, set(rows_by_path) | set(impacted))
    history = workspace.history(head_sha, [r["path"] for r in file_rows if r["status"] != "D"])
    owners = diff.read_codeowners(head_dir)
    people = impact.reviewers(file_rows, owners, history, impacted)
    risk = impact.risk(file_rows, changes, impacted, entries, tests, config_result, deps, concerns, head, delta, history)
    run.log(f"Risk {risk['label']} ({risk['score']}) · {len(impacted)} impacted file(s) · {len(entries)} entry point(s) · {len(config_result['findings'])} config finding(s) · "
            f"{len(tests['gaps'])} test gap(s)", 88)
    run.check()

    # ---- LLM: changed logic + narrative ----
    settings = llm_settings.settings_row()
    limit = int(settings.get("llm_pr_function_limit") if settings.get("llm_pr_function_limit") is not None else 25)
    context = {"file_kinds": {p: r["change_kinds"] for p, r in rows_by_path.items()}, "pagerank": {p: f.get("pagerank", 0) for p, f in head.files.items()},
               "impacted": impacted, "entries": entries, "callees": _callees(head, [c["path"] for c in changes])}
    produced, cached = pr_logic.analyse_functions(changes, context, limit, _logic_cache(repo["repo_id"]), lambda m: run.log(m, 90, stage="Synthesis"))
    if produced or cached:
        run.log(f"Changed logic explained for {produced + cached} function(s) ({produced} new, {cached} reused by content hash)", 94)
    elif not llm.configured():
        run.log("No LLM configured — the report is fully deterministic (configure Gemini or Spirare under Settings > LLM to explain the changed logic)", 94, "warn")
    result = {
        "pr": {k: pr.get(k) for k in ("number", "title", "author", "description", "url", "source_branch", "target_branch", "state")} if pr else None,
        "files": file_rows, "entry_points": entries, "config": config_result, "tests": tests, "graph": delta, "dependencies": deps, "concerns": concerns,
        "reviewers": people, "risk": risk, "impacted": sorted(impacted.values(), key=lambda r: (r["depth"], _conf(r["confidence"]), r["path"]))[:1500],
        "features": [{"feature_id": f["feature_id"], "name": f["name"], "confirmed": f["confirmed"]} for f in feature_list],
        "stats": {
            "files": len(file_rows), "additions": sum(f.additions for f in file_diffs), "deletions": sum(f.deletions for f in file_diffs), "symbols": len(changes),
            "impacted": len(impacted), "impacted_strong": sum(1 for r in impacted.values() if r["confidence"] in ("high", "medium")),
            "entry_points": len(entries), "ahead": ahead, "behind": behind, "history_shallow": workspace.is_shallow() if not workspace.local else False,
            "llm_functions": produced, "llm_functions_cached": cached, "duration_seconds": round(time.time() - started, 1),
        },
        "limits": [
            "Static estimate: reflection, dependency-injection wiring by configuration, dynamically built routes and string-based dispatch are only partly visible.",
            "Consumers in other repositories (API clients, shared libraries, database users) are not analysed; contract changes are flagged as external impact.",
        ] + ([f"Configuration from {', '.join(config_result['external_config'])} lives outside the repository and was not analysed."] if config_result["external_config"] else []),
    }
    feature_names = {f["feature_id"]: f["name"] for f in feature_list}
    story = pr_logic.narrative(result, pr or {"title": row["title"], "description": ""}, changes, feature_names)
    _persist(run.run_id, file_rows, file_diffs, changes, impacted)
    db.exec_sql(
        """
        UPDATE pr_impact_runs SET status = 'completed', stage = 'Completed', progress = 100, message = ?, finished_at = ?, risk = ?, risk_label = ?,
            result_json = ?, narrative_json = ?, llm_used = ? WHERE run_id = ?
        """,
        [f"Completed in {round(time.time() - started, 1)}s · risk {risk['label']}", db.now(), risk["score"], risk["label"], json.dumps(result, default=str),
         json.dumps(story, default=str), story.get("source") == "llm" or produced > 0, run.run_id],
    )
    run.log(f"Impact analysis completed in {round(time.time() - started, 1)}s · risk {risk['label']}", 100, stage="Completed")


def _conf(value: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(value, 3)


def _analysis_for(run: Run, repo: dict, row: dict, workspace: diff.Workspace, sha: str, folder: Path, ref_name: str, label: str, low: float, high: float) -> str:
    """Reuses a completed analysis of the same commit, otherwise analyses the snapshot inline with the normal engine."""
    existing = db.fetchone("SELECT analysis_id FROM analyses WHERE repo_id = ? AND commit_sha = ? AND status = 'completed' ORDER BY finished_at DESC LIMIT 1", [repo["repo_id"], sha])
    if existing:
        run.log(f"Reusing the completed analysis {existing['analysis_id'][:8]} of the {label} commit {sha[:10]}", high)
        return existing["analysis_id"]
    facts = workspace.commit_facts(sha)
    analysis_id = str(uuid.uuid4())
    db.insert("analyses", {
        "analysis_id": analysis_id, "repo_id": repo["repo_id"], "branch": ref_name[:255], "commit_sha": sha, "commit_message": facts["commit_message"][:2000],
        "commit_author": facts["commit_author"][:250], "commit_date": facts["commit_date"][:60], "status": "queued", "stage": "Queued", "progress": 0,
        "message": f"Part of impact run {run.run_id[:8]}", "started_by": row["started_by"], "created_at": db.now(), "stats_json": json.dumps({}),
        "source_dir": str(folder), "purpose": "pr",
    })
    run.log(f"Analysing the {label} commit {sha[:10]} with the four-stage engine (analysis {analysis_id[:8]})", low, stage=f"Analyse {label}")
    from app.services.analyzer import pipeline

    pipeline.execute(analysis_id, lambda _id: run.is_cancelled(run.run_id))
    done = db.fetchone("SELECT status, error FROM analyses WHERE analysis_id = ?", [analysis_id])
    if done["status"] == "cancelled":
        raise Cancelled()
    if done["status"] != "completed":
        raise AppError(500, f"The {label} analysis failed: {(done['error'] or '')[:300]}")
    db.exec_sql("UPDATE analyses SET source_dir = '' WHERE analysis_id = ?", [analysis_id])
    run.log(f"The {label} analysis completed", high)
    return analysis_id


def load_side(analysis_id: str, folder: Path | None = None) -> impact.Side:
    files = {}
    for row in db.fetchall(
        "SELECT file_id, path, layer, role, language, container, is_test, is_source, is_entry, is_generated, concepts_json, pagerank, complexity, sha256 FROM analysis_files WHERE analysis_id = ?",
        [analysis_id],
    ):
        row["concepts"] = _loads(row.pop("concepts_json"), [])
        row["pagerank"] = float(row["pagerank"] or 0)
        files[row["path"]] = row
    symbols: dict[str, list[dict]] = {}
    for row in db.fetchall(
        "SELECT path, name, qualified_name, kind, parent, line_start, line_end, complexity, signature, exported FROM analysis_symbols WHERE analysis_id = ? ORDER BY path, line_start",
        [analysis_id],
    ):
        symbols.setdefault(row["path"], []).append(row)
    edges = [(r["source_path"], r["target_path"], r["kind"]) for r in db.fetchall("SELECT source_path, target_path, kind FROM analysis_edges WHERE analysis_id = ?", [analysis_id])]
    catalog = []
    for row in db.fetchall("SELECT item_key, kind, name, path, line_start, line_end, layer, framework, summary, details_json FROM analysis_catalog WHERE analysis_id = ?", [analysis_id]):
        row["details"] = _loads(row.pop("details_json"), {}) if row["kind"] == "config" else {}
        catalog.append(row)
    links = [(r["source_key"], r["target_key"], r["kind"]) for r in db.fetchall("SELECT source_key, target_key, kind FROM analysis_catalog_links WHERE analysis_id = ?", [analysis_id])]
    concerns = db.fetchall("SELECT severity, category, title, path, detail FROM analysis_concerns WHERE analysis_id = ?", [analysis_id])
    dependencies = db.fetchall("SELECT container, ecosystem, name, version, scope FROM analysis_dependencies WHERE analysis_id = ?", [analysis_id])
    cache: dict[str, str] = {}

    def content(path: str) -> str:
        if path in cache:
            return cache[path]
        text = config_impact.read_text(folder, path, 1_000_000) if folder is not None else ""
        if not text and path in files:
            found = db.fetchone("SELECT content FROM analysis_file_contents WHERE file_id = ?", [files[path]["file_id"]])
            text = found["content"] if found else ""
        if len(cache) < 6000:
            cache[path] = text
        return text

    return impact.Side(analysis_id, files, symbols, edges, content, catalog, links, concerns, dependencies)


def _config_seeds(config_result: dict, head: impact.Side) -> dict[str, dict]:
    seeds: dict[str, dict] = {}
    for change in config_result["keys"]:
        for reader in change.get("readers", [])[:8]:
            path, line = reader.get("path"), int(reader.get("line") or 0)
            if not path or path not in head.files:
                continue
            holders = impact._containing_symbols(head.symbols.get(path, []), [line]) if line else []
            seed = seeds.setdefault(path, {"names": [], "ranges": [], "via": change["path"], "reason": f"reads {change['key']} ({change['change']}{' in ' + change['environment'] if change['environment'] != 'default' else ''})", "depth": 4})
            for sym in holders:
                if sym["name"] not in seed["names"] and len(sym["name"]) >= 3:
                    seed["names"].append(sym["name"])
                seed["ranges"].append((sym["line_start"], sym["line_end"] or sym["line_start"]))
            if not holders and line:
                seed["ranges"].append((line, line))
    return seeds


def _callees(head: impact.Side, paths: list[str]) -> dict[str, list[str]]:
    forward = head.forward()
    return {p: sorted({f"{t} ({k})" for t, k in forward.get(p, [])})[:10] for p in set(paths)}


def _attach_contracts(entries: list[dict], base_id: str, head_id: str) -> None:
    from app.services import catalog

    try:
        changes = catalog.diff(base_id, head_id)
    except Exception:  # noqa: BLE001 - older analyses without a catalogue
        return
    if not changes.get("available"):
        return
    by_name: dict[str, list[dict]] = {}
    for kind in ("endpoint", "screen", "job"):
        for item in (changes.get(kind) or {}).get("changed", []):
            by_name.setdefault(item["name"], []).extend(item["changes"])
    for entry in entries:
        if entry["name"] in by_name:
            entry["contract_changes"] = by_name[entry["name"]][:10]
            entry["breaking"] = any(c.get("breaking") for c in by_name[entry["name"]])


def _logic_cache(repo_id: str) -> dict[str, dict]:
    rows = db.fetchall(
        """
        SELECT s.content_hash, s.logic_json FROM pr_changed_symbols s JOIN pr_impact_runs r ON r.run_id = s.run_id
        WHERE r.repo_id = ? AND s.logic_source = 'llm' AND s.content_hash <> ''
        """,
        [repo_id],
    )
    out = {}
    for row in rows:
        logic = _loads(row["logic_json"], {})
        if logic.get("prompt_version") == pr_logic.PROMPT_VERSION:
            out[row["content_hash"]] = logic
    return out


def _persist(run_id: str, file_rows: list[dict], file_diffs: list[diff.FileDiff], changes: list[dict], impacted: dict[str, dict]) -> None:
    for table in ("pr_changed_files", "pr_changed_symbols", "pr_impacted"):
        db.exec_sql(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
    hunks = {fd.path: fd.hunks for fd in file_diffs}
    db.insert_many("pr_changed_files", ["row_id", "run_id", "path", "old_path", "status", "additions", "deletions", "layer", "role", "change_kinds_json", "hunks_json"], [
        (str(uuid.uuid4()), run_id, r["path"][:1000], (r["old_path"] or "")[:1000], r["status"], r["additions"], r["deletions"], r["layer"] or "", (r["role"] or "")[:60],
         json.dumps(r["change_kinds"]), json.dumps([h.to_dict() for h in hunks.get(r["path"], [])][:200]))
        for r in file_rows
    ], chunk=200)
    db.insert_many("pr_changed_symbols", ["row_id", "run_id", "path", "qualified_name", "name", "kind", "change_type", "signature_before", "signature_after", "exported",
                                          "line_start", "line_end", "flags_json", "content_hash", "logic_json", "logic_source"], [
        (str(uuid.uuid4()), run_id, c["path"][:1000], c["qualified_name"][:1000], c["name"][:500], c["kind"][:30], c["change_type"], c["signature_before"][:2000],
         c["signature_after"][:2000], c["exported"], c["line_start"], c["line_end"], json.dumps(c["flags"]), c["content_hash"], json.dumps(c.get("logic") or {}),
         c.get("logic_source") or "")
        for c in changes
    ], chunk=500)
    db.insert_many("pr_impacted", ["row_id", "run_id", "path", "depth", "via_path", "via_kind", "symbols_json", "confidence", "reason", "layer", "role", "is_test"], [
        (str(uuid.uuid4()), run_id, r["path"][:1000], r["depth"], (r["via_path"] or "")[:1000], r["via_kind"][:20], json.dumps(r["symbols"]), r["confidence"],
         r["reason"][:500], r.get("layer") or "", (r.get("role") or "")[:60], r.get("is_test", False))
        for r in impacted.values()
    ], chunk=1000)


# --- reading ------------------------------------------------------------------------------------------------------


def _brief(row: dict) -> dict:
    return {k: row.get(k) for k in RUN_FIELDS}


def _run_filters(repo_id: str, date_from: str, date_to: str, required: bool = False) -> tuple[str, list]:
    where, params = listing.date_range(date_from, date_to, "r.created_at", required)
    if repo_id:
        where.insert(0, "r.repo_id = ?")
        params.insert(0, repo_id)
    return (f"WHERE {' AND '.join(where)}" if where else ""), params


def _run_briefs(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        brief = _brief(row)
        stats = _loads(row["result_json"], {}).get("stats", {}) if row["status"] == "completed" else {}
        brief.update(repo_name=row["repo_name"], pr_number=row["pr_number"], pr_url=row["pr_url"], stats=stats)
        out.append(brief)
    return out


_RUN_LIST_SQL = """
    SELECT r.*, repo.name AS repo_name, p.number AS pr_number, p.url AS pr_url FROM pr_impact_runs r JOIN repositories repo ON repo.repo_id = r.repo_id
    LEFT JOIN pull_requests p ON p.pr_id = r.pr_id {where} ORDER BY r.created_at DESC LIMIT {limit} OFFSET {offset}
"""


def list_runs(user: dict, repo_id: str = "", limit: int = 100) -> list[dict]:
    require(user, "repositories", "read")
    where, params = _run_filters(repo_id, "", "")
    rows = db.fetchall(_RUN_LIST_SQL.format(where=where, limit=max(1, min(int(limit), 500)), offset=0), params)
    return _run_briefs(rows)


def page_runs(user: dict, repo_id: str = "", page: int = 1, page_size: int = listing.PAGE_SIZE, date_from: str = "", date_to: str = "") -> dict:
    """One page of impact runs (newest first) with the total, optionally limited to a date range."""
    require(user, "repositories", "read")
    number, size, offset = listing.page_bounds(page, page_size)
    where, params = _run_filters(repo_id, date_from, date_to)
    total = int(db.scalar(f"SELECT count(*) FROM pr_impact_runs r JOIN repositories repo ON repo.repo_id = r.repo_id {where}", params) or 0)
    rows = db.fetchall(_RUN_LIST_SQL.format(where=where, limit=size, offset=offset), params)
    return {"items": _run_briefs(rows), "total": total, "page": number, "page_size": size}


def purge_runs(user: dict, body: dict) -> dict:
    """Deletes impact runs created in [date_from, date_to). Queued/running runs are kept. dry_run only counts."""
    require(user, "repositories", "write")
    where, params = _run_filters(str(body.get("repo_id") or ""), str(body.get("date_from") or ""), str(body.get("date_to") or ""), required=True)
    rows = db.fetchall(f"SELECT r.run_id, r.status FROM pr_impact_runs r {where}", params)
    active = [r for r in rows if r["status"] in ("queued", "running")]
    targets = [r["run_id"] for r in rows if r["status"] not in ("queued", "running")]
    if not body.get("dry_run"):
        for run_id in targets:
            delete_run(user, run_id)
    return {"matched": len(targets), "deleted": 0 if body.get("dry_run") else len(targets), "skipped_active": len(active), "dry_run": bool(body.get("dry_run"))}


def get_run(user: dict, run_id: str) -> dict:
    require(user, "repositories", "read")
    row = _run_row(run_id)
    repo = db.fetchone("SELECT repo_id, name, source_type, url, default_branch FROM repositories WHERE repo_id = ?", [row["repo_id"]]) or {}
    payload = _brief(row)
    payload["repo"] = repo
    payload["result"] = _loads(row["result_json"], {})
    payload["narrative"] = _loads(row["narrative_json"], {})
    pr = db.fetchone("SELECT * FROM pull_requests WHERE pr_id = ?", [row["pr_id"]]) if row["pr_id"] else None
    payload["pull_request"] = {k: pr[k] for k in ("number", "title", "author", "description", "url", "source_branch", "target_branch", "state", "head_sha")} if pr else None
    symbols = db.fetchall("SELECT * FROM pr_changed_symbols WHERE run_id = ? ORDER BY path, line_start", [run_id])
    payload["symbols"] = [{**{k: s[k] for k in ("path", "qualified_name", "name", "kind", "change_type", "signature_before", "signature_after", "exported", "line_start", "line_end", "logic_source")},
                           "flags": _loads(s["flags_json"], []), "logic": _loads(s["logic_json"], {})} for s in symbols]
    starter = db.fetchone("SELECT username FROM users WHERE user_id = ?", [row["started_by"]])
    payload["started_by_name"] = starter["username"] if starter else row["started_by"]
    payload["cross_repo"] = _cross_repo(row["repo_id"], payload["result"])
    return payload


def openapi_diff(user: dict, run_id: str) -> dict:
    from app.services import openapi

    require(user, "repositories", "read")
    row = _run_row(run_id)
    if row["status"] != "completed" or not row["base_analysis_id"] or not row["head_analysis_id"]:
        return {"available": False, "message": "The run has not completed."}
    return openapi.compare_analyses(row["base_analysis_id"], row["head_analysis_id"])


def _cross_repo(repo_id: str, result: dict) -> dict:
    """Consumers in other repositories (from the system landscapes this repository belongs to) of the affected
    endpoints, and of topics published from changed or impacted files. Computed when viewed, so landscapes created
    after the run still apply."""
    from app.services import landscape

    if not result or not landscape.in_any_landscape(repo_id):
        return {"in_landscape": False, "consumers": []}
    keys = [e["item_key"] for e in result.get("entry_points") or [] if e.get("item_key")]
    paths = [f["path"] for f in result.get("files") or []] + [i["path"] for i in result.get("impacted") or []]
    return {"in_landscape": True, "consumers": landscape.consumers_of(repo_id, keys, paths)}


def file_diff(user: dict, run_id: str, path: str) -> dict:
    require(user, "repositories", "read")
    _run_row(run_id)
    row = db.fetchone("SELECT * FROM pr_changed_files WHERE run_id = ? AND path = ?", [run_id, path])
    if row is None:
        raise AppError(404, "This file is not part of the change")
    return {"path": row["path"], "old_path": row["old_path"], "status": row["status"], "additions": row["additions"], "deletions": row["deletions"],
            "change_kinds": _loads(row["change_kinds_json"], []), "hunks": _loads(row["hunks_json"], [])}


def events(user: dict, run_id: str, after: str = "") -> list[dict]:
    require(user, "repositories", "read")
    _run_row(run_id)
    if after:
        return db.fetchall("SELECT * FROM pr_run_events WHERE run_id = ? AND ts > ? ORDER BY ts", [run_id, after])
    return db.fetchall("SELECT * FROM pr_run_events WHERE run_id = ? ORDER BY ts", [run_id])


def impact_graph(user: dict, run_id: str, limit: int = 160) -> dict:
    """Changed files, impacted files (by depth) and the edges between them in the head analysis."""
    require(user, "repositories", "read")
    row = _run_row(run_id)
    if row["status"] != "completed":
        raise AppError(409, "The impact run has not completed")
    changed = {r["path"]: r for r in db.fetchall("SELECT path, status, layer, role FROM pr_changed_files WHERE run_id = ?", [run_id])}
    impacted = {r["path"]: r for r in db.fetchall("SELECT path, depth, confidence, layer, role, is_test, via_path FROM pr_impacted WHERE run_id = ? AND confidence <> 'low' ORDER BY depth", [run_id])}
    files = {r["path"]: r for r in db.fetchall(
        "SELECT path, layer, role, language, code_lines, complexity, fan_in, fan_out, pagerank, scc_id, is_entry FROM analysis_files WHERE analysis_id = ?", [row["head_analysis_id"]])}
    chosen = [p for p in changed if p in files] + [p for p in impacted if p in files]
    chosen = chosen[:limit]
    keep = set(chosen)
    nodes = []
    for path in chosen:
        node = dict(files[path])
        node["pagerank"] = round(float(node["pagerank"] or 0), 6)
        node["impact"] = "changed" if path in changed else "impacted"
        node["depth"] = 0 if path in changed else impacted[path]["depth"]
        node["is_test"] = bool(impacted.get(path, {}).get("is_test"))
        nodes.append(node)
    edges = [e for e in db.fetchall("SELECT source_path, target_path, kind, weight FROM analysis_edges WHERE analysis_id = ?", [row["head_analysis_id"]])
             if e["source_path"] in keep and e["target_path"] in keep]
    return {"nodes": nodes, "edges": edges, "truncated": len(changed) + len(impacted) > len(chosen)}


def cancel_run(user: dict, run_id: str) -> dict:
    require(user, "repositories", "write")
    row = _run_row(run_id)
    if row["status"] == "queued":
        db.exec_sql("UPDATE pr_impact_runs SET status = 'cancelled', message = 'Cancelled before start', finished_at = ? WHERE run_id = ?", [db.now(), run_id])
    elif row["status"] == "running":
        jobs.cancel(run_id)
    return {"run_id": run_id, "status": "cancelling" if row["status"] == "running" else "cancelled"}


def rerun(user: dict, run_id: str) -> dict:
    row = _run_row(run_id)
    if row["pr_id"]:
        pr = db.fetchone("SELECT number FROM pull_requests WHERE pr_id = ?", [row["pr_id"]])
        return start_run(user, row["repo_id"], {"pr_number": pr["number"]})
    return start_run(user, row["repo_id"], {"base_ref": row["base_ref"], "head_ref": row["head_ref"]})


def regenerate_narrative(user: dict, run_id: str) -> dict:
    require(user, "repositories", "write")
    row = _run_row(run_id)
    if row["status"] != "completed":
        raise AppError(409, "The impact run has not completed")
    if not llm.configured():
        raise AppError(503, "No LLM is configured. Add one under Settings > LLM.")
    result = _loads(row["result_json"], {})
    pr = db.fetchone("SELECT * FROM pull_requests WHERE pr_id = ?", [row["pr_id"]]) if row["pr_id"] else None
    changes = [{**s, "flags": _loads(s["flags_json"], []), "logic": _loads(s["logic_json"], {})} for s in db.fetchall("SELECT * FROM pr_changed_symbols WHERE run_id = ?", [run_id])]
    names = {f["feature_id"]: f["name"] for f in features_service.all_for(row["repo_id"])}
    story = pr_logic.narrative(result, dict(pr) if pr else {"title": row["title"], "description": ""}, changes, names)
    db.exec_sql("UPDATE pr_impact_runs SET narrative_json = ?, llm_used = ? WHERE run_id = ?", [json.dumps(story, default=str), story.get("source") == "llm" or bool(row["llm_used"]), run_id])
    return get_run(user, run_id)


def delete_run(user: dict, run_id: str) -> None:
    require(user, "repositories", "write")
    row = _run_row(run_id)
    if row["status"] in ("queued", "running"):
        jobs.cancel(run_id)
    for table in db.PR_RUN_TABLES:
        db.exec_sql(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
    db.exec_sql("DELETE FROM pr_impact_runs WHERE run_id = ?", [run_id])
    from app.services import analyses

    for analysis_id in {row["base_analysis_id"], row["head_analysis_id"]} - {""}:
        owner = db.fetchone("SELECT purpose FROM analyses WHERE analysis_id = ?", [analysis_id])
        still_used = db.fetchone("SELECT run_id FROM pr_impact_runs WHERE base_analysis_id = ? OR head_analysis_id = ?", [analysis_id, analysis_id])
        if owner and owner["purpose"] == "pr" and not still_used:
            analyses.delete_analysis(user, analysis_id)
    if row["pr_id"] and not db.fetchone("SELECT run_id FROM pr_impact_runs WHERE pr_id = ?", [row["pr_id"]]):
        db.exec_sql("DELETE FROM pull_requests WHERE pr_id = ?", [row["pr_id"]])


def delete_for_repo(user: dict, repo_id: str) -> None:
    for row in db.fetchall("SELECT run_id FROM pr_impact_runs WHERE repo_id = ?", [repo_id]):
        delete_run(user, row["run_id"])
    db.exec_sql("DELETE FROM pull_requests WHERE repo_id = ?", [repo_id])
    features_service.delete_for_repo(repo_id)


# --- downloadable report ------------------------------------------------------------------------------------------

REPORT_LIMIT = 150


def _cell(value) -> str:
    """Markdown-table-safe text."""
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def report_filename(run: dict, extension: str) -> str:
    label = f"PR-{run['pull_request']['number']}" if run.get("pull_request") else (run.get("head_ref") or "change")
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label).strip("-")[:60] or "change"
    return f"IMPACT-{safe}-{run['run_id'][:8]}.{extension}"


def run_markdown(user: dict, run_id: str) -> str:
    """The impact run as a Markdown report: summary, risk, changed files and symbols, blast radius, entry points,
    configuration, tests, structure, dependencies, concerns, reviewers and limits."""
    run = get_run(user, run_id)
    if run["status"] != "completed":
        raise AppError(409, f"The impact run is {run['status']}; a report is available once it has completed.")
    r = run.get("result") or {}
    story = run.get("narrative") or {}
    stats = r.get("stats") or {}
    risk = r.get("risk") or {}
    pr = run.get("pull_request") or r.get("pr")
    repo = run.get("repo") or {}
    lines: list[str] = []
    add = lines.append

    def more(items: list, shown: int, noun: str) -> None:
        if len(items) > shown:
            add(f"- … and {len(items) - shown} more {noun} (see the run in Code Analyzer)")

    add(f"# Pull-Request Impact Report — {run.get('title') or run['head_ref'] + ' → ' + run['base_ref']}")
    add("")
    add(f"- **Repository:** {repo.get('name', '')}")
    if pr:
        add(f"- **Pull request:** #{pr.get('number')} {pr.get('title') or ''}" + (f" ({pr['url']})" if pr.get("url") else ""))
        if pr.get("author"):
            add(f"- **Author:** {pr['author']}")
    add(f"- **Change:** `{run['head_ref']}` → `{run['base_ref']}` · merge-base `{(run.get('merge_base_sha') or '')[:10]}` · head `{(run.get('head_sha') or '')[:10]}`")
    add(f"- **Risk:** **{risk.get('label', run.get('risk_label', ''))}** ({risk.get('score', run.get('risk'))})")
    add(f"- **Size:** {stats.get('files', 0)} file(s), +{stats.get('additions', 0)} / −{stats.get('deletions', 0)} lines, {stats.get('symbols', 0)} changed symbol(s), "
        f"{stats.get('impacted', 0)} impacted file(s), {stats.get('entry_points', 0)} entry point(s)")
    add(f"- **Analysed:** {str(run.get('finished_at') or run.get('created_at') or '')[:19].replace('T', ' ')} UTC by {run.get('started_by_name', '')}"
        f" · narrative by {'LLM' if story.get('source') == 'llm' else 'rules'}")
    add("")

    add("## Summary")
    add("")
    add(str(story.get("summary") or "").strip() or "No summary.")
    add("")
    if story.get("risk_explanation"):
        add(f"**Why this risk level:** {story['risk_explanation']}")
        add("")
    if story.get("description_drift"):
        add("### Description vs. diff")
        add("")
        for item in story["description_drift"]:
            add(f"- {item}")
        add("")
    if story.get("review_checklist"):
        add("## Review Checklist")
        add("")
        for item in story["review_checklist"]:
            add(f"- [ ] {item}")
        add("")
    if story.get("features"):
        add("## Features Affected")
        add("")
        for f in story["features"]:
            add(f"- **{f.get('feature')}:** {f.get('summary')}")
        add("")

    add("## Risk Breakdown")
    add("")
    if risk.get("overrides"):
        add("Raised to at least HIGH because: " + "; ".join(risk["overrides"]) + ".")
        add("")
    add("| Factor | Value | Weight | Contribution | Detail |")
    add("| :--- | ---: | ---: | ---: | :--- |")
    for f in risk.get("factors") or []:
        add(f"| {_cell(str(f.get('factor', '')).replace('_', ' '))} | {f.get('value')} | {f.get('weight')} | {f.get('contribution')} | {_cell(f.get('detail'))} |")
    add("")
    if risk.get("hotspots"):
        add("**Hotspots:** " + ", ".join(f"`{h.get('path') if isinstance(h, dict) else h}`" for h in risk["hotspots"][:20]))
        add("")

    files = r.get("files") or []
    add(f"## Changed Files ({len(files)})")
    add("")
    if files:
        add("| File | Status | +/− | Layer | Kinds |")
        add("| :--- | :---: | ---: | :--- | :--- |")
        for f in files[:REPORT_LIMIT]:
            name = f"{f.get('old_path')} → {f['path']}" if f.get("old_path") and f.get("old_path") != f["path"] else f["path"]
            flag = " (sensitive)" if f.get("sensitive") else ""
            add(f"| `{_cell(name)}`{flag} | {f.get('status')} | +{f.get('additions', 0)} / −{f.get('deletions', 0)} | {_cell(f.get('layer'))} | {_cell(', '.join(f.get('change_kinds') or []))} |")
        more(files, REPORT_LIMIT, "file(s)")
        add("")

    symbols = run.get("symbols") or []
    if symbols:
        add(f"## Changed Symbols ({len(symbols)})")
        add("")
        add("| Symbol | Change | Kind | File | Flags |")
        add("| :--- | :---: | :--- | :--- | :--- |")
        for s in symbols[:REPORT_LIMIT]:
            add(f"| `{_cell(s.get('qualified_name'))}` | {s.get('change_type')} | {s.get('kind')} | `{_cell(s.get('path'))}:{s.get('line_start')}` | {_cell(', '.join(s.get('flags') or []))} |")
        more(symbols, REPORT_LIMIT, "symbol(s)")
        add("")
        explained = [s for s in symbols if (s.get("logic") or {}).get("summary")]
        if explained:
            add("### Changed Logic")
            add("")
            for s in explained[:60]:
                logic = s["logic"]
                add(f"#### `{s.get('qualified_name')}` ({s.get('change_type')})")
                add("")
                add(logic.get("summary", ""))
                for rule in logic.get("rules_changed") or []:
                    add(f"- Rule: {rule.get('before') or '—'} → {rule.get('after') or '—'}")
                for effect in logic.get("side_effects") or []:
                    add(f"- Side effect ({effect.get('kind')}, {effect.get('verified')}): {effect.get('detail')}")
                for note in logic.get("risk_notes") or []:
                    add(f"- Risk: {note}")
                for note in logic.get("test_focus") or []:
                    add(f"- Test: {note}")
                add("")

    entries = r.get("entry_points") or []
    if entries:
        add(f"## Affected Entry Points ({len(entries)})")
        add("")
        add("| Entry point | Kind | Reach | Via | Location |")
        add("| :--- | :--- | :--- | :--- | :--- |")
        for e in entries[:REPORT_LIMIT]:
            name = e.get("name", "") + (" (BREAKING)" if e.get("breaking") else "")
            add(f"| `{_cell(name)}` | {_cell(e.get('kind'))} | {_cell(e.get('reach'))} (depth {e.get('depth')}) | `{_cell(e.get('via'))}` | `{_cell(e.get('path'))}:{e.get('line')}` |")
        more(entries, REPORT_LIMIT, "entry point(s)")
        add("")
        contracts = [e for e in entries if e.get("contract_changes")]
        if contracts:
            add("### Contract Changes")
            add("")
            for e in contracts[:60]:
                for change in e["contract_changes"]:
                    add(f"- `{e['name']}`: {change.get('text', '')}" + (" **(breaking)**" if change.get("breaking") else ""))
            add("")

    impacted = r.get("impacted") or []
    if impacted:
        add(f"## Blast Radius — Impacted Files ({len(impacted)})")
        add("")
        add("| File | Depth | Confidence | Via | Reason |")
        add("| :--- | ---: | :--- | :--- | :--- |")
        for i in impacted[:REPORT_LIMIT]:
            add(f"| `{_cell(i.get('path'))}` | {i.get('depth')} | {i.get('confidence')} | `{_cell(i.get('via_path'))}` | {_cell(i.get('reason'))} |")
        more(impacted, REPORT_LIMIT, "impacted file(s)")
        add("")

    config = r.get("config") or {}
    findings, keys = config.get("findings") or [], config.get("keys") or []
    if findings or keys:
        add("## Configuration")
        add("")
        if findings:
            add(f"### Findings ({len(findings)})")
            add("")
            for f in findings[:REPORT_LIMIT]:
                where = " · ".join(x for x in (f.get("environment"), f.get("path")) if x)
                add(f"- **[{str(f.get('severity', '')).upper()}]** {f.get('message')}" + (f" ({where})" if where else ""))
            add("")
        if keys:
            add(f"### Changed Keys ({len(keys)})")
            add("")
            add("| Key | Environment | Change | Before | After | Read by |")
            add("| :--- | :--- | :---: | :--- | :--- | :--- |")
            for k in keys[:REPORT_LIMIT]:
                readers = ", ".join(str(x.get("path") if isinstance(x, dict) else x) for x in (k.get("readers") or [])[:4])
                add(f"| `{_cell(k.get('key'))}` | {_cell(k.get('environment'))} | {k.get('change')} | `{_cell(k.get('before'))}` | `{_cell(k.get('after'))}` | {_cell(readers)} |")
            more(keys, REPORT_LIMIT, "key(s)")
            add("")
        if config.get("external_config"):
            add("Configuration outside the repository (not analysed): " + ", ".join(config["external_config"]))
            add("")

    tests = r.get("tests") or {}
    if tests:
        add("## Tests")
        add("")
        add(f"- Changed functions: {tests.get('functions', 0)} · without any test reaching them: {len(tests.get('gaps') or [])}")
        if tests.get("tests"):
            add("- Tests to run: " + ", ".join(f"`{t.get('path')}`" for t in tests["tests"][:40]))
        add("")
        if tests.get("gaps"):
            add("### Test Gaps")
            add("")
            for g in tests["gaps"][:REPORT_LIMIT]:
                add(f"- `{g.get('symbol')}` ({g.get('change_type')}) in `{g.get('path')}`")
            add("")

    graph = r.get("graph") or {}
    if any(graph.get(k) for k in ("added_count", "removed_count", "layer_violations", "new_cycles", "resolved_cycles", "behaviour")):
        add("## Structure")
        add("")
        add(f"- Dependency edges: +{graph.get('added_count', 0)} / −{graph.get('removed_count', 0)}")
        for v in graph.get("layer_violations") or []:
            add(f"- New layer violation: {v.get('source', v) if isinstance(v, dict) else v}" + (f" → {v.get('target')}" if isinstance(v, dict) and v.get("target") else ""))
        for c in graph.get("new_cycles") or []:
            add(f"- New cycle: {' → '.join(c) if isinstance(c, list) else c}")
        for c in graph.get("resolved_cycles") or []:
            add(f"- Resolved cycle: {' → '.join(c) if isinstance(c, list) else c}")
        for b in (graph.get("behaviour") or [])[:40]:
            add(f"- Behaviour {b.get('change')}: `{b.get('tag')}` in `{b.get('path')}`")
        add("")

    deps = r.get("dependencies") or {}
    if any(deps.get(k) for k in ("added", "removed", "changed")):
        add("## Dependencies")
        add("")
        for d in deps.get("added") or []:
            add(f"- Added `{d.get('name')}` {d.get('version') or d.get('right') or ''}".rstrip())
        for d in deps.get("removed") or []:
            add(f"- Removed `{d.get('name')}`")
        for d in deps.get("changed") or []:
            add(f"- `{d.get('name')}` {d.get('left') or d.get('before') or ''} → {d.get('right') or d.get('after') or ''}" + (" (major)" if d.get("major") else ""))
        add("")

    concerns = r.get("concerns") or {}
    new_concerns, resolved = concerns.get("new") or [], concerns.get("resolved") or []
    if new_concerns or resolved:
        add("## Code-Quality Concerns")
        add("")
        for label, items in (("New", new_concerns), ("Resolved", resolved)):
            if items:
                add(f"### {label} ({len(items)})")
                add("")
                for c in items[:REPORT_LIMIT]:
                    add(f"- **[{str(c.get('severity', '')).upper()}]** {c.get('category')}: {c.get('title')}" + (f" (`{c['path']}`)" if c.get("path") else ""))
                add("")

    contract = openapi_diff(user, run_id)
    if contract.get("available") and contract.get("changes"):
        add(f"## API Contract (OpenAPI) Changes — {contract['breaking']} breaking, {contract['non_breaking']} non-breaking")
        add("")
        add("| Severity | Operation | Where | Change |")
        add("| :--- | :--- | :--- | :--- |")
        for c in sorted(contract["changes"], key=lambda c: (c["severity"] != "breaking", c["operation"]))[:REPORT_LIMIT]:
            add(f"| {c['severity']} | `{_cell(c['operation'])}` | {_cell(c.get('where'))} | {_cell(c['change'])} |")
        more(contract["changes"], REPORT_LIMIT, "change(s)")
        add("")

    cross = run.get("cross_repo") or {}
    if cross.get("in_landscape"):
        consumers = cross.get("consumers") or []
        add(f"## Consumers in Other Repositories ({len(consumers)})")
        add("")
        if consumers:
            add("| Interface | Type | Consumer repository | Consumer | Location | Landscape |")
            add("| :--- | :--- | :--- | :--- | :--- | :--- |")
            for c in consumers[:REPORT_LIMIT]:
                add(f"| `{_cell(c['interface'])}` | {c['kind']} | {_cell(c['consumer_repo'])} | {_cell(c['consumer'])} | `{_cell(c['where'])}` | {_cell(c['landscape'])} |")
            more(consumers, REPORT_LIMIT, "consumer(s)")
        else:
            add("No repository in this repository's system landscapes consumes the affected endpoints or topics.")
        add("")

    reviewers = r.get("reviewers") or []
    if reviewers:
        add("## Suggested Reviewers")
        add("")
        for p in reviewers[:15]:
            add(f"- **{p.get('name')}** ({p.get('source')}) — {len(p.get('files') or [])} file(s)")
        add("")

    if r.get("limits"):
        add("## Limits of This Analysis")
        add("")
        for item in r["limits"]:
            if cross.get("in_landscape") and item.startswith("Consumers in other repositories"):
                item = "Consumers in other repositories are taken from the system landscapes this repository belongs to; repositories outside them are not analysed."
            add(f"- {item}")
        add("")
    return "\n".join(lines).rstrip() + "\n"


def run_pdf(user: dict, run_id: str) -> bytes:
    from app.services import md_pdf

    return md_pdf.render(run_markdown(user, run_id), "Code Analyzer Pull-Request Impact Report")
