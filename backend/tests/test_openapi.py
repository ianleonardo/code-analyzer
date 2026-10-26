"""OpenAPI generation from the layer catalogue: spec content, options, downloads, drift, breaking-change diff, external
APIs drafted from a landscape, and LLM enrichment that never changes the structure."""

import io
import json
import zipfile

import pytest
import yaml

from tests.conftest import login
from tests.landscape_fixture import split_polyglot, write_repo

ITEMS = {
    "pom.xml": """
        <project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId><artifactId>catalogue</artifactId><version>2.3.1</version>
        <dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency></dependencies></project>
    """,
    "src/main/resources/application.yml": """
        server:
          port: 8081
    """,
    "src/main/java/com/acme/cat/ItemController.java": """
        package com.acme.cat;

        import org.springframework.web.bind.annotation.*;
        import java.util.List;

        @RestController
        @RequestMapping("/api/items")
        public class ItemController {
            @GetMapping("/{id}")
            public Item get(@PathVariable Long id) { return null; }

            @PostMapping
            public Item create(@RequestBody Item item) { return item; }

            @GetMapping
            public List<Item> search(@RequestParam(required = false) String q, @RequestParam(defaultValue = "20") int size) { return null; }
        }
    """,
    "src/main/java/com/acme/cat/Item.java": """
        package com.acme.cat;

        public class Item {
            private Long id;
            private String name;
            private Status status;
        }
    """,
    "src/main/java/com/acme/cat/Status.java": """
        package com.acme.cat;

        public enum Status { ACTIVE, RETIRED }
    """,
    "api/openapi.yaml": """
        openapi: 3.0.3
        info: {title: Catalogue, version: 2.3.0}
        paths:
          /api/items/{id}:
            get:
              operationId: getItem
              parameters:
                - {name: id, in: path, required: true, schema: {type: integer, format: int64}}
              responses:
                '200':
                  description: OK
                  content:
                    application/json:
                      schema: {$ref: '#/components/schemas/Item'}
            delete:
              operationId: deleteItem
              parameters:
                - {name: id, in: path, required: true, schema: {type: integer, format: int64}}
              responses:
                '204': {description: Deleted}
        components:
          schemas:
            Item:
              type: object
              required: [id]
              properties:
                id: {type: integer, format: int64}
                name: {type: string}
                price: {type: number}
    """,
}


def _add(client, headers, path, name):
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(path), "name": name}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["repo_id"], body["started_analysis"]["analysis_id"]


@pytest.fixture
def shop(client, admin, tmp_path):
    return {name: _add(client, admin, path, name) for name, path in split_polyglot(tmp_path).items()}


