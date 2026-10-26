import io
import zipfile

from tests.conftest import login


def _analyse(client, headers, repo_path, name="orders"):
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(repo_path), "name": name}, headers=headers)
    assert response.status_code == 200, response.text
    analysis_id = response.json()["started_analysis"]["analysis_id"]
    analysis = client.get(f"/api/analyses/{analysis_id}", headers=headers).json()
    assert analysis["status"] == "completed", analysis["error"]
    return analysis


def test_full_pipeline_on_sample_repo(client, admin, sample_repo):
    analysis = _analyse(client, admin, sample_repo)
    stats, macro = analysis["stats"], analysis["macro"]
    paths = {f["path"] for f in client.get(f"/api/analyses/{analysis['analysis_id']}/files?limit=500&only_source=false", headers=admin).json()["items"]}
    # hygiene gates
    assert not any(p.startswith(("node_modules/", "dist/")) for p in paths)
    assert len(analysis["merkle_root"]) == 64
    # macro
    names = {fw["name"] for fw in macro["frameworks"]}
    assert {"FastAPI", "SQLAlchemy", "React", "Axios"} <= names
    assert "app/main.py" in macro["entry_points"]
    assert any(d["claim"] == "Kafka" for d in macro["drift"])  # README claims Kafka; manifests do not
    # graph
    # util/a <-> util/b, and api -> service -> data -> api through the layer-violating import
    assert stats["cycles"] == 2
    assert ["app/util/a.py", "app/util/b.py"] in [sorted(c) for c in stats["sccs"]]
    assert ["app/api/orders.py", "app/data/order_repository.py", "app/services/order_service.py"] in [sorted(c) for c in stats["sccs"]]
    assert stats["edge_kinds"].get("IMPORTS") and stats["edge_kinds"].get("INJECTS")
    # classification
    files = {f["path"]: f for f in client.get(f"/api/analyses/{analysis['analysis_id']}/files?limit=500", headers=admin).json()["items"]}
    assert files["app/api/orders.py"]["layer"] == "api"
    assert files["app/services/order_service.py"]["layer"] == "service"
    assert files["app/data/order_repository.py"]["layer"] == "data"
    assert files["app/data/order_repository.py"]["role"] == "Repository / DAO"
    assert {"db-read", "db-write"} <= set(files["app/data/order_repository.py"]["concepts"])
    assert files["tests/test_orders.py"]["is_test"]
    # quality
    concerns = client.get(f"/api/analyses/{analysis['analysis_id']}/concerns", headers=admin).json()
    categories = {c["category"] for c in concerns}
    assert {"Tarjan SCC Cycle", "Layer Violation", "Hardcoded Secret", "Documentation Drift"} <= categories
    assert 0 <= analysis["health_score"] < 100
    markdown = client.get(f"/api/analyses/{analysis['analysis_id']}/concerns.md", headers=admin).text
    assert markdown.startswith("# CONCERNS.md") and "s3cr3tP4ssw0rd" not in markdown
    # C4
    assert analysis["c4"]["context"].startswith("C4Context") and analysis["c4"]["container"].startswith("C4Container")
    # offline content and inspector
    detail = client.get(f"/api/analyses/{analysis['analysis_id']}/file", params={"path": "app/services/order_service.py"}, headers=admin).json()
    assert "class OrderService" in detail["content"]
    assert any(s["qualified_name"] == "OrderService.find" for s in detail["symbols"])
    assert any(e["path"] == "app/api/orders.py" for e in detail["incoming"])
    assert detail["blast_radius"]["impacted_count"] >= 1
    tree = client.get(f"/api/analyses/{analysis['analysis_id']}/tree", headers=admin).json()
    assert {c["name"] for c in tree["children"]} >= {"app", "web"}
    graph = client.get(f"/api/analyses/{analysis['analysis_id']}/graph?limit=50", headers=admin).json()
    assert graph["nodes"] and graph["edges"]
    found = client.get(f"/api/analyses/{analysis['analysis_id']}/search?q=OrderService", headers=admin).json()
    assert found["symbols"]
    events = client.get(f"/api/analyses/{analysis['analysis_id']}/events", headers=admin).json()
    assert any("Merkle root" in e["message"] for e in events)


