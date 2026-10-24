"""Pull-request impact analysis: diff parsing, config rules, and an end-to-end run on a local git repository."""

import subprocess
import textwrap
from pathlib import Path

import pytest
from app.services.analyzer import config_impact, diff

from tests.conftest import login


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                               env={"GIT_AUTHOR_NAME": "Dev One", "GIT_AUTHOR_EMAIL": "dev1@example.com", "GIT_COMMITTER_NAME": "Dev One",
                                    "GIT_COMMITTER_EMAIL": "dev1@example.com", "HOME": str(root), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    return completed.stdout


def write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


@pytest.fixture
def git_repo(sample_repo: Path) -> Path:
    root = sample_repo
    write(root, "config/application.yml", """
        orders:
          timeout-ms: 3000
          retry:
            enabled: true
          base-url: https://pricing.internal
        server:
          port: 8080
    """)
    write(root, "config/application-prod.yml", """
        orders:
          timeout-ms: 5000
        cors:
          allowed-origins: https://shop.example.com
    """)
    write(root, "app/services/settings.py", """
        import os

        TIMEOUT = os.getenv("ORDERS_TIMEOUT_MS", "3000")
    """)
    write(root, "CODEOWNERS", "app/services/ @orders-team\n*.yml @platform\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "checkout", "-q", "-b", "feature/cap-orders")
    write(root, "app/services/order_service.py", """
        from app.data.order_repository import OrderRepository


        class OrderService:
            def __init__(self, repo: OrderRepository):
                self.repo = repo

            def find(self, order_id, include_closed=False):
                order = self.repo.get(order_id)
                if order and (order.status == "open" or include_closed):
                    self.repo.save(order)
                    return order
                return None

            def cancel(self, order_id):
                order = self.repo.get(order_id)
                order.status = "cancelled"
                self.repo.save(order)
                return order
    """)
    write(root, "config/application.yml", """
        server:
          port: 8080
        orders:
          retry:
            enabled: false
          timeout-ms: 30000
          base-url: https://pricing-v2.internal
          new-flag: true
    """)
    write(root, "config/application-prod.yml", """
        orders:
          timeout-ms: 5000
        cors:
          allowed-origins: "*"
    """)
    write(root, "app/services/notify.py", 'API_KEY = "sk_live_9f8e7d6c5b4a3210"\n\ndef notify():\n    return API_KEY\n')
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Cap orders and change timeouts")
    git(root, "checkout", "-q", "main")
    return root


def test_parse_unified_diff_with_rename_and_hunks():
    text = textwrap.dedent("""
        diff --git a/src/a.py b/src/a.py
        index 1..2 100644
        --- a/src/a.py
        +++ b/src/a.py
        @@ -3,2 +3,3 @@ def f():
        -    return 1
        -    pass
        +    x = 2
        +    return x
        +    # done
        @@ -10,0 +12,1 @@
        +print(1)
        diff --git a/old.py b/new.py
        similarity index 90%
        rename from old.py
        rename to new.py
        @@ -1 +1 @@
        -a
        +b
        diff --git a/gone.txt b/gone.txt
        deleted file mode 100644
        --- a/gone.txt
        +++ /dev/null
        @@ -1,2 +0,0 @@
        -x
        -y
        diff --git a/img.png b/img.png
        new file mode 100644
        Binary files /dev/null and b/img.png differ
    """).lstrip("\n")
    files = {f.path: f for f in diff.parse_unified(text)}
    a = files["src/a.py"]
    assert a.status == "M" and a.additions == 4 and a.deletions == 2
    assert a.hunks[0].added[0] == (3, "    x = 2") and a.hunks[0].removed[1] == (4, "    pass")
    assert a.touches_new(4, 5) and not a.touches_new(20, 30) and a.touches_new(12, 12)
    assert files["new.py"].status == "R" and files["new.py"].old_path == "old.py"
    assert files["gone.txt"].status == "D" and files["gone.txt"].deletions == 2
    assert files["img.png"].status == "A" and files["img.png"].binary


def test_config_rules_and_environment_resolution():
    assert config_impact.environment_of("src/main/resources/application-prod.yml") == "prod"
    assert config_impact.environment_of(".env.production") == "prod"
    assert config_impact.environment_of("deploy/overlays/uat/values.yaml") == "uat"
    assert config_impact.environment_of("config/application.yml") == "default"
    assert config_impact.number("30s", "timeout") == 30 and config_impact.number("500ms", "timeout") == 0.5
    assert config_impact.number("512m", "resources.limits.memory") == 512e6
    base = "a:\n  timeout: 1000\n  tls:\n    verify: true\nfeature:\n  checkout:\n    enabled: false\n"
    head = "a:\n  timeout: 9000\n  tls:\n    verify: false\nfeature:\n  checkout:\n    enabled: true\n"
    fd = diff.FileDiff("config/application.yml", status="M", hunks=[diff.Hunk(2, 1, 2, 1, added=[(2, "  timeout: 9000")], removed=[(2, "  timeout: 1000")])])
    result = config_impact.analyse([fd], {"config/application.yml": base}, {"config/application.yml": head}, {}, set())
    rules = {f["rule"] for f in result.findings}
    assert {"numeric-factor", "security-loosened", "feature-flag"} <= rules
    assert result.environments["default"]["changed"] == 3
    readers = config_impact.find_readers(["orders.timeout-ms", "API_TOKEN"], {
        "A.java": 'class A { @Value("${orders.timeout-ms}") int t; }', "b.py": 'import os\nx = os.getenv("API_TOKEN")\n'})
    assert readers["orders.timeout-ms"][0]["path"] == "A.java" and readers["API_TOKEN"][0]["line"] == 2


def test_codeowners_matching():
    rules = [("*", ["@all"]), ("app/services/", ["@orders"]), ("*.yml", ["@platform"]), ("/docs/**", ["@docs"])]
    assert diff.codeowners_for(rules, "app/services/order_service.py") == ["@orders"]
    assert diff.codeowners_for(rules, "config/application.yml") == ["@platform"]
    assert diff.codeowners_for(rules, "docs/a/b.md") == ["@docs"]
    assert diff.codeowners_for(rules, "README.md") == ["@all"]


def test_impact_run_on_local_git_branches(client, admin, git_repo):
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(git_repo), "name": "orders-git"}, headers=admin)
    assert response.status_code == 200, response.text
    repo_id = response.json()["repo_id"]
    refs = client.get(f"/api/repositories/{repo_id}/refs", headers=admin).json()
    assert {"main", "feature/cap-orders"} <= {r["name"] for r in refs["refs"]}
    prs = client.get(f"/api/repositories/{repo_id}/pull-requests", headers=admin).json()
    assert prs["supported"] is False

    started = client.post(f"/api/repositories/{repo_id}/impact-runs", json={"base_ref": "main", "head_ref": "feature/cap-orders"}, headers=admin)
    assert started.status_code == 200, started.text
    run = client.get(f"/api/impact-runs/{started.json()['run_id']}", headers=admin).json()
    assert run["status"] == "completed", run["error"]
    result = run["result"]
    files = {f["path"]: f for f in result["files"]}
    assert files["app/services/order_service.py"]["status"] == "M" and "code" in files["app/services/order_service.py"]["change_kinds"]
    assert files["config/application.yml"]["change_kinds"] == ["config"]
    # symbols: find modified with a new signature, cancel added; db-write appears in find
    symbols = {s["qualified_name"]: s for s in run["symbols"]}
    assert symbols["OrderService.find"]["change_type"] == "modified"
    assert "signature" in symbols["OrderService.find"]["flags"] and "+db-write" in symbols["OrderService.find"]["flags"]
    assert symbols["OrderService.cancel"]["change_type"] == "added"
    # symbol-level propagation: the API module calls service.find, so it is impacted with high confidence
    impacted = {r["path"]: r for r in result["impacted"]}
    assert impacted["app/api/orders.py"]["confidence"] == "high" and "find" in impacted["app/api/orders.py"]["symbols"]
    # entry points: GET /orders/{order_id} is reached transitively
    endpoints = [e for e in result["entry_points"] if e["kind"] == "endpoint"]
    assert any("/orders" in e["name"] and e["reach"] in ("transitive", "same file", "direct") for e in endpoints), result["entry_points"]
    # config at key level
    keys = {(k["key"], k["environment"]): k for k in result["config"]["keys"]}
    assert keys[("orders.timeout-ms", "default")]["change"] == "changed"
    assert ("server.port", "default") not in keys  # reordering is not a change
    rules = {f["rule"] for f in result["config"]["findings"]}
    assert {"numeric-factor", "feature-flag", "endpoint-changed", "security-loosened", "secret-committed", "unread-key"} <= rules
    assert any(f["rule"] == "security-loosened" and f["environment"] == "prod" and f["severity"] == "critical" for f in result["config"]["findings"])
    secret = next(f for f in result["config"]["findings"] if f["rule"] == "secret-committed")
    assert "9f8e7d6c" not in secret["message"]
    # tests: tests/test_orders.py imports OrderService
    assert any(t["path"] == "tests/test_orders.py" for t in result["tests"]["tests"])
    assert any(g["symbol"] == "OrderService.cancel" for g in result["tests"]["gaps"] + result["tests"]["indirect"])
    # risk is explainable and floored by the committed secret
    assert result["risk"]["label"] in ("HIGH", "CRITICAL") and result["risk"]["overrides"]
    assert {f["factor"] for f in result["risk"]["factors"]} >= {"blast_radius", "configuration", "test_gaps", "entry_points"}
    assert abs(sum(f["contribution"] for f in result["risk"]["factors"]) - min(1, result["risk"]["score"])) < 0.6
    # reviewers from CODEOWNERS and history
    names = {p["name"] for p in result["reviewers"]}
    assert "@orders-team" in names and "@platform" in names
    # deterministic narrative without an LLM
    assert run["narrative"]["source"] == "rules" and run["narrative"]["review_checklist"]
    # base / head analyses are kept out of branch history
    repo = client.get(f"/api/repositories/{repo_id}", headers=admin).json()
    assert all(a["analysis_id"] not in (run["base_analysis_id"], run["head_analysis_id"]) for a in repo["analyses"])
    graph = client.get(f"/api/impact-runs/{run['run_id']}/graph", headers=admin).json()
    assert any(n["impact"] == "changed" for n in graph["nodes"]) and any(n["impact"] == "impacted" for n in graph["nodes"])
    hunks = client.get(f"/api/impact-runs/{run['run_id']}/diff", params={"path": "app/services/order_service.py"}, headers=admin).json()
    assert hunks["hunks"] and hunks["additions"] > 0
    assert client.get(f"/api/impact-runs?repo_id={repo_id}", headers=admin).json()[0]["risk_label"] == result["risk"]["label"]

    # a second run reuses both analyses (same commits)
    again = client.post(f"/api/impact-runs/{run['run_id']}/rerun", headers=admin).json()
    second = client.get(f"/api/impact-runs/{again['run_id']}", headers=admin).json()
    assert second["base_analysis_id"] == run["base_analysis_id"] and second["head_analysis_id"] == run["head_analysis_id"]
    events = client.get(f"/api/impact-runs/{again['run_id']}/events", headers=admin).json()
    assert any("Reusing the completed analysis" in e["message"] for e in events)

    # viewers read, but cannot start runs
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.get(f"/api/impact-runs/{run['run_id']}", headers=viewer).status_code == 200
    assert client.post(f"/api/repositories/{repo_id}/impact-runs", json={"base_ref": "main", "head_ref": "feature/cap-orders"}, headers=viewer).status_code == 403

    # deleting runs removes their PR analyses once nothing uses them
    assert client.delete(f"/api/impact-runs/{again['run_id']}", headers=admin).status_code == 204
    assert client.get(f"/api/analyses/{run['head_analysis_id']}", headers=admin).status_code == 200
    assert client.delete(f"/api/impact-runs/{run['run_id']}", headers=admin).status_code == 204
    assert client.get(f"/api/analyses/{run['head_analysis_id']}", headers=admin).status_code == 404


