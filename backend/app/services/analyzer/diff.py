"""Pull-request workspace: fetch base and head, find the merge-base, parse the diff, export commit snapshots.

Only git plumbing lives here. The PR head is untrusted code (forks included): nothing is built, installed or run.
Snapshots are exported with `git archive` (no .git folder, no credentials) and extracted with tarfile's "data" filter
so a hostile archive cannot write outside its folder.

Remote repositories are fetched into a bare scratch repository with progressively deeper history until base and head
share a merge-base. Local folders that are git repositories are read in place with read-only commands only.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.errors import AppError
from app.services import git_hosts

DEPTHS = (50, 250, 1000, 5000)
MAX_HUNK_LINES = 400  # per file, kept for rules and LLM prompts
MAX_LINE_CHARS = 400


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str = ""
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line number, text)
    removed: list[tuple[int, str]] = field(default_factory=list)  # (old line number, text)

    def to_dict(self) -> dict:
        return {"old_start": self.old_start, "old_count": self.old_count, "new_start": self.new_start, "new_count": self.new_count,
                "header": self.header[:200], "added": [[n, t] for n, t in self.added], "removed": [[n, t] for n, t in self.removed]}

    @staticmethod
    def from_dict(data: dict) -> "Hunk":
        return Hunk(data["old_start"], data["old_count"], data["new_start"], data["new_count"], data.get("header", ""),
                    [(int(n), t) for n, t in data.get("added", [])], [(int(n), t) for n, t in data.get("removed", [])])


@dataclass
class FileDiff:
    path: str
    old_path: str = ""
    status: str = "M"  # A added, D deleted, M modified, R renamed
    binary: bool = False
    additions: int = 0
    deletions: int = 0
    hunks: list[Hunk] = field(default_factory=list)

    def new_lines(self) -> set[int]:
        out: set[int] = set()
        for hunk in self.hunks:
            out.update(range(hunk.new_start, hunk.new_start + max(hunk.new_count, 0)))
        return out

    def old_lines(self) -> set[int]:
        out: set[int] = set()
        for hunk in self.hunks:
            out.update(range(hunk.old_start, hunk.old_start + max(hunk.old_count, 0)))
        return out

    def touches_new(self, start: int, end: int) -> bool:
        """Does any hunk add or change lines in [start, end] of the new file? Pure deletions count at their position."""
        for hunk in self.hunks:
            if hunk.new_count == 0:
                if start <= hunk.new_start <= end or start <= hunk.new_start + 1 <= end:
                    return True
            elif hunk.new_start <= end and start <= hunk.new_start + hunk.new_count - 1:
                return True
        return False

    def touches_old(self, start: int, end: int) -> bool:
        for hunk in self.hunks:
            if hunk.old_count and hunk.old_start <= end and start <= hunk.old_start + hunk.old_count - 1:
                return True
        return False


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def _unquote(path: str) -> str:
    path = path.strip()
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1].encode("utf-8", "replace").decode("unicode_escape").encode("latin-1", "replace").decode("utf-8", "replace")
    return path


def parse_unified(text: str) -> list[FileDiff]:
    """Parses `git diff -M -U0` output into files and hunks with the added / removed line texts."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    kept = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r'^diff --git (?:"?a/(.+?)"?) (?:"?b/(.+?)"?)$', line)
            a, b = (match.group(1), match.group(2)) if match else ("", "")
            current = FileDiff(path=_unquote(b), old_path=_unquote(a))
            files.append(current)
            hunk = None
            kept = 0
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "A"
        elif line.startswith("deleted file mode"):
            current.status = "D"
        elif line.startswith("rename from "):
            current.old_path = _unquote(line[len("rename from "):])
            current.status = "R"
        elif line.startswith("rename to "):
            current.path = _unquote(line[len("rename to "):])
            current.status = "R"
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.binary = True
        elif line.startswith("--- ") and hunk is None:
            if line[4:].strip() == "/dev/null":
                current.status = "A"
        elif line.startswith("+++ ") and hunk is None:
            if line[4:].strip() == "/dev/null":
                current.status = "D"
        elif line.startswith("@@"):
            match = _HUNK.match(line)
            if not match:
                continue
            hunk = Hunk(int(match.group(1)), int(match.group(2)) if match.group(2) is not None else 1,
                        int(match.group(3)), int(match.group(4)) if match.group(4) is not None else 1, match.group(5) or "")
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
        elif hunk is not None and line.startswith("+"):
            current.additions += 1
            if kept < MAX_HUNK_LINES:
                hunk.added.append((new_no, line[1:][:MAX_LINE_CHARS]))
                kept += 1
            new_no += 1
        elif hunk is not None and line.startswith("-"):
            current.deletions += 1
            if kept < MAX_HUNK_LINES:
                hunk.removed.append((old_no, line[1:][:MAX_LINE_CHARS]))
                kept += 1
            old_no += 1
        elif hunk is not None and line.startswith(" "):
            old_no += 1
            new_no += 1
    for item in files:
        if item.status == "M" and item.old_path and item.old_path != item.path:
            item.status = "R"
        if item.status != "R":
            item.old_path = "" if item.status != "D" else item.old_path
        if item.status == "D":
            item.path = item.old_path or item.path
    return files


