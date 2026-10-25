"""System landscape: cross-repository links (HTTP, messaging, shared database, external), review decisions, views."""

import pytest

from tests.conftest import login
from tests.landscape_fixture import split_polyglot


@pytest.fixture
def shop(client, admin, tmp_path):
    repos = {}
    for name, path in split_polyglot(tmp_path).items():
        response = client.post("/api/repositories", json={"source_type": "local", "path": str(path), "name": name}, headers=admin)
        assert response.status_code == 200, response.text
        repos[name] = response.json()["repo_id"]
    return repos


def _links(client, admin, lid, **params):
    return client.get(f"/api/landscapes/{lid}/links", params=params, headers=admin).json()["items"]


def _one(links, source_repo, kind, contains):
    found = [lk for lk in links if lk["source_repo_name"] == source_repo and lk["kind"] == kind and contains in (lk["source_name"] + " " + lk["label"])]
    assert found, (source_repo, kind, contains, [(lk["source_repo_name"], lk["source_name"]) for lk in links])
    return found


def test_links_across_repositories(client, admin, shop):
    members = [shop[n] for n in ("shop-backend", "shop-web", "shop-android", "shop-ios", "audit-service")]
    created = client.post("/api/landscapes", json={"name": "Shop", "description": "Ordering", "repo_ids": members}, headers=admin)
    assert created.status_code == 200, created.text
    land = created.json()
    assert land["auto_accept"] == "high_medium" and land["status"] == "ready" and len(land["repos"]) == 5
    lid = land["landscape_id"]
    links = _links(client, admin, lid)

    # screens / clients in web, Android and iOS reach the backend's endpoints (context path /shop resolved)
    web = _one(links, "shop-web", "http", "POST /api/orders")[0]
    assert web["target_repo_name"] == "shop-backend" and web["target_name"] == "POST /api/orders" and web["status"] == "auto" and web["confidence"] == "high"
    assert any("context path" in e for e in web["evidence"])
    android = _one(links, "shop-android", "http", "GET /api/orders/{orderId}")[0]
    assert android["target_name"] == "GET /api/orders/{orderId}" and android["status"] == "auto"
    assert any("BuildConfig.API_URL" in e for e in android["evidence"])
    ios = _one(links, "shop-ios", "http", "api.acme.com")[0]
    assert ios["target_repo_name"] == "shop-backend" and ios["status"] == "auto"
    # Feign client in the audit service: host + application name point to shop-backend
    feign = _one(links, "audit-service", "http", "shop-backend:8080")[0]
    assert feign["target_name"] == "GET /api/orders/{orderId}" and feign["confidence"] == "high"
    assert "/http:/" not in feign["source_name"]
    # Kafka: backend publishes order-events, the audit listener consumes it
    kafka = _one(links, "shop-backend", "messaging", "order-events")[0]
    assert kafka["target_repo_name"] == "audit-service" and kafka["target_name"] == "OrderAuditListener.onOrderEvent" and kafka["status"] == "auto"
    # shared ORDERS table on the same datasource
    table = [lk for lk in links if lk["kind"] == "database"]
    assert len(table) == 1 and table[0]["confidence"] == "high" and any("same datasource" in e for e in table[0]["evidence"])
    # external: partner API (call + detected host), Kafka / Oracle infrastructure; Room (device-local) never shared
    assert any(lk["external_name"] == "api.partner.com" and lk["kind"] == "http" and lk["status"] == "external" for lk in links)
    assert {"Kafka", "Oracle"} <= {lk["external_name"] for lk in links if lk["kind"] == "external"}
    assert not any("cached_orders" in (lk["source_name"] + lk["target_name"]).lower() for lk in links)
    # no duplicate of a client call through the screen that uses it
    assert not [lk for lk in links if lk["source_repo_name"] == "shop-web" and lk["source_name"].startswith("OrderPage →")]
    assert land["stats"]["review"] == 0 and land["stats"]["links"] >= 7

    # map, interfaces, journey, exports
    smap = client.get(f"/api/landscapes/{lid}/map", headers=admin).json()
    assert {n["label"] for n in smap["nodes"]} >= {"shop-backend", "shop-web", "audit-service", "api.partner.com"}
    assert any(e["kind"] == "http" and e["count"] >= 2 for e in smap["edges"]) and smap["mermaid"].startswith("flowchart LR")
    http_edge = next(e for e in smap["edges"] if e["kind"] == "http" and e["count"] >= 2)
    assert len(http_edge["links"]) >= 2
    assert any(l["label"] == "POST /api/orders" for e in smap["edges"] for l in e["links"])
    provided = client.get(f"/api/landscapes/{lid}/interfaces", headers=admin).json()["provided"]
    topic = next(p for p in provided if p["kind"] == "messaging")
    assert topic["repo_name"] == "shop-backend" and topic["name"] == "kafka order-events" and topic["consumers"][0]["repo_name"] == "audit-service"
    post = next(p for p in provided if p["name"] == "POST /api/orders")
    assert {c["repo_name"] for c in post["consumers"]} >= {"shop-web", "shop-android", "shop-ios"}
    starts = client.get(f"/api/landscapes/{lid}/entry-points", headers=admin).json()
    screen = next(s for s in starts if s["repo_name"] == "shop-android" and s["kind"] == "screen")
    trip = client.get(f"/api/landscapes/{lid}/journey", params={"repo_id": screen["repo_id"], "key": screen["item_key"]}, headers=admin).json()
    assert trip["participants"][:2] == ["shop-android", "shop-backend"]
    assert any(s["from"] == "shop-android" and s["to"] == "shop-backend" for s in trip["steps"])
    assert trip["mermaid"].startswith("sequenceDiagram")
    md = client.get(f"/api/landscapes/{lid}/report.md", headers=admin)
    assert md.status_code == 200 and md.text.startswith("# System Landscape — Shop") and "## Provided Interfaces" in md.text
    assert client.get(f"/api/landscapes/{lid}/report.pdf", headers=admin).content.startswith(b"%PDF-1.4")
    assert client.get(f"/api/landscapes/{lid}/export.xlsx", headers=admin).content[:2] == b"PK"

    # pull-request impact: consumers in other repositories of a backend endpoint / topic
    from app.services import landscape

    consumers = landscape.consumers_of(shop["shop-backend"], ["api:POST /api/orders"], [kafka["source_path"]])
    assert {c["consumer_repo"] for c in consumers if c["kind"] == "http"} >= {"shop-web", "shop-android", "shop-ios"}
    assert any(c["kind"] == "messaging" and c["consumer_repo"] == "audit-service" for c in consumers)

    # viewers read, but cannot change
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.get(f"/api/landscapes/{lid}", headers=viewer).status_code == 200
    assert client.post(f"/api/landscapes/{lid}/recompute", headers=viewer).status_code == 403
    assert client.post("/api/landscapes", json={"name": "x", "repo_ids": members}, headers=viewer).status_code == 403
    assert client.post("/api/landscapes", json={"name": "x", "repo_ids": members[:1]}, headers=admin).status_code == 400