def test_feature_catalogue_and_matching(client, admin, git_repo):
    repo_id = client.post("/api/repositories", json={"source_type": "local", "path": str(git_repo), "name": "orders-git"}, headers=admin).json()["repo_id"]
    suggested = client.post(f"/api/repositories/{repo_id}/features/suggest", json={}, headers=admin)
    assert suggested.status_code == 200, suggested.text
    data = suggested.json()
    assert data["features"] and all(not f["confirmed"] for f in data["features"])
    orders = next(f for f in data["features"] if f["name"] == "Orders")
    assert orders["entry_count"] >= 1
    confirmed = client.patch(f"/api/features/{orders['feature_id']}", json={"confirmed": True, "name": "Order lookup"}, headers=admin).json()
    assert confirmed["confirmed"] and confirmed["name"] == "Order lookup"
    custom = client.post(f"/api/repositories/{repo_id}/features", json={"name": "Web app", "patterns": [{"type": "path", "value": "web/**"}]}, headers=admin)
    assert custom.status_code == 200
    bad = client.post(f"/api/repositories/{repo_id}/features", json={"name": "x", "patterns": [{"type": "nope", "value": "a"}]}, headers=admin)
    assert bad.status_code == 400
    run = client.post(f"/api/repositories/{repo_id}/impact-runs", json={"base_ref": "main", "head_ref": "feature/cap-orders"}, headers=admin).json()
    result = client.get(f"/api/impact-runs/{run['run_id']}", headers=admin).json()["result"]
    assert any(orders["feature_id"] in e.get("feature_ids", []) for e in result["entry_points"])