# --- workspace --------------------------------------------------------------------------------------------------


class Workspace:
    """A git directory that holds the base and head commits of one comparison."""

    def __init__(self, git_dir: Path, ref=None, credential=None, local: bool = False):
        self.git_dir = Path(git_dir).resolve()
        self.ref = ref
        self.credential = credential
        self.local = local

    def git(self, *args: str, check: bool = True, timeout: int | None = None) -> str:
        completed = git_hosts.git_command(self.ref if not self.local else None, self.credential if not self.local else None,
                                          ["-C", str(self.git_dir), *args], timeout=timeout)
        if check and completed.returncode != 0:
            raise AppError(502, f"git {args[0]} failed: {git_hosts.scrub(completed.stderr or completed.stdout)[:400]}")
        return completed.stdout

    def try_git(self, *args: str) -> str | None:
        completed = git_hosts.git_command(self.ref if not self.local else None, self.credential if not self.local else None, ["-C", str(self.git_dir), *args])
        return completed.stdout if completed.returncode == 0 else None

    def resolve(self, name: str) -> str:
        out = self.try_git("rev-parse", "--verify", "--quiet", f"{name}^{{commit}}")
        if not out:
            raise AppError(400, f"'{name}' is not a branch, tag or commit in this repository.")
        return out.strip()

    def merge_base(self, base: str, head: str) -> str | None:
        out = self.try_git("merge-base", base, head)
        return out.strip() if out else None

    def commit_facts(self, sha: str) -> dict:
        out = self.try_git("log", "-1", "--format=%H%n%an%n%aI%n%s", sha) or ""
        lines = out.splitlines() + ["", "", "", ""]
        return {"commit_sha": lines[0] or sha, "commit_author": lines[1], "commit_date": lines[2], "commit_message": lines[3]}

    def diff(self, base: str, head: str) -> list[FileDiff]:
        text = self.git("diff", "--no-color", "--no-ext-diff", "-M", "-U0", base, head, timeout=600)
        return parse_unified(text)

    def export(self, sha: str, target: Path) -> Path:
        """Writes the tree of `sha` into `target` (no .git, safe extraction)."""
        target = Path(target).resolve()
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        archive = (target.parent / f"{target.name}.tar").resolve()
        self.git("archive", "--format=tar", "-o", str(archive), sha, timeout=900)
        try:
            with tarfile.open(archive) as bundle:
                bundle.extractall(target, filter="data")
        finally:
            archive.unlink(missing_ok=True)
        return target

    def history(self, head: str, paths: list[str], days: int = 180, limit: int = 2000) -> dict[str, dict]:
        """Commits and authors per path in the fetched history (approximate when the history is shallow)."""
        if not paths:
            return {}
        out = self.try_git("log", f"--since={days}.days", f"--max-count={limit}", "--no-merges", "--format=@@%an <%ae>", "--name-only", head, "--", *paths[:400]) or ""
        stats: dict[str, dict] = {}
        author = ""
        wanted = set(paths)
        for line in out.splitlines():
            if line.startswith("@@"):
                author = line[2:].strip()
                continue
            path = line.strip()
            if path and path in wanted:
                entry = stats.setdefault(path, {"commits": 0, "authors": {}})
                entry["commits"] += 1
                entry["authors"][author] = entry["authors"].get(author, 0) + 1
        return stats

    def is_shallow(self) -> bool:
        return (self.try_git("rev-parse", "--is-shallow-repository") or "").strip() == "true"