def test_ambiguous_links_wait_for_review_and_decisions_stick(client, admin, shop, monkeypatch):
    members = [shop[n] for n in ("shop-backend", "shop-backend-v2", "shop-android", "audit-service")]
    lid = client.post("/api/landscapes", json={"name": "Shop v2", "repo_ids": members}, headers=admin).json()["landscape_id"]
    queue = client.get(f"/api/landscapes/{lid}/review", headers=admin).json()
    pending = {g["source_name"]: g for g in queue["pending"]}
    group = pending["GET /api/orders/{orderId}"]
    assert group["ambiguous"] and {c["target_repo_name"] for c in group["candidates"]} == {"shop-backend", "shop-backend-v2"}
    # the Feign call names its host (shop-backend), so it is not ambiguous
    assert not any("shop-backend:8080" in name for name in pending)

    # explanation: rules without an LLM, LLM text when configured (cached afterwards)
    rules = client.post(f"/api/landscapes/{lid}/explain", json={"group_key": group["group_key"]}, headers=admin).json()
    assert rules["source"] == "rules" and "match almost equally well" in rules["explanation"]
    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.post(f"/api/landscapes/{lid}/explain", json={"group_key": group["group_key"]}, headers=viewer).status_code == 200

    # choose shop-backend; the decision survives recomputation and re-analysis
    target = next(c for c in group["candidates"] if c["target_repo_name"] == "shop-backend")
    after = client.post(f"/api/landscapes/{lid}/decisions", json={"group_key": group["group_key"], "targets": [f"{target['target_repo_id']}|{target['target_key']}"]},
                        headers=admin).json()
    assert group["group_key"] in {g["group_key"] for g in after["decided"]}
    assert group["group_key"] not in {g["group_key"] for g in after["pending"]}
    rerun = client.post(f"/api/repositories/{shop['shop-android']}/analyses", json={}, headers=admin)
    assert rerun.status_code == 200, rerun.text
    links = [lk for lk in _links(client, admin, lid) if lk["group_key"] == group["group_key"]]
    assert {(lk["target_repo_name"], lk["status"]) for lk in links} == {("shop-backend", "confirmed"), ("shop-backend-v2", "rejected")}

    # bulk: accept the top suggestion for the rest; mark one as external; undo
    rest = [g["group_key"] for g in client.get(f"/api/landscapes/{lid}/review", headers=admin).json()["pending"]]
    assert rest
    client.post(f"/api/landscapes/{lid}/decisions", json={"group_keys": rest[:1], "action": "accept_top"}, headers=admin)
    client.post(f"/api/landscapes/{lid}/decisions", json={"group_key": group["group_key"], "external": True, "external_name": "Legacy gateway"}, headers=admin)
    links = [lk for lk in _links(client, admin, lid) if lk["group_key"] == group["group_key"]]
    assert any(lk["status"] == "external" and lk["external_name"] == "Legacy gateway" for lk in links)
    assert all(lk["status"] == "rejected" for lk in links if lk["target_repo_id"])
    client.post(f"/api/landscapes/{lid}/decisions", json={"group_key": group["group_key"], "action": "reset"}, headers=admin)
    assert group["group_key"] in {g["group_key"] for g in client.get(f"/api/landscapes/{lid}/review", headers=admin).json()["pending"]}

    # policy 'high' sends medium-confidence links to review as well
    lid2 = client.post("/api/landscapes", json={"name": "Strict", "auto_accept": "high", "repo_ids": [shop["shop-backend"], shop["shop-web"]]}, headers=admin).json()["landscape_id"]
    assert client.patch(f"/api/landscapes/{lid2}", json={"auto_accept": "sometimes"}, headers=admin).status_code == 400
    statuses = {lk["confidence"]: lk["status"] for lk in _links(client, admin, lid2, kind="http")}
    assert statuses.get("high") == "auto" and statuses.get("medium", "review") == "review"

    # LLM explanation
    from app.services import llm

    monkeypatch.setattr(llm, "configured", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: {"recommendation": "shop-backend", "explanation": "The Android base URL points at the shop host.", "check": "prod API_URL"})
    other = client.get(f"/api/landscapes/{lid}/review", headers=admin).json()["pending"][0]
    text = client.post(f"/api/landscapes/{lid}/explain", json={"group_key": other["group_key"]}, headers=admin).json()
    assert text["source"] == "llm" and "shop host" in text["explanation"]

    # deleting a member repository recomputes the landscape without it
    client.delete(f"/api/repositories/{shop['shop-backend-v2']}", headers=admin)
    land = client.get(f"/api/landscapes/{lid}", headers=admin).json()
    assert "shop-backend-v2" not in {r["name"] for r in land["repos"]}
    assert not any(lk["target_repo_name"] == "shop-backend-v2" for lk in _links(client, admin, lid))
    assert client.delete(f"/api/landscapes/{lid}", headers=admin).status_code == 204