def test_logic_explained_by_llm_and_cross_checked(client, admin, git_repo, monkeypatch):
    from tests.conftest import FakeAdapter

    class LogicAdapter(FakeAdapter):
        def complete(self, system, user, temperature):
            if "pull-request impact report" in system and "CODE AFTER" in user:
                return ('{"summary": "Closed orders can now be returned and every lookup saves the order.", '
                        '"rules_changed": [{"before": "only open orders", "after": "open, or closed when include_closed", "line": 10}], '
                        '"side_effects": [{"kind": "db-write", "detail": "saves the order", "line": 11}, {"kind": "http-call", "detail": "invented", "line": 999}], '
                        '"risk_notes": ["find now writes"], "test_focus": ["include_closed=True"]}')
            if "pull-request impact report" in system:
                return '{"summary": "Orders lookup changed.", "risk_explanation": "Secret committed.", "features": [], "description_drift": [], "review_checklist": ["Remove the API key"]}'
            return super().complete(system, user, temperature)

    monkeypatch.setattr("app.services.llm_settings.active_adapter", lambda: LogicAdapter())
    repo_id = client.post("/api/repositories", json={"source_type": "local", "path": str(git_repo), "name": "orders-git"}, headers=admin).json()["repo_id"]
    run = client.post(f"/api/repositories/{repo_id}/impact-runs", json={"base_ref": "main", "head_ref": "feature/cap-orders"}, headers=admin).json()
    data = client.get(f"/api/impact-runs/{run['run_id']}", headers=admin).json()
    assert data["status"] == "completed", data["error"]
    find = next(s for s in data["symbols"] if s["qualified_name"] == "OrderService.find")
    assert find["logic_source"] == "llm" and "Closed orders" in find["logic"]["summary"]
    effects = {e["kind"]: e for e in find["logic"]["side_effects"]}
    assert effects["db-write"]["verified"] == "yes" and effects["http-call"]["verified"] == "unverified" and effects["http-call"]["line"] == 0
    assert data["narrative"]["source"] == "llm" and data["narrative"]["review_checklist"] == ["Remove the API key"]
    # the next run reuses the explanation by content hash
    again = client.post(f"/api/impact-runs/{run['run_id']}/rerun", headers=admin).json()
    second = client.get(f"/api/impact-runs/{again['run_id']}", headers=admin).json()
    assert next(s for s in second["symbols"] if s["qualified_name"] == "OrderService.find")["logic_source"] == "cache"