def test_zip_upload_with_zip_slip_guard(client, admin, sample_repo):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for path in sample_repo.rglob("*"):
            if path.is_file():
                bundle.write(path, f"orders-main/{path.relative_to(sample_repo)}")
    response = client.post("/api/repositories/upload", files={"file": ("orders.zip", buffer.getvalue(), "application/zip")}, data={"name": "orders-zip"}, headers=admin)
    assert response.status_code == 200, response.text
    analysis_id = response.json()["started_analysis"]["analysis_id"]
    assert client.get(f"/api/analyses/{analysis_id}", headers=admin).json()["status"] == "completed"

    evil = io.BytesIO()
    with zipfile.ZipFile(evil, "w") as bundle:
        bundle.writestr("../../escape.py", "print(1)")
    response = client.post("/api/repositories/upload", files={"file": ("evil.zip", evil.getvalue(), "application/zip")}, data={"name": "evil"}, headers=admin)
    run = client.get(f"/api/analyses/{response.json()['started_analysis']['analysis_id']}", headers=admin).json()
    assert run["status"] == "failed" and "unsafe path" in run["error"]


def test_rerun_and_delete(client, admin, sample_repo):
    first = _analyse(client, admin, sample_repo)
    rerun = client.post(f"/api/analyses/{first['analysis_id']}/rerun", headers=admin).json()
    second = client.get(f"/api/analyses/{rerun['analysis_id']}", headers=admin).json()
    assert second["merkle_root"] == first["merkle_root"]
    assert client.delete(f"/api/analyses/{first['analysis_id']}", headers=admin).status_code == 204
    assert client.get(f"/api/analyses/{first['analysis_id']}", headers=admin).status_code == 404


def test_local_paths_can_be_disabled(client, admin, sample_repo, monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_PATHS", "false")
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(sample_repo)}, headers=admin)
    assert response.status_code == 403


def test_viewer_can_read_results(client, admin, sample_repo):
    analysis = _analyse(client, admin, sample_repo)
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.get(f"/api/analyses/{analysis['analysis_id']}/files", headers=viewer).status_code == 200
    assert client.post(f"/api/analyses/{analysis['analysis_id']}/rerun", headers=viewer).status_code == 403


def test_browser_folder_upload_works_without_server_local_paths(client, admin, sample_repo, monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_PATHS", "false")
    multipart_files = []
    paths = []
    for path in sample_repo.rglob("*"):
        if path.is_file():
            rel = f"orders-folder/{path.relative_to(sample_repo).as_posix()}"
            multipart_files.append(("files", (rel, path.read_bytes(), "application/octet-stream")))
            paths.append(("paths", (None, rel)))
    response = client.post(
        "/api/repositories/upload-folder",
        files=multipart_files + paths,
        data={"name": ""},
        headers=admin,
    )
    assert response.status_code == 200, response.text
    repo = response.json()
    assert repo["source_type"] == "local"
    assert repo["name"] == "orders-folder"
    analysis_id = repo["started_analysis"]["analysis_id"]
    analysis = client.get(f"/api/analyses/{analysis_id}", headers=admin).json()
    assert analysis["status"] == "completed", analysis["error"]
    files = {f["path"] for f in client.get(f"/api/analyses/{analysis_id}/files?limit=500", headers=admin).json()["items"]}
    assert "app/services/order_service.py" in files


def test_check_existing_repository(client, admin, sample_repo):
    # Before adding
    res = client.post("/api/repositories/check-exists", json={"name": "orders"}, headers=admin).json()
    assert res["exists"] is False

    # Add repo
    analysis = _analyse(client, admin, sample_repo, name="orders")
    file_count = analysis["file_count"]

    # Check same
    res = client.post("/api/repositories/check-exists", json={"name": "orders", "file_count": file_count}, headers=admin).json()
    assert res["exists"] is True
    assert res["version_status"] == "same"

    # Check newer / larger
    res = client.post("/api/repositories/check-exists", json={"name": "orders", "file_count": file_count + 5}, headers=admin).json()
    assert res["exists"] is True
    assert res["version_status"] == "newer"

    # Check older / smaller
    res = client.post("/api/repositories/check-exists", json={"name": "orders", "file_count": max(1, file_count - 5)}, headers=admin).json()
    assert res["exists"] is True
    assert res["version_status"] == "older"

    # Check remote with commit SHA
    from app import db
    db.exec_sql("UPDATE analyses SET commit_sha = ? WHERE analysis_id = ?", ["3c57d3b91234", analysis["analysis_id"]])

    res = client.post(
        "/api/repositories/check-exists",
        json={"source_type": "remote", "name": "orders", "commit_sha": "3c57d3b91234", "branch": analysis["branch"]},
        headers=admin,
    ).json()
    assert res["exists"] is True
    assert res["version_status"] == "same"

    # Check remote with different commit SHA
    res = client.post(
        "/api/repositories/check-exists",
        json={"source_type": "remote", "name": "orders", "commit_sha": "different123456789", "branch": analysis["branch"]},
        headers=admin,
    ).json()
    assert res["exists"] is True
    assert res["version_status"] in ("different", "newer", "older")
