"""Repositories (GitHub, Bitbucket Cloud/Server, any Git URL, uploaded archive, local folder) and starting analyses."""

from __future__ import annotations

import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from app import config, db
from app.errors import AppError
from app.permissions import require
from app.services import git_credentials, git_hosts, jobs

REMOTE = (git_hosts.GITHUB, git_hosts.BITBUCKET, git_hosts.BITBUCKET_SERVER, git_hosts.GIT)


def _payload(row: dict, latest: dict | None = None, counts: dict | None = None, branch_runs: list[dict] | None = None) -> dict:
    data = {key: row[key] for key in ("repo_id", "name", "source_type", "url", "host", "owner", "slug", "default_branch", "description", "is_private", "created_by", "created_at", "updated_at")}
    data["provider_label"] = git_hosts.PROVIDER_LABELS.get(row["source_type"], {"zip": "Archive upload", "local": "Local folder"}.get(row["source_type"], row["source_type"]))
    data["web_url"] = git_hosts.parse_url(row["url"]).web_url if row["source_type"] in REMOTE else ""
    data["latest_analysis"] = latest
    data["analysis_count"] = (counts or {}).get(row["repo_id"], 0)
    data["branch_runs"] = branch_runs if branch_runs is not None else ([latest] if latest else [])
    return data


def _analyses_by_repo() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    rows = db.fetchall(
        """
        SELECT analysis_id, repo_id, branch, commit_sha, commit_message, status, stage, progress, message, health_score, file_count, loc_total, created_at, finished_at, architecture_style
        FROM analyses WHERE coalesce(purpose, 'branch') = 'branch' ORDER BY created_at DESC
        """
    )
    latest: dict[str, dict] = {}
    by_branch: dict[str, dict[str, dict]] = {}
    branch_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        rid = row["repo_id"]
        bname = row["branch"] or ""
        latest.setdefault(rid, row)
        branch_counts[(rid, bname)] = branch_counts.get((rid, bname), 0) + 1
        repo_branches = by_branch.setdefault(rid, {})
        if bname not in repo_branches:
            repo_branches[bname] = dict(row)
    branches_list: dict[str, list[dict]] = {}
    for rid, bmap in by_branch.items():
        items: list[dict] = []
        for bname, entry in bmap.items():
            entry["run_count"] = branch_counts.get((rid, bname), 1)
            items.append(entry)
        branches_list[rid] = items
    return latest, branches_list


def list_repositories(user: dict) -> list[dict]:
    require(user, "repositories", "read")
    latest, branch_runs = _analyses_by_repo()
    counts = {row["repo_id"]: int(row["c"]) for row in db.fetchall("SELECT repo_id, count(*) AS c FROM analyses WHERE coalesce(purpose, 'branch') = 'branch' GROUP BY repo_id")}
    rows = db.fetchall("SELECT * FROM repositories ORDER BY updated_at DESC")
    return [_payload(row, latest.get(row["repo_id"]), counts, branch_runs.get(row["repo_id"], [])) for row in rows]


def get_repository(user: dict, repo_id: str) -> dict:
    require(user, "repositories", "read")
    row = _row(repo_id)
    analyses = db.fetchall(
        """
        SELECT analysis_id, branch, commit_sha, commit_message, status, stage, progress, message, health_score, file_count, loc_total,
               architecture_style, llm_used, started_by, created_at, started_at, finished_at
        FROM analyses WHERE repo_id = ? AND coalesce(purpose, 'branch') = 'branch' ORDER BY created_at DESC
        """,
        [repo_id],
    )
    names = {u["user_id"]: u["username"] for u in db.fetchall("SELECT user_id, username FROM users")}
    for item in analyses:
        item["started_by_name"] = names.get(item["started_by"], item["started_by"])
    payload = _payload(row, analyses[0] if analyses else None, {repo_id: len(analyses)})
    payload["analyses"] = analyses
    return payload