def test_generate_preview_and_download(client, admin, shop):
    _, aid = shop["shop-backend"]
    info = client.get(f"/api/analyses/{aid}/openapi", headers=admin).json()
    assert info["applicable"] and info["services"][0]["endpoints"] == 2  # REST only; the Thymeleaf pages are left out
    result = client.get(f"/api/analyses/{aid}/openapi/spec", headers=admin).json()
    assert result["valid"] and result["completeness"] >= 80
    spec = yaml.safe_load(result["text"])
    assert spec["openapi"] == "3.0.3" and spec["servers"][0]["url"] == "/shop"
    post = spec["paths"]["/api/orders"]["post"]
    assert post["operationId"] == "create" and post["summary"] == "Create orders"
    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/CreateOrderRequest"}
    assert set(post["responses"]) == {"201", "400", "422"} and "OrderLimitException" in post["responses"]["422"]["description"]
    assert post["x-roles"] == ["CLERK"] and post["security"] == [{"basicAuth": []}]
    assert post["x-source"].endswith("OrderController.java:20") and "Business rules" in post["description"]
    request = spec["components"]["schemas"]["CreateOrderRequest"]
    assert request["required"] == ["customerCode", "quantity"]
    assert request["properties"]["quantity"] == {"type": "integer", "format": "int32", "minimum": 1, "maximum": 1000}
    assert request["properties"]["contactEmail"]["format"] == "email" and request["properties"]["customerCode"]["minLength"] == 1
    get = spec["paths"]["/api/orders/{orderId}"]["get"]
    assert get["parameters"][0] == {"name": "orderId", "in": "path", "required": True, "schema": {"type": "integer", "format": "int64"}}
    assert spec["components"]["securitySchemes"] == {"basicAuth": {"type": "http", "scheme": "basic"}}

    # options: JSON, 3.1, no extensions, tags by path, selected operations
    opt = client.get(f"/api/analyses/{aid}/openapi/spec", params={"format": "json", "version": "3.1.0", "extensions": "false", "tag_by": "path",
                                                                  "keys": "api:POST /api/orders"}, headers=admin).json()
    spec31 = json.loads(opt["text"])
    assert spec31["openapi"] == "3.1.0" and list(spec31["paths"]) == ["/api/orders"]
    assert "x-source" not in json.dumps(spec31) and spec31["paths"]["/api/orders"]["post"]["tags"] == ["orders"]
    assert client.get(f"/api/analyses/{aid}/openapi/spec", params={"version": "2.0"}, headers=admin).status_code == 400

    download = client.get(f"/api/analyses/{aid}/openapi/download", headers=admin)
    assert download.status_code == 200 and download.headers["content-type"].startswith("application/yaml")
    assert 'filename="shop-openapi.yaml"' in download.headers["content-disposition"]
    bundle = client.get(f"/api/analyses/{aid}/openapi/download", params={"service": "*", "format": "json"}, headers=admin)
    names = zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()
    assert names == ["shop-openapi.json"]

    viewer = login(client, "viewer", "ViewerPass123!")
    assert client.get(f"/api/analyses/{aid}/openapi/spec", headers=viewer).status_code == 200
    _, web = shop["shop-web"]
    assert client.get(f"/api/analyses/{web}/openapi", headers=admin).json()["applicable"] is False
    assert client.get(f"/api/analyses/{web}/openapi/spec", headers=admin).status_code == 409


def test_drift_against_committed_spec(client, admin, tmp_path):
    _, aid = _add(client, admin, write_repo(tmp_path / "catalogue", ITEMS), "catalogue")
    info = client.get(f"/api/analyses/{aid}/openapi", headers=admin).json()
    assert info["spec_files"] == ["api/openapi.yaml"] and info["services"][0]["spec_only"] == 1
    generated = yaml.safe_load(client.get(f"/api/analyses/{aid}/openapi/spec", headers=admin).json()["text"])
    assert generated["info"]["version"] == "2.3.1" and generated["servers"][0]["url"] == "http://localhost:8081"
    assert generated["components"]["schemas"]["Status"] == {"type": "string", "enum": ["ACTIVE", "RETIRED"]}
    search = generated["paths"]["/api/items"]["get"]
    assert search["responses"]["200"]["content"]["application/json"]["schema"] == {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}
    size = next(p for p in search["parameters"] if p["name"] == "size")
    assert size["schema"]["default"] == 20 and not size["required"]

    report = client.get(f"/api/analyses/{aid}/openapi/drift", headers=admin).json()
    by = {(c["operation"], c["change"]) for c in report["changes"]}
    assert ("DELETE /api/items/{id}", "in the spec but not implemented in the code") in by
    assert ("POST /api/items", "implemented in the code but not in the spec") in by
    assert any(c["operation"] == "GET /api/items/{id}" and c["where"] == "response 200.price" for c in report["changes"])