def test_remote_workspace_fetches_shallow_and_deepens_to_the_merge_base(tmp_path, git_repo):
    from app.services import git_hosts

    # 60 commits on main after the branch point, so depth 50 cannot see the merge-base at first
    git(git_repo, "checkout", "-q", "main")
    for index in range(60):
        write(git_repo, "history.txt", f"{index}\n")
        git(git_repo, "add", "-A")
        git(git_repo, "commit", "-q", "-m", f"main {index}")
    ref = git_hosts.parse_url("https://git.example.com/team/orders.git")
    logs: list[str] = []
    workspace = diff.remote_workspace(tmp_path / "ws", ref, None, [(str(git_repo), "refs/heads/main", "refs/ca/base"),
                                                                     (str(git_repo), "refs/heads/feature/cap-orders", "refs/ca/head")], logs.append)
    base, head = workspace.resolve("refs/ca/base"), workspace.resolve("refs/ca/head")
    merge_base = workspace.merge_base(base, head)
    assert merge_base == git(git_repo, "merge-base", "main", "feature/cap-orders").strip()
    assert any("deepening" in m for m in logs)
    files = {f.path for f in workspace.diff(merge_base, head)}
    assert "app/services/order_service.py" in files and "history.txt" not in files
    snapshot = workspace.export(head, tmp_path / "head")
    assert (snapshot / "app/services/notify.py").is_file() and not (snapshot / ".git").exists()