def _row(repo_id: str) -> dict:
    row = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [repo_id])
    if row is None:
        raise AppError(404, "Repository not found")
    return row


def check_existing(user: dict, body: dict) -> dict:
    """Check whether a repository being added already exists, and compare its version."""
    require(user, "repositories", "read")
    source_type = str(body.get("source_type") or "remote").strip()
    url = str(body.get("url") or "").strip()
    name = str(body.get("name") or "").strip()
    branch = str(body.get("branch") or "").strip()
    commit_sha = str(body.get("commit_sha") or "").strip()
    file_count = body.get("file_count")
    total_bytes = body.get("total_bytes")

    existing_repo = None
    ref = None
    if source_type in ("remote", "github", "bitbucket", "bitbucket_server", "git") and url:
        try:
            ref = git_hosts.parse_url(url)
            existing_repo = db.fetchone(
                "SELECT * FROM repositories WHERE lower(url) = lower(?) OR (lower(host) = lower(?) AND lower(owner) = lower(?) AND lower(slug) = lower(?))",
                [ref.web_url, ref.host, ref.owner, ref.slug],
            )
        except Exception:
            existing_repo = db.fetchone("SELECT * FROM repositories WHERE lower(url) = lower(?)", [url])
    elif name:
        existing_repo = db.fetchone("SELECT * FROM repositories WHERE lower(name) = lower(?)", [name])

    if not existing_repo and url:
        existing_repo = db.fetchone("SELECT * FROM repositories WHERE lower(url) = lower(?)", [url])

    if not existing_repo and name:
        existing_repo = db.fetchone("SELECT * FROM repositories WHERE lower(name) = lower(?)", [name])

    if not existing_repo:
        return {"exists": False}

    repo_id = existing_repo["repo_id"]
    latest_analysis = None
    if branch:
        latest_analysis = db.fetchone(
            "SELECT * FROM analyses WHERE repo_id = ? AND branch = ? AND coalesce(purpose, 'branch') = 'branch' ORDER BY created_at DESC LIMIT 1",
            [repo_id, branch],
        )
    if not latest_analysis:
        latest_analysis = db.fetchone(
            "SELECT * FROM analyses WHERE repo_id = ? AND coalesce(purpose, 'branch') = 'branch' ORDER BY created_at DESC LIMIT 1",
            [repo_id],
        )

    if not latest_analysis:
        return {
            "exists": True,
            "repo": {
                "repo_id": existing_repo["repo_id"],
                "name": existing_repo["name"],
                "source_type": existing_repo["source_type"],
                "url": existing_repo["url"],
                "default_branch": existing_repo["default_branch"],
                "created_at": str(existing_repo["created_at"]),
            },
            "latest_analysis": None,
            "version_status": "existing_repo_no_runs",
            "version_label": "Existing repository (no previous runs)",
            "message": f"Repository '{existing_repo['name']}' already exists, but has not completed any analyses yet.",
            "differences": {
                "current_branch": branch,
                "existing_branch": None,
                "current_sha": commit_sha,
                "existing_sha": None,
            },
        }

    existing_sha = (latest_analysis.get("commit_sha") or "").strip()
    existing_branch = (latest_analysis.get("branch") or "").strip()
    existing_date_str = latest_analysis.get("commit_date") or ""

    version_status = "different"
    version_label = "Different version"
    message = f"Repository '{existing_repo['name']}' already exists in workspace."

    if commit_sha:
        if existing_sha and commit_sha.lower() == existing_sha.lower():
            version_status = "same"
            version_label = "Same version"
            message = f"Repository '{existing_repo['name']}' already has an analysis with the exact same commit ({commit_sha[:7]}) on branch '{existing_branch}'."
        elif existing_sha:
            remote_date_str = None
            if ref:
                try:
                    cred = git_credentials.credential_for(user["user_id"], ref)
                    if ref.provider == git_hosts.GITHUB:
                        res = git_hosts._get(f"{ref.api_base}/repos/{ref.owner}/{ref.slug}/commits/{commit_sha}", cred, ref.provider)
                        if res.status_code == 200:
                            remote_date_str = (res.json().get("commit") or {}).get("author", {}).get("date")
                    elif ref.provider == git_hosts.BITBUCKET:
                        res = git_hosts._get(f"{ref.api_base}/repositories/{ref.owner}/{ref.slug}/commit/{commit_sha}", cred, ref.provider)
                        if res.status_code == 200:
                            remote_date_str = res.json().get("date")
                except Exception:
                    pass

            if remote_date_str and existing_date_str:
                try:
                    d_remote = datetime.fromisoformat(remote_date_str.replace("Z", "+00:00"))
                    d_exist = datetime.fromisoformat(existing_date_str.replace("Z", "+00:00"))
                    if d_remote > d_exist:
                        version_status = "newer"
                        version_label = "Newer version"
                        message = f"Repository '{existing_repo['name']}' has a newer commit ({commit_sha[:7]}) than the previous analysis ({existing_sha[:7]})."
                    elif d_remote < d_exist:
                        version_status = "older"
                        version_label = "Older version"
                        message = f"Repository '{existing_repo['name']}' selected commit ({commit_sha[:7]}) is older than the previous analysis ({existing_sha[:7]})."
                    else:
                        version_status = "different"
                        version_label = "Different version"
                        message = f"Repository '{existing_repo['name']}' commit ({commit_sha[:7]}) differs from previous analysis ({existing_sha[:7]})."
                except Exception:
                    version_status = "different"
                    version_label = "Different version"
                    message = f"Repository '{existing_repo['name']}' commit ({commit_sha[:7]}) differs from previous analysis ({existing_sha[:7]})."
            else:
                if branch and existing_branch and branch != existing_branch:
                    version_status = "different"
                    version_label = f"Different branch ('{branch}' vs '{existing_branch}')"
                    message = f"Repository '{existing_repo['name']}' exists with branch '{existing_branch}'. You are adding branch '{branch}'."
                else:
                    version_status = "different"
                    version_label = "Different commit"
                    message = f"Repository '{existing_repo['name']}' commit ({commit_sha[:7]}) differs from previous analysis ({existing_sha[:7]})."
        else:
            version_status = "different"
            version_label = "New commit"
            message = f"Repository '{existing_repo['name']}' already exists; new commit ({commit_sha[:7]}) will be analysed."
    elif file_count is not None:
        existing_files = latest_analysis.get("file_count") or 0
        if existing_files > 0:
            if file_count == existing_files:
                version_status = "same"
                version_label = "Same version (identical file count)"
                message = f"Repository '{existing_repo['name']}' already exists with {existing_files} files (analysed on {str(latest_analysis['created_at'])[:10]})."
            elif file_count > existing_files:
                version_status = "newer"
                version_label = "Newer / larger version"
                message = f"Repository '{existing_repo['name']}' has {file_count} files (increased from {existing_files} files previously analysed)."
            else:
                version_status = "older"
                version_label = "Older / smaller version"
                message = f"Repository '{existing_repo['name']}' has {file_count} files (decreased from {existing_files} files previously analysed)."
        else:
            version_status = "different"
            version_label = "Existing repository"
            message = f"Repository '{existing_repo['name']}' already exists with prior analyses."
    elif branch and existing_branch and branch == existing_branch:
        version_status = "same"
        version_label = "Same branch"
        message = f"Repository '{existing_repo['name']}' already exists on branch '{branch}'."
    else:
        version_status = "different"
        version_label = "Existing repository"
        message = f"Repository '{existing_repo['name']}' already exists with prior analyses."

    return {
        "exists": True,
        "repo": {
            "repo_id": existing_repo["repo_id"],
            "name": existing_repo["name"],
            "source_type": existing_repo["source_type"],
            "url": existing_repo["url"],
            "default_branch": existing_repo["default_branch"],
            "created_at": str(existing_repo["created_at"]),
        },
        "latest_analysis": {
            "analysis_id": latest_analysis["analysis_id"],
            "branch": latest_analysis.get("branch") or "",
            "commit_sha": latest_analysis.get("commit_sha") or "",
            "commit_message": latest_analysis.get("commit_message") or "",
            "commit_date": latest_analysis.get("commit_date") or "",
            "health_score": latest_analysis.get("health_score"),
            "file_count": latest_analysis.get("file_count"),
            "loc_total": latest_analysis.get("loc_total"),
            "created_at": str(latest_analysis["created_at"]),
        },
        "version_status": version_status,
        "version_label": version_label,
        "message": message,
        "differences": {
            "current_branch": branch,
            "existing_branch": existing_branch,
            "current_sha": commit_sha,
            "existing_sha": existing_sha,
            "current_file_count": file_count,
            "existing_file_count": latest_analysis.get("file_count"),
        },
    }