def test_external_messaging_broker_capture(client, admin, tmp_path):
    from tests.landscape_fixture import write_repo

    camel_service = {
        "pom.xml": """
            <project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId><artifactId>camel-service</artifactId>
            <dependencies>
              <dependency><groupId>org.apache.camel.springboot</groupId><artifactId>camel-spring-boot-starter</artifactId></dependency>
              <dependency><groupId>org.apache.camel</groupId><artifactId>camel-activemq</artifactId></dependency>
              <dependency><groupId>org.apache.camel</groupId><artifactId>camel-kafka</artifactId></dependency>
            </dependencies></project>
        """,
        "src/main/java/com/acme/routes/OrderRoute.java": """
            package com.acme.routes;
            import org.apache.camel.builder.RouteBuilder;
            import org.springframework.stereotype.Component;

            @Component
            public class OrderRoute extends RouteBuilder {
                @Override
                public void configure() {
                    from("activemq:queue:incoming-orders")
                        .routeId("order-consumer")
                        .to("kafka:processed-orders");
                }
            }
        """,
    }
    dummy_service = {
        "pom.xml": """
            <project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId><artifactId>dummy-service</artifactId>
            <dependencies>
              <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
            </dependencies></project>
        """,
        "src/main/java/com/acme/DummyController.java": """
            package com.acme;
            import org.springframework.web.bind.annotation.*;
            @RestController
            public class DummyController {
                @GetMapping("/health")
                public String health() { return "OK"; }
            }
        """,
    }
    p1 = write_repo(tmp_path / "camel-service", camel_service)
    p2 = write_repo(tmp_path / "dummy-service", dummy_service)
    r1 = client.post("/api/repositories", json={"source_type": "local", "path": str(p1), "name": "camel-service"}, headers=admin).json()["repo_id"]
    r2 = client.post("/api/repositories", json={"source_type": "local", "path": str(p2), "name": "dummy-service"}, headers=admin).json()["repo_id"]

    created = client.post("/api/landscapes", json={"name": "Camel MQ Landscape", "repo_ids": [r1, r2]}, headers=admin)
    assert created.status_code == 200, created.text
    lid = created.json()["landscape_id"]

    links = _links(client, admin, lid)
    # External ActiveMQ broker should be captured with kind="messaging" and direction="in"
    amq = [lk for lk in links if lk["external_name"] == "ActiveMQ"]
    assert amq, [(lk["external_name"], lk["kind"], lk["label"]) for lk in links]
    assert any(lk["kind"] == "messaging" and "incoming-orders" in lk["label"] for lk in amq)

    # External Kafka broker should be captured with kind="messaging" and direction="out"
    kf = [lk for lk in links if lk["external_name"] == "Kafka"]
    assert kf, [(lk["external_name"], lk["kind"], lk["label"]) for lk in links]
    assert any(lk["kind"] == "messaging" and "processed-orders" in lk["label"] for lk in kf)

    # Verify system map has hexagon broker nodes {{ ActiveMQ }} and {{ Kafka }}
    smap = client.get(f"/api/landscapes/{lid}/map", headers=admin).json()
    assert any("{{ActiveMQ}}" in line or '{{ "ActiveMQ" }}' in line or 'ActiveMQ' in line and "{{" in line for line in smap["mermaid"].splitlines())
    assert any("{{Kafka}}" in line or '{{ "Kafka" }}' in line or 'Kafka' in line and "{{" in line for line in smap["mermaid"].splitlines())

    # Verify edges connecting to brokers have kind="messaging" and have links with interface details
    msg_edges = [e for e in smap["edges"] if e["kind"] == "messaging"]
    assert len(msg_edges) >= 2
    for edge in msg_edges:
        assert len(edge["links"]) >= 1
        assert edge["links"][0]["source_name"] or edge["links"][0]["target_name"]