def local_workspace(path: Path) -> Workspace:
    if not (path / ".git").exists():
        raise AppError(400, "This folder is not a git repository, so there are no branches to compare. Register a folder that contains a .git directory.")
    return Workspace(path, local=True)


def remote_workspace(git_dir: Path, ref, credential, fetches: list[tuple[str, str, str]], log=lambda _m: None) -> Workspace:
    """Bare scratch repository with each (url, remote ref, local ref) fetched shallowly, deepened until they connect.

    fetches[0] is the base and fetches[1] the head; both end up at refs/ca/base and refs/ca/head.
    """
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)
    git_dir.mkdir(parents=True, exist_ok=True)
    workspace = Workspace(git_dir, ref, credential)
    workspace.git("init", "--bare", "--quiet")
    depth = DEPTHS[0]
    for url, remote_ref, local_ref in fetches:
        _fetch(workspace, url, remote_ref, local_ref, ["--depth", str(depth)])
    base, head = workspace.resolve(fetches[0][2]), workspace.resolve(fetches[1][2])
    for next_depth in DEPTHS[1:] + (0,):
        if workspace.merge_base(base, head):
            break
        if next_depth:
            log(f"No merge-base within {depth} commits; deepening history to {next_depth}")
            for url, remote_ref, local_ref in fetches:
                _fetch(workspace, url, remote_ref, local_ref, ["--deepen", str(next_depth - depth)])
            depth = next_depth
        else:
            log("Fetching the full history to find the merge-base")
            for url, remote_ref, local_ref in fetches:
                _fetch(workspace, url, remote_ref, local_ref, ["--unshallow"] if workspace.is_shallow() else [])
    return workspace


def _fetch(workspace: Workspace, url: str, remote_ref: str, local_ref: str, extra: list[str]) -> None:
    completed = git_hosts.git_command(workspace.ref, workspace.credential, ["-C", str(workspace.git_dir), "fetch", "--no-tags", "--quiet", *extra, url, f"+{remote_ref}:{local_ref}"])
    if completed.returncode != 0:
        error = git_hosts.scrub(completed.stderr or completed.stdout)
        lowered = error.lower()
        if "couldn't find remote ref" in lowered or "not our ref" in lowered:
            raise AppError(400, f"{remote_ref.replace('refs/heads/', '')} was not found on the remote.")
        if any(word in lowered for word in ("authentication", "403", "401", "could not read username", "terminal prompts")):
            raise AppError(403, "Access to the repository was refused while fetching. Sign in to the Git host again.", extra={"status": "auth_required"})
        raise AppError(502, f"git fetch failed: {error[:400]}")


# --- CODEOWNERS -------------------------------------------------------------------------------------------------


def read_codeowners(root: Path) -> list[tuple[str, list[str]]]:
    for candidate in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS", ".bitbucket/CODEOWNERS"):
        path = root / candidate
        if path.is_file():
            rules = []
            for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line or line.startswith("["):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    rules.append((parts[0], parts[1:]))
            return rules
    return []


def codeowners_for(rules: list[tuple[str, list[str]]], path: str) -> list[str]:
    """Last matching rule wins, as on GitHub / GitLab / Bitbucket."""
    owners: list[str] = []
    for pattern, names in rules:
        if _co_match(pattern, path):
            owners = names
    return owners


def _co_match(pattern: str, path: str) -> bool:
    anchored = pattern.startswith("/")
    pat = pattern.lstrip("/")
    if pat.endswith("/"):
        pat += "**"
    if "/" not in pat.rstrip("*") and not anchored:
        return any(fnmatch.fnmatch(part, pat) for part in PurePosixPath(path).parts) or fnmatch.fnmatch(path, f"**/{pat}")
    regex = re.escape(pat).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
    return bool(re.match(f"^{regex}(?:/.*)?$", path))