def test_breaking_changes_between_analyses(client, admin, tmp_path):
    _, before = _add(client, admin, write_repo(tmp_path / "v1", ITEMS), "catalogue-v1")
    changed = dict(ITEMS)
    changed["src/main/java/com/acme/cat/Item.java"] = """
        package com.acme.cat;

        import jakarta.validation.constraints.NotBlank;

        public class Item {
            private Long id;
            @NotBlank private String sku;
            private Status status;
        }
    """
    changed["src/main/java/com/acme/cat/ItemController.java"] = ITEMS["src/main/java/com/acme/cat/ItemController.java"].replace(
        "            @GetMapping\n            public List<Item> search(@RequestParam(required = false) String q, @RequestParam(defaultValue = \"20\") int size) { return null; }\n", "")
    _, after = _add(client, admin, write_repo(tmp_path / "v2", changed), "catalogue-v2")
    result = client.get(f"/api/analyses/{before}/openapi/diff/{after}", headers=admin).json()
    assert result["available"] and result["breaking"] >= 3
    changes = {(c["operation"], c["where"], c["change"]) for c in result["changes"]}
    assert ("GET /api/items", "", "operation removed") in changes
    assert ("POST /api/items", "request body.sku", "required field added") in changes
    assert ("GET /api/items/{id}", "response 200.name", "field removed (clients reading it break)") in changes
    comparison = client.post("/api/comparisons", json={"left_analysis_id": before, "right_analysis_id": after}, headers=admin).json()
    via_compare = client.get(f"/api/comparisons/{comparison['comparison_id']}/openapi-diff", headers=admin).json()
    assert via_compare["breaking"] == result["breaking"]


def test_external_api_as_used_and_llm_enrichment(client, admin, shop, monkeypatch):
    ids = [shop[n][0] for n in ("shop-backend", "audit-service")]
    lid = client.post("/api/landscapes", json={"name": "Audit", "repo_ids": ids}, headers=admin).json()["landscape_id"]
    ext = client.get(f"/api/landscapes/{lid}/external-openapi", params={"name": "api.partner.com"}, headers=admin).json()
    spec = yaml.safe_load(ext["text"])
    assert ext["valid"] and spec["servers"] == [{"url": "https://api.partner.com"}] and "/v1/notify" in spec["paths"]
    assert spec["paths"]["/v1/notify"]["post"]["x-consumers"][0].startswith("audit-service")
    dl = client.get(f"/api/landscapes/{lid}/external-openapi/download", params={"name": "api.partner.com"}, headers=admin)
    assert 'filename="api.partner.com-as-used-openapi.yaml"' in dl.headers["content-disposition"]

    from app.services import llm

    monkeypatch.setattr(llm, "configured", lambda: True)
    calls = []

    def fake(system, prompt, temperature=0.1):
        calls.append(prompt)
        ops = json.loads(prompt.split("OPERATIONS:\n", 1)[1].split("\n\nFor each")[0])
        out = []
        for op in ops:
            entry = {"id": op["id"], "summary": f"Do {op['method']}", "description": "Explained."}
            if op["request"]:
                entry["request_example"] = {"customerCode": "C-1", "quantity": 2}
            if op["response"]:
                entry["response_example"] = {"id": 7, "status": "NEW", "invented": True}
            out.append(entry)
        return {"operations": out}

    monkeypatch.setattr(llm, "complete_json", fake)
    _, aid = shop["shop-backend"]
    enriched = client.get(f"/api/analyses/{aid}/openapi/spec", params={"enrich": "true"}, headers=admin).json()
    spec = yaml.safe_load(enriched["text"])
    post = spec["paths"]["/api/orders"]["post"]
    assert post["requestBody"]["content"]["application/json"]["example"] == {"customerCode": "C-1", "quantity": 2}
    assert "example" not in post["responses"]["201"]["content"]["application/json"]  # invented field -> example dropped
    assert post["description"].startswith("Explained.") and post["x-enriched-by"] == "llm"
    assert spec["components"] == yaml.safe_load(client.get(f"/api/analyses/{aid}/openapi/spec", headers=admin).json()["text"])["components"]
    client.get(f"/api/analyses/{aid}/openapi/spec", params={"enrich": "true"}, headers=admin)
    assert len(calls) == 1  # cached by content hash
