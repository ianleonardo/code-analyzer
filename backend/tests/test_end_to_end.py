"""End to end: Android / iOS client (CORE) -> Camel gateway with versioned routes (SPL) -> Spring microservices (DBE)."""

import pytest

from tests.landscape_fixture import write_repo
from tests.test_camel_egress import CUSTOMER_XML, POM, PROD_PROPERTIES, PROPERTIES, ROUTES_JAVA
from tests.test_mobile_http import ANDROID, IOS

DBE_POM = """
<project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId><artifactId>{name}</artifactId>
<dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>
"""

DBE_ACCOUNT = {
    "pom.xml": DBE_POM.format(name="dbe-account"),
    "src/main/resources/application.yml": """
        spring:
          application:
            name: dbe-account
        server:
          port: 8080
          servlet:
            context-path: /api
    """,
    "src/main/java/com/acme/dbe/AccountController.java": """
        package com.acme.dbe;

        import org.springframework.web.bind.annotation.*;

        @RestController
        @RequestMapping("/accounts")
        public class AccountController {
            @GetMapping("/{id}")
            public String get(@PathVariable String id) { return id; }

            @PostMapping
            public String create(@RequestBody String body) { return body; }
        }
    """,
}

DBE_CUSTOMER = {
    "pom.xml": DBE_POM.format(name="dbe-customer"),
    "src/main/resources/application.yml": """
        spring:
          application:
            name: dbe-customer
        server:
          port: 8080
    """,
    "src/main/java/com/acme/dbe/CustomerController.java": """
        package com.acme.dbe;

        import org.springframework.web.bind.annotation.*;

        @RestController
        @RequestMapping("/customers")
        public class CustomerController {
            @GetMapping("/{customerId}")
            public String get(@PathVariable String customerId) { return customerId; }

            @PostMapping("/validate")
            public String validate(@RequestBody String body) { return body; }

            @PostMapping("/legacy-validate")
            public String legacy(@RequestBody String body) { return body; }
        }
    """,
}


@pytest.fixture
def system(client, admin, tmp_path):
    spl = {
        "pom.xml": POM,
        "src/main/resources/application.properties": PROPERTIES.replace("dbe.customer.host=dbe-customer.internal", "dbe.customer.host=dbe-customer").replace("8443", "8080"),
        "src/main/resources/application-prod.properties": PROD_PROPERTIES,
        "src/main/java/com/acme/spl/AccountRoutes.java": ROUTES_JAVA,
        "src/main/resources/routes/customer-routes.xml": CUSTOMER_XML,
    }
    android = {f.path: f.text for f in ANDROID}
    android["core/src/main/java/com/acme/core/data/LegacyRepository.kt"] = """
        package com.acme.core.data

        import com.acme.core.net.ApiClient

        class LegacyRepository(private val api: ApiClient) {
            fun oldAccount(id: String) = api.get("/accounts/$id")
        }
    """
    repos = {
        "core-android": android,
        "core-ios": {f.path: f.text for f in IOS},
        "spl": spl,
        "dbe-account": DBE_ACCOUNT,
        "dbe-customer": DBE_CUSTOMER,
    }
    ids = {}
    for name, files in repos.items():
        path = write_repo(tmp_path / name, files)
        response = client.post("/api/repositories", json={"source_type": "local", "path": str(path), "name": name}, headers=admin)
        assert response.status_code == 200, response.text
        ids[name] = response.json()["repo_id"]
    return ids


def test_mobile_to_spl_to_dbe(client, admin, system):
    created = client.post("/api/landscapes", json={"name": "Core banking", "repo_ids": list(system.values())}, headers=admin)
    assert created.status_code == 200, created.text
    lid = created.json()["landscape_id"]
    links = client.get(f"/api/landscapes/{lid}/links", headers=admin).json()["items"]
    http = [lk for lk in links if lk["kind"] == "http"]
    summary = [(lk["source_repo_name"], lk["source_name"], lk["target_repo_name"], lk["target_name"], lk["status"], lk["confidence"]) for lk in http]


    def find(repo, contains):
        return [lk for lk in http if lk["source_repo_name"] == repo and contains in lk["source_name"] + " " + lk["label"]]

    # CORE -> SPL: the app's call (constant + helper + wrapper + BuildConfig base URL) reaches the v2 route, not v1
    android = find("core-android", "/v2/accounts/{id}")
    assert android and android[0]["target_repo_name"] == "spl" and android[0]["target_name"] == "GET /v2/accounts/{id}", summary
    ios = find("core-ios", "/v2/accounts/{id}")
    assert ios and ios[0]["target_repo_name"] == "spl" and ios[0]["target_name"] == "GET /v2/accounts/{id}", summary
    # SPL -> DBE: Camel placeholders, toD and headers resolved to the microservices' endpoints
    account = [lk for lk in http if lk["source_repo_name"] == "spl" and lk["target_repo_name"] == "dbe-account" and lk["target_name"].startswith("GET")]
    assert account and account[0]["target_name"] == "GET /accounts/{id}", summary
    customer = [lk for lk in http if lk["source_repo_name"] == "spl" and lk["target_repo_name"] == "dbe-customer"]
    assert {lk["target_name"] for lk in customer} >= {"POST /customers/validate", "GET /customers/{customerId}"}, summary


def test_call_without_version_is_ambiguous_between_v1_and_v2(client, admin, system):
    lid = client.post("/api/landscapes", json={"name": "Versions", "repo_ids": [system["core-android"], system["spl"]]}, headers=admin).json()["landscape_id"]
    links = [lk for lk in client.get(f"/api/landscapes/{lid}/links", headers=admin).json()["items"] if lk["kind"] == "http" and lk["source_name"] == "GET /accounts/{id}"]
    targets = {lk["target_name"]: lk for lk in links if lk["target_repo_name"] == "spl"}
    assert {"GET /v1/accounts/{id}", "GET /v2/accounts/{id}"} <= set(targets), links
    assert all(lk["ambiguous"] and lk["status"] in ("review", "candidate") for lk in targets.values())
    assert any("does not say which version" in e for e in targets["GET /v2/accounts/{id}"]["evidence"])
    # the versioned call from the same app is not confused with them
    exact = [lk for lk in client.get(f"/api/landscapes/{lid}/links", headers=admin).json()["items"] if lk["source_name"] == "GET /v2/accounts/{id}" and lk["target_repo_name"] == "spl"]
    assert exact and not exact[0]["ambiguous"] and exact[0]["status"] == "auto"