def create_remote(user: dict, body: dict) -> dict:
    """Register a GitHub / Bitbucket / Git repository after confirming the user can read it."""
    require(user, "repositories", "write")
    ref = git_hosts.parse_url(str(body.get("url") or ""))
    credential = git_credentials.credential_for(user["user_id"], ref)
    access = git_hosts.check_access(ref, credential)
    if access["status"] != "ok":
        raise AppError(403, access["message"] or "Cannot read this repository", extra={"status": access["status"], "repo": access["repo"]})
    existing = db.fetchone("SELECT * FROM repositories WHERE lower(url) = lower(?)", [ref.web_url])
    if existing:
        repo_id = existing["repo_id"]
    else:
        repo_id = str(uuid.uuid4())
        now = db.now()
        db.insert(
            "repositories",
            {
                "repo_id": repo_id,
                "name": str(body.get("name") or ref.name).strip()[:255],
                "source_type": ref.provider,
                "url": ref.web_url,
                "host": ref.host,
                "owner": ref.owner,
                "slug": ref.slug,
                "default_branch": access.get("default_branch") or "",
                "description": str(body.get("description") or access.get("description") or "")[:4000],
                "is_private": bool(access.get("is_private")),
                "created_by": user["user_id"],
                "created_at": now,
                "updated_at": now,
            },
        )
    result = get_repository(user, repo_id)
    branch = str(body.get("branch") or "").strip()
    if body.get("analyze", True):
        result["started_analysis"] = start_analysis(user, repo_id, {"branch": branch or result["default_branch"]})
    return result


