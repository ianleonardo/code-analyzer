"""Paginated lists and date-range purges for impact runs and saved comparisons."""

from datetime import datetime, timedelta

from app import db

from tests.conftest import login

DAY0 = datetime(2026, 1, 1, 12, 0, 0)


def _repo(client, headers, path) -> dict:
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(path), "name": "orders"}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _add_runs(repo_id: str, count: int, status: str = "completed", start: int = 0) -> None:
    for i in range(start, start + count):
        db.insert(
            "pr_impact_runs",
            {"run_id": f"run-{i:03d}", "repo_id": repo_id, "mode": "branch", "title": f"run {i}", "status": status, "started_by": "u-admin", "created_at": DAY0 + timedelta(days=i)},
        )


def test_impact_runs_page_and_purge(client, admin, sample_repo):
    repo_id = _repo(client, admin, sample_repo)["repo_id"]
    _add_runs(repo_id, 60)
    _add_runs(repo_id, 1, status="running", start=60)

    first = client.get("/api/impact-runs/page", params={"repo_id": repo_id}, headers=admin).json()
    assert first["total"] == 61 and len(first["items"]) == 50 and first["page"] == 1
    assert first["items"][0]["run_id"] == "run-060"  # newest first
    second = client.get("/api/impact-runs/page", params={"repo_id": repo_id, "page": 2}, headers=admin).json()
    assert len(second["items"]) == 11 and second["items"][-1]["run_id"] == "run-000"

    # [day 10, day 20) -> runs 10..19
    window = {"date_from": (DAY0 + timedelta(days=10)).date().isoformat() + "T00:00:00Z", "date_to": (DAY0 + timedelta(days=20)).date().isoformat() + "T00:00:00Z"}
    filtered = client.get("/api/impact-runs/page", params={"repo_id": repo_id, **window}, headers=admin).json()
    assert filtered["total"] == 10

    assert client.post("/api/impact-runs/purge", json={"repo_id": repo_id}, headers=admin).status_code == 400  # a range is required
    bad = client.post("/api/impact-runs/purge", json={"date_from": window["date_to"], "date_to": window["date_from"]}, headers=admin)
    assert bad.status_code == 400
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.post("/api/impact-runs/purge", json=window, headers=viewer).status_code == 403

    preview = client.post("/api/impact-runs/purge", json={**window, "dry_run": True}, headers=admin).json()
    assert preview == {"matched": 10, "deleted": 0, "skipped_active": 0, "dry_run": True}
    assert client.get("/api/impact-runs/page", params={"repo_id": repo_id}, headers=admin).json()["total"] == 61
    done = client.post("/api/impact-runs/purge", json=window, headers=admin).json()
    assert done["deleted"] == 10
    assert client.get("/api/impact-runs/page", params={"repo_id": repo_id}, headers=admin).json()["total"] == 51

    # open-ended range from day 50: runs 50..59 deleted, the running one is kept
    tail = client.post("/api/impact-runs/purge", json={"date_from": "2026-02-20T00:00:00Z"}, headers=admin).json()
    assert tail["deleted"] == 10 and tail["skipped_active"] == 1
    remaining = client.get("/api/impact-runs/page", params={"repo_id": repo_id}, headers=admin).json()
    assert remaining["total"] == 41 and remaining["items"][0]["run_id"] == "run-060"


def test_comparisons_page_search_and_purge(client, admin, sample_repo):
    analysis_id = _repo(client, admin, sample_repo)["started_analysis"]["analysis_id"]
    for i in range(55):
        db.insert(
            "comparisons",
            {
                "comparison_id": f"cmp-{i:03d}",
                "left_analysis_id": analysis_id,
                "right_analysis_id": analysis_id,
                "title": f"{'billing' if i % 5 == 0 else 'orders'} main vs release-{i}",
                "result_json": "{}",
                "created_by": "u-admin",
                "created_at": DAY0 + timedelta(days=i),
            },
        )
    page = client.get("/api/comparisons/page", headers=admin).json()
    assert page["total"] == 55 and len(page["items"]) == 50 and page["items"][0]["comparison_id"] == "cmp-054"
    assert page["items"][0]["created_by_name"]
    assert len(client.get("/api/comparisons/page", params={"page": 2}, headers=admin).json()["items"]) == 5
    assert client.get("/api/comparisons/page", params={"q": "BILLING"}, headers=admin).json()["total"] == 11

    session = client.post("/api/chat/sessions", json={"scope_type": "comparison", "scope_id": "cmp-000"}, headers=admin).json()
    done = client.post("/api/comparisons/purge", json={"date_to": "2026-01-11T00:00:00Z"}, headers=admin).json()  # days 0..9
    assert done["deleted"] == 10
    assert client.get("/api/comparisons/page", headers=admin).json()["total"] == 45
    assert client.get(f"/api/chat/sessions/{session['session_id']}", headers=admin).status_code == 404