def test_cross_service_base_url_and_prefix_matching():
    from app.services.landscape import Member, match_http
    caller = Member(repo={"repo_id": "caller-repo", "name": "cart-ios"}, analysis_id="a1", items={})
    caller.cores = {"cart", "ios"}

    provider = Member(
        repo={"repo_id": "provider-repo", "name": "cart-inventory"}, analysis_id="a2",
        items={
            "ep:products": {
                "item_key": "ep:products",
                "kind": "endpoint",
                "name": "GET /api/v1/products",
                "details": {"method": "GET", "path": "/api/v1/products"},
            },
            "ep:product_by_id": {
                "item_key": "ep:product_by_id",
                "kind": "endpoint",
                "name": "GET /api/v1/products/{id}",
                "details": {"method": "GET", "path": "/api/v1/products/{id}"},
            },
        },
    )
    provider.cores = {"cart", "inventory"}
    provider.ports = {"8081"}

    # 1. Exact path + port match + target clue
    call1 = {
        "method": "GET",
        "url": "/api/v1/products",
        "base": "http://localhost:8081",
        "target": "inventory",
        "client": "APIClient",
    }
    matches1 = match_http(call1, caller, [provider])
    assert len(matches1) == 1
    assert matches1[0]["provider"].name == "cart-inventory"
    assert matches1[0]["endpoint"]["name"] == "GET /api/v1/products"
    assert matches1[0]["score"] >= 0.8
    assert any("port 8081" in e for e in matches1[0]["evidence"])
    assert any("inventory" in e for e in matches1[0]["evidence"])

    # 2. Relative path (/products) matching backend with prefix (/api/v1/products)
    call2 = {
        "method": "GET",
        "url": "/products",
        "base": "http://localhost:8081",
        "target": "inventory",
        "client": "APIClient",
    }
    matches2 = match_http(call2, caller, [provider])
    assert len(matches2) == 1
    assert matches2[0]["endpoint"]["name"] == "GET /api/v1/products"
    assert any("under backend API prefix" in e for e in matches2[0]["evidence"])

    # 3. Path with prefix matching backend with relative root
    provider_root = Member(
        repo={"repo_id": "root-repo", "name": "cart-order"}, analysis_id="a3",
        items={
            "ep:carts": {
                "item_key": "ep:carts",
                "kind": "endpoint",
                "name": "POST /carts",
                "details": {"method": "POST", "path": "/carts"},
            },
        },
    )
    provider_root.cores = {"cart", "order"}
    provider_root.ports = {"8083"}

    call3 = {
        "method": "POST",
        "url": "/api/v1/carts",
        "base": "http://localhost:8083",
        "target": "order",
        "client": "APIClient",
    }
    matches3 = match_http(call3, caller, [provider_root])
    assert len(matches3) == 1
    assert matches3[0]["endpoint"]["name"] == "POST /carts"