def create_local(user: dict, body: dict) -> dict:
    require(user, "repositories", "write")
    from app.services.analyzer import ingest

    path = ingest.check_local_path(str(body.get("path") or ""))
    repo_id = str(uuid.uuid4())
    now = db.now()
    db.insert(
        "repositories",
        {
            "repo_id": repo_id, "name": str(body.get("name") or path.name)[:255], "source_type": "local", "url": str(path),
            "host": "", "owner": "", "slug": path.name, "default_branch": "", "description": str(body.get("description") or "")[:4000],
            "is_private": True, "created_by": user["user_id"], "created_at": now, "updated_at": now,
        },
    )
    result = get_repository(user, repo_id)
    result["started_analysis"] = start_analysis(user, repo_id, {"branch": ""})
    return result


def create_upload(user: dict, name: str, filename: str, stream, repo_id: str | None = None) -> dict:
    """Store an uploaded .zip/.tar.gz (new repository, or a new version of an existing archive repository)."""
    require(user, "repositories", "write")
    lowered = (filename or "").lower()
    if not lowered.endswith((".zip", ".tar.gz", ".tgz", ".tar")):
        raise AppError(400, "Upload a .zip, .tar.gz or .tgz archive")
    now = db.now()
    if repo_id:
        row = _row(repo_id)
        if row["source_type"] != "zip":
            raise AppError(400, "Only archive repositories accept uploads")
    else:
        repo_id = str(uuid.uuid4())
        clean_name = (name or re.sub(r"\.(zip|tar\.gz|tgz|tar)$", "", filename, flags=re.I)).strip()[:255] or "archive"
        db.insert(
            "repositories",
            {
                "repo_id": repo_id, "name": clean_name, "source_type": "zip", "url": "", "host": "", "owner": "", "slug": clean_name,
                "default_branch": "", "description": "", "is_private": True, "created_by": user["user_id"], "created_at": now, "updated_at": now,
            },
        )
    analysis_id = str(uuid.uuid4())
    folder = config.data_dir() / "uploads" / repo_id
    folder.mkdir(parents=True, exist_ok=True)
    suffix = ".tar.gz" if lowered.endswith(".tar.gz") else Path(lowered).suffix
    target = folder / f"{analysis_id}{suffix}"
    size = 0
    with open(target, "wb") as sink:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            size += len(block)
            if size > config.max_upload_bytes():
                sink.close()
                target.unlink(missing_ok=True)
                raise AppError(413, "The archive is larger than ANALYSIS_MAX_UPLOAD_MB")
            sink.write(block)
    shutil.copyfile(target, folder / f"latest{suffix}")
    result = get_repository(user, repo_id)
    result["started_analysis"] = _queue(user, repo_id, "", analysis_id)
    return result