def test_pull_request_payloads_for_each_host():
    from app.services import git_hosts

    gh = git_hosts.parse_url("https://github.com/acme/shop")
    item = git_hosts._pr_payload(gh, {"number": 7, "title": "Cap", "user": {"login": "ann"}, "body": "desc", "draft": False, "state": "open",
                                      "head": {"ref": "feat", "sha": "a" * 40, "repo": {"clone_url": "https://github.com/fork/shop.git"}},
                                      "base": {"ref": "main", "sha": "b" * 40}, "html_url": "https://github.com/acme/shop/pull/7"})
    assert item["head_fetch"] == {"url": "https://github.com/acme/shop.git", "ref": "refs/pull/7/head"} and item["target_branch"] == "main"
    bb = git_hosts.parse_url("https://bitbucket.org/team/shop")
    item = git_hosts._pr_payload(bb, {"id": 3, "title": "T", "author": {"display_name": "Bo"}, "state": "OPEN",
                                      "source": {"branch": {"name": "feat"}, "commit": {"hash": "abc123def456"}, "repository": {"full_name": "bo/shop-fork"}},
                                      "destination": {"branch": {"name": "main"}, "commit": {"hash": "fff"}}, "links": {"html": {"href": "u"}}})
    assert item["head_fetch"] == {"url": "https://bitbucket.org/bo/shop-fork.git", "ref": "refs/heads/feat"} and item["state"] == "open"
    bbs = git_hosts.parse_url("https://git.corp.example/projects/PAY/repos/core/browse")
    item = git_hosts._pr_payload(bbs, {"id": 12, "title": "S", "author": {"user": {"displayName": "Cy"}}, "state": "OPEN",
                                       "fromRef": {"displayId": "feature/x", "latestCommit": "c" * 40}, "toRef": {"displayId": "develop", "latestCommit": "d" * 40},
                                       "links": {"self": [{"href": "https://git.corp.example/pr/12"}]}})
    assert item["head_fetch"]["ref"] == "refs/pull-requests/12/from" and item["source_branch"] == "feature/x"


def test_pull_request_permission_errors_name_the_missing_scope():
    import httpx
    from app.errors import AppError
    from app.services import git_hosts

    ref = git_hosts.parse_url("https://bitbucket.org/team/inventory")
    refused = httpx.Response(403, json={"type": "error", "error": {"message": "Your credentials lack one or more required privilege scopes."}})
    with pytest.raises(AppError) as caught:
        git_hosts._raise_pr(refused, ref, git_hosts.Credential(token="t"))
    assert caught.value.extra["status"] == "insufficient_scope"
    assert "Pull requests: Read" in caught.value.detail and "lack one or more required privilege scopes" in caught.value.detail
    with pytest.raises(AppError) as caught:
        git_hosts._raise_pr(httpx.Response(404, json={}), ref, None)
    assert caught.value.extra["status"] == "auth_required"