def _clean_rel_path(raw: str) -> str:
    cleaned = raw.replace("\\", "/").lstrip("/")
    parts = [part for part in PurePosixPath(cleaned).parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise AppError(400, f"Invalid file path in folder upload: {raw[:200]}")
    return "/".join(parts)


def create_folder_upload(user: dict, name: str, files: list, paths: list[str], repo_id: str | None = None) -> dict:
    """Store a browser-selected local folder (files + relative paths) as a repository archive."""
    require(user, "repositories", "write")
    if not files:
        raise AppError(400, "Select a folder with at least one file")
    rel_paths = [
        _clean_rel_path(paths[idx] if idx < len(paths) and paths[idx] else (getattr(upload, "filename", "") or f"file_{idx}"))
        for idx, upload in enumerate(files)
    ]
    first_parts = rel_paths[0].split("/")
    top_folder = first_parts[0] if len(first_parts) > 1 else ""
    now = db.now()
    if repo_id:
        row = _row(repo_id)
        if row["source_type"] not in ("local", "zip"):
            raise AppError(400, "Only local folder or archive repositories accept folder uploads")
    else:
        repo_id = str(uuid.uuid4())
        clean_name = (name or top_folder or "local-folder").strip()[:255]
        db.insert(
            "repositories",
            {
                "repo_id": repo_id, "name": clean_name, "source_type": "local", "url": "", "host": "", "owner": "", "slug": clean_name,
                "default_branch": "", "description": "", "is_private": True, "created_by": user["user_id"], "created_at": now, "updated_at": now,
            },
        )
    analysis_id = str(uuid.uuid4())
    folder = config.data_dir() / "uploads" / repo_id
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{analysis_id}.zip"
    max_bytes = config.max_upload_bytes()
    total_size = 0
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for upload, rel_path in zip(files, rel_paths):
                with bundle.open(rel_path, "w") as sink:
                    while True:
                        block = upload.file.read(1 << 20)
                        if not block:
                            break
                        total_size += len(block)
                        if total_size > max_bytes:
                            raise AppError(413, "The selected folder is larger than ANALYSIS_MAX_UPLOAD_MB")
                        sink.write(block)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    shutil.copyfile(target, folder / "latest.zip")
    result = get_repository(user, repo_id)
    result["started_analysis"] = _queue(user, repo_id, "", analysis_id)
    return result


def update_repository(user: dict, repo_id: str, body: dict) -> dict:
    require(user, "repositories", "write")
    row = _row(repo_id)
    name = str(body.get("name", row["name"]) or "").strip()[:255] or row["name"]
    description = str(body.get("description", row["description"]) or "")[:4000]
    default_branch = str(body.get("default_branch", row["default_branch"]) or "").strip()[:255]
    db.exec_sql("UPDATE repositories SET name = ?, description = ?, default_branch = ?, updated_at = ? WHERE repo_id = ?", [name, description, default_branch, db.now(), repo_id])
    return get_repository(user, repo_id)


def delete_repository(user: dict, repo_id: str) -> None:
    require(user, "repositories", "write")
    _row(repo_id)
    from app.services import analyses, pr_impact

    pr_impact.delete_for_repo(user, repo_id)
    for row in db.fetchall("SELECT analysis_id FROM analyses WHERE repo_id = ?", [repo_id]):
        analyses.delete_analysis(user, row["analysis_id"])
    db.exec_sql("DELETE FROM repositories WHERE repo_id = ?", [repo_id])
    from app.services import landscape

    landscape.delete_for_repo(repo_id)
    shutil.rmtree(config.data_dir() / "uploads" / repo_id, ignore_errors=True)


def branches(user: dict, repo_id: str) -> dict:
    require(user, "repositories", "read")
    row = _row(repo_id)
    if row["source_type"] not in REMOTE:
        return {"default_branch": "", "branches": []}
    ref = git_hosts.parse_url(row["url"])
    return git_hosts.list_branches(ref, git_credentials.credential_for(user["user_id"], ref))


def start_analysis(user: dict, repo_id: str, body: dict) -> dict:
    require(user, "repositories", "write")
    row = _row(repo_id)
    branch = str(body.get("branch") or "").strip()[:255]
    if row["source_type"] in REMOTE:
        ref = git_hosts.parse_url(row["url"])
        credential = git_credentials.credential_for(user["user_id"], ref)
        access = git_hosts.check_access(ref, credential)
        if access["status"] != "ok":
            raise AppError(403, access["message"] or "Cannot read this repository", extra={"status": access["status"], "repo": access["repo"]})
        branch = branch or access.get("default_branch") or row["default_branch"]
    if row["source_type"] == "zip":
        latest = sorted((config.data_dir() / "uploads" / repo_id).glob("latest.*"))
        if not latest:
            raise AppError(400, "Upload the archive again to analyse it")
        analysis_id = str(uuid.uuid4())
        shutil.copyfile(latest[0], latest[0].with_name(analysis_id + "".join(latest[0].suffixes)))
        return _queue(user, repo_id, branch, analysis_id)
    if row["source_type"] == "local":
        latest = sorted((config.data_dir() / "uploads" / repo_id).glob("latest.*"))
        if latest:
            analysis_id = str(uuid.uuid4())
            shutil.copyfile(latest[0], latest[0].with_name(analysis_id + "".join(latest[0].suffixes)))
            return _queue(user, repo_id, branch, analysis_id)
    return _queue(user, repo_id, branch, str(uuid.uuid4()))


def _queue(user: dict, repo_id: str, branch: str, analysis_id: str) -> dict:
    active = db.fetchone("SELECT analysis_id FROM analyses WHERE repo_id = ? AND branch = ? AND status IN ('queued', 'running') AND coalesce(purpose, 'branch') = 'branch'", [repo_id, branch])
    if active:
        raise AppError(409, "An analysis of this branch is already running", extra={"analysis_id": active["analysis_id"]})
    now = db.now()
    db.insert(
        "analyses",
        {
            "analysis_id": analysis_id, "repo_id": repo_id, "branch": branch, "status": "queued", "stage": "Queued", "progress": 0,
            "message": "Waiting for a worker", "started_by": user["user_id"], "created_at": now, "stats_json": json.dumps({}),
        },
    )
    db.exec_sql("UPDATE repositories SET updated_at = ? WHERE repo_id = ?", [now, repo_id])
    jobs.submit(analysis_id)
    return db.fetchone("SELECT analysis_id, repo_id, branch, status, stage, progress, created_at FROM analyses WHERE analysis_id = ?", [analysis_id])
