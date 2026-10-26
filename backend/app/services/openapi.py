"""OpenAPI generation from the layer catalogue of a stored analysis.

Everything is derived from what the catalogue already holds (no clone, no re-run):

  info        repository / service name, branch, commit, version from the manifest, business purpose
  servers     context path and port per profile (server.port, server.servlet.context-path, …)
  paths       REST endpoints (Spring, Express, NestJS, FastAPI, Next.js API routes, OpenAPI contract-first);
              operationId from the handler, summary / description from Javadoc, OpenAPI annotations, LLM purpose
              and the guard rules; parameters with types and validation constraints
  components  request / response DTOs expanded into schemas (nested types, lists, maps, enums, required)
  responses   success code and body, error responses from exceptions mapped to a status
  security    schemes from Spring Security / guards / auth dependencies; roles per operation (x-roles)
  tags        by controller, by feature catalogue, or by first path segment

Whatever the code does not pin down (Object, Map, untyped JavaScript, ResponseEntity<?>) is marked x-inferred and
counted against the operation's completeness. The document is checked against the core OpenAPI rules before it is
returned. One spec per service (container) of a monorepo.

Also here: a structural diff of two specs (breaking / non-breaking changes), the drift between a committed spec and
the code, and an optional LLM pass that only adds summaries, descriptions and examples (never paths, types or schemas).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
from copy import deepcopy

import yaml

from app import db
from app.errors import AppError
from app.permissions import require

logger = logging.getLogger(__name__)

VERSIONS = ("3.0.3", "3.1.0")
HTTP_METHODS = ("GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE")
INTERNAL_PATH = re.compile(r"^/(actuator|error|health|healthz|readyz|livez|metrics|prometheus|swagger-ui|v3/api-docs|v2/api-docs|webjars|favicon\.ico)(/|$)", re.I)
REASONS = {200: "OK", 201: "Created", 202: "Accepted", 204: "No Content", 301: "Moved Permanently", 302: "Found", 304: "Not Modified", 400: "Bad Request",
           401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 410: "Gone", 412: "Precondition Failed",
           415: "Unsupported Media Type", 422: "Unprocessable Entity", 429: "Too Many Requests", 500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}

WRAPPERS = {"ResponseEntity", "Optional", "Mono", "CompletableFuture", "CompletionStage", "Future", "Promise", "Callable", "DeferredResult", "HttpEntity",
            "Observable", "Single", "Maybe", "Awaitable", "Coroutine", "Response", "EntityModel", "JsonResult", "ActionResult", "Task", "Deferred"}
COLLECTIONS = {"List", "Set", "Collection", "Iterable", "ArrayList", "LinkedList", "HashSet", "TreeSet", "SortedSet", "Flux", "Stream", "Array", "ReadonlyArray",
               "list", "set", "tuple", "Sequence", "MutableList", "MutableSet", "CollectionModel", "frozenset", "Iterator"}
MAPS = {"Map", "HashMap", "TreeMap", "LinkedHashMap", "ConcurrentHashMap", "MutableMap", "Record", "dict", "Dict", "Mapping", "MultiValueMap"}
PAGES = {"Page", "Slice", "PagedModel"}
SCALARS: dict[str, dict] = {
    "string": {"type": "string"}, "str": {"type": "string"}, "charsequence": {"type": "string"}, "char": {"type": "string", "maxLength": 1},
    "character": {"type": "string", "maxLength": 1}, "uuid": {"type": "string", "format": "uuid"},
    "int": {"type": "integer", "format": "int32"}, "integer": {"type": "integer", "format": "int32"}, "short": {"type": "integer", "format": "int32"},
    "byte": {"type": "integer", "format": "int32"}, "long": {"type": "integer", "format": "int64"}, "biginteger": {"type": "integer"},
    "double": {"type": "number", "format": "double"}, "float": {"type": "number", "format": "float"}, "bigdecimal": {"type": "number"},
    "number": {"type": "number"}, "decimal": {"type": "number"}, "boolean": {"type": "boolean"}, "bool": {"type": "boolean"},
    "localdate": {"type": "string", "format": "date"}, "date": {"type": "string", "format": "date"},
    "localdatetime": {"type": "string", "format": "date-time"}, "datetime": {"type": "string", "format": "date-time"}, "instant": {"type": "string", "format": "date-time"},
    "offsetdatetime": {"type": "string", "format": "date-time"}, "zoneddatetime": {"type": "string", "format": "date-time"}, "timestamp": {"type": "string", "format": "date-time"},
    "localtime": {"type": "string", "format": "time"}, "time": {"type": "string", "format": "time"}, "duration": {"type": "string", "format": "duration"},
    "uri": {"type": "string", "format": "uri"}, "url": {"type": "string", "format": "uri"}, "emailstr": {"type": "string", "format": "email"},
    "multipartfile": {"type": "string", "format": "binary"}, "resource": {"type": "string", "format": "binary"}, "inputstream": {"type": "string", "format": "binary"},
    "uploadfile": {"type": "string", "format": "binary"}, "file": {"type": "string", "format": "binary"}, "blob": {"type": "string", "format": "binary"},
    "bytes": {"type": "string", "format": "byte"}, "byte[]": {"type": "string", "format": "byte"}, "bytearray": {"type": "string", "format": "byte"},
    "object": {}, "any": {}, "unknown": {}, "jsonnode": {}, "objectnode": {}, "map": {"type": "object"},
}
NO_BODY = {"void", "unit", "none", "nothing", "never", "undefined", "null"}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


# --- type mapping ------------------------------------------------------------------------------------------------------


def _split_generic(text: str) -> tuple[str, list[str]]:
    """'Map<String, List<Order>>' -> ('Map', ['String', 'List<Order>'])."""
    text = text.strip()
    m = re.match(r"^([\w.$]+)\s*[<\[](.*)[>\]]$", text)
    if not m:
        return text, []
    args, depth, current = [], 0, ""
    for ch in m.group(2):
        if ch in "<[" and depth >= 0:
            depth += 1
        elif ch in ">]":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(current.strip())
    return m.group(1), args


class Schemas:
    """Collects component schemas; names are made unique when two different shapes share a simple name."""

    def __init__(self, version: str):
        self.version = version
        self.components: dict[str, dict] = {}
        self.inferred = 0
        self.fields = 0

    def nullable(self, schema: dict) -> dict:
        if "$ref" in schema:
            return {"allOf": [schema], "nullable": True} if self.version == "3.0.3" else {"anyOf": [schema, {"type": "null"}]}
        if self.version == "3.0.3":
            return {**schema, "nullable": True}
        if isinstance(schema.get("type"), str):
            return {**schema, "type": [schema["type"], "null"]}
        return schema

    def of_type(self, type_text: str, fields: list | None = None, enum: list | None = None, name_hint: str = "") -> dict:
        """JSON schema for a type label (Java / Kotlin / TypeScript / Python), with the catalogue's field list when known."""
        text = (type_text or "").strip()
        text = re.sub(r"^(final|readonly|const)\s+", "", text)
        nullable = text.endswith("?") or text.endswith("| null") or text.startswith("Optional[")
        text = re.sub(r"\?$|\s*\|\s*null$|\s*\|\s*undefined$", "", text).strip()
        if text.startswith("Optional[") and text.endswith("]"):
            text = text[9:-1]
        schema = self._of(text, fields, enum, name_hint)
        return self.nullable(schema) if nullable and schema else schema

    def _of(self, text: str, fields, enum, name_hint) -> dict:
        if text.endswith("[]") and text.lower() not in ("byte[]",):
            return {"type": "array", "items": self._of(text[:-2], fields, enum, name_hint)}
        base, args = _split_generic(text)
        simple = base.split(".")[-1]
        low = simple.lower()
        if base.lower() == "byte[]" or text.lower() == "byte[]":
            return {"type": "string", "format": "byte"}
        if simple in WRAPPERS and args:
            return self._of(args[0], fields, enum, name_hint)
        if simple in COLLECTIONS or low in ("list", "set"):
            return {"type": "array", "items": self._of(args[0], fields, enum, name_hint) if args else self._inferred({})}
        if simple in MAPS:
            value = args[1] if len(args) > 1 else (args[0] if args and simple == "Record" else "")
            return {"type": "object", "additionalProperties": self._of(value, None, None, "") if value else self._inferred({})}
        if simple in PAGES and args:
            return {"type": "object", "properties": {"content": {"type": "array", "items": self._of(args[0], fields, enum, name_hint)},
                                                     "totalElements": {"type": "integer", "format": "int64"}, "totalPages": {"type": "integer", "format": "int32"},
                                                     "number": {"type": "integer", "format": "int32"}, "size": {"type": "integer", "format": "int32"}},
                    "x-inferred": "Spring Data page shape"}
        if "|" in text and not args:
            parts = [p.strip().strip("'\"") for p in text.split("|")]
            if all(re.match(r"^[\w-]+$", p) for p in parts) and any(c.islower() for c in "".join(parts)) and not any(p.lower() in SCALARS for p in parts):
                return {"type": "string", "enum": parts}
        if low in SCALARS:
            schema = dict(SCALARS[low])
            if not schema or (low == "map"):
                return self._inferred(schema)
            return schema
        if low in NO_BODY or not simple:
            return {}
        if enum:
            return self.component(simple, {"type": "string", "enum": [str(e) for e in enum][:200]})
        if fields:
            return self.component(simple, self.object_of(fields))
        if simple[:1].isupper():
            # a named type the catalogue could not expand (library type, generated class, …)
            return self.component(simple, self._inferred({"type": "object", "description": f"{simple} (structure not found in the repository)"}))
        return self._inferred({})

    def _inferred(self, schema: dict) -> dict:
        self.inferred += 1
        return {**schema, "x-inferred": True}

    def object_of(self, fields: list[dict]) -> dict:
        props, required = {}, []
        for field in fields[:150]:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            self.fields += 1
            prop = self.of_type(str(field.get("type") or ""), field.get("fields"), field.get("enum"), field.get("name", ""))
            if not prop and field.get("type"):
                prop = self._inferred({})
            prop = apply_constraints(prop, field.get("validations") or [])
            if field.get("default") not in (None, ""):
                prop.setdefault("default", _literal(str(field["default"])))
            if field.get("description"):
                prop.setdefault("description", str(field["description"])[:500])
            props[str(field["name"])] = prop
            if field.get("required"):
                required.append(str(field["name"]))
        out: dict = {"type": "object", "properties": props}
        if required:
            out["required"] = required
        return out

    def component(self, name: str, schema: dict) -> dict:
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "Model"
        candidate, index = name, 2
        while candidate in self.components and self.components[candidate] != schema:
            if _fields_of(self.components[candidate]) and not _fields_of(schema):
                return {"$ref": f"#/components/schemas/{candidate}"}  # same type seen with less detail: reuse the richer one
            if not _fields_of(self.components[candidate]) and _fields_of(schema):
                self.components[candidate] = schema
                return {"$ref": f"#/components/schemas/{candidate}"}
            candidate = f"{name}{index}"
            index += 1
        self.components[candidate] = schema
        return {"$ref": f"#/components/schemas/{candidate}"}


def _fields_of(schema: dict) -> dict:
    return schema.get("properties") or {}


def _literal(text: str):
    t = text.strip().strip("\"'")
    if re.match(r"^-?\d+$", t):
        return int(t)
    if re.match(r"^-?\d+\.\d+$", t):
        return float(t)
    if t.lower() in ("true", "false"):
        return t.lower() == "true"
    return t


def apply_constraints(schema: dict, validations: list) -> dict:
    """Validation descriptions from the catalogue -> JSON-schema keywords; the rest stays in x-validations."""
    if not validations:
        return schema
    out = dict(schema)
    if "$ref" in out or "allOf" in out or "anyOf" in out:
        texts = [str(v) for v in validations if not str(v).startswith(("required", "validated"))]
        return {**out, "x-validations": texts} if texts and "$ref" not in out else out  # siblings of $ref are ignored by OpenAPI 3.0
    target = out
    rest = []
    is_string = out.get("type") == "string" or (isinstance(out.get("type"), list) and "string" in out["type"])
    is_array = out.get("type") == "array"
    for raw in validations:
        v = re.sub(r"\s+—\s+\".*\"$", "", str(raw)).strip()
        m = re.match(r"^length (\d+)\s*[–-]\s*(\d+)$", v)
        if m:
            if is_array:
                target.update(minItems=int(m.group(1)), maxItems=int(m.group(2)))
            else:
                target.update(minLength=int(m.group(1)), maxLength=int(m.group(2)))
            continue
        m = re.match(r"^length ([≥≤]) (\d+)$", v)
        if m:
            key = ("minItems" if m.group(1) == "≥" else "maxItems") if is_array else ("minLength" if m.group(1) == "≥" else "maxLength")
            target[key] = int(m.group(2))
            continue
        m = re.match(r"^([≥≤<>]) (-?[\d.]+)$", v)
        if m:
            number = _literal(m.group(2))
            op = m.group(1)
            if op == "≥":
                target["minimum"] = number
            elif op == "≤":
                target["maximum"] = number
            elif op == ">":
                target.update(minimum=number, exclusiveMinimum=True)
            else:
                target.update(maximum=number, exclusiveMaximum=True)
            continue
        m = re.match(r"^between (\S+) and (\S+)$", v)
        if m:
            if m.group(1) not in ("∞",):
                target["minimum"] = _literal(m.group(1))
            if m.group(2) not in ("∞",):
                target["maximum"] = _literal(m.group(2))
            continue
        if v == "valid e-mail address":
            target["format"] = "email"
            continue
        if v == "valid URL":
            target["format"] = "uri"
            continue
        m = re.match(r"^matches /(.*)/$", v)
        if m and m.group(1):
            target["pattern"] = m.group(1)
            continue
        if v in ("required, not blank", "required, not empty") and is_string:
            target.setdefault("minLength", 1)
            continue
        if v in ("required, not empty") and is_array:
            target.setdefault("minItems", 1)
            continue
        m = re.match(r"^(minLength|maxLength|minimum|maximum|minItems|maxItems|pattern|format)[ =:]\s*(.+)$", v)
        if m:
            target[m.group(1)] = _literal(m.group(2)) if m.group(1) not in ("pattern", "format") else m.group(2)
            continue
        m = re.match(r"^(min_length|max_length|ge|le|gt|lt|regex|pattern)=(.+)$", v)
        if m:
            key, value = m.group(1), m.group(2).strip().strip("'\"")
            mapping = {"min_length": "minLength", "max_length": "maxLength", "ge": "minimum", "le": "maximum", "regex": "pattern", "pattern": "pattern"}
            if key in ("gt", "lt"):
                target.update({"minimum" if key == "gt" else "maximum": _literal(value), "exclusiveMinimum" if key == "gt" else "exclusiveMaximum": True})
            else:
                target[mapping[key]] = value if mapping[key] == "pattern" else _literal(value)
            continue
        if v.startswith("required") or v.startswith("validated"):
            continue
        rest.append(str(raw))
    if rest:
        out["x-validations"] = rest
    return out


# --- loading the catalogue -------------------------------------------------------------------------------------------


def _analysis(analysis_id: str) -> dict:
    row = db.fetchone("SELECT a.*, r.name AS repo_name, r.default_branch, r.url AS repo_url FROM analyses a JOIN repositories r ON r.repo_id = a.repo_id WHERE a.analysis_id = ?",
                      [analysis_id])
    if row is None:
        raise AppError(404, "Analysis not found")
    if row["status"] != "completed":
        raise AppError(409, f"The analysis is {row['status']}; a spec can be generated once it completes")
    return row


def _items(analysis_id: str) -> list[dict]:
    rows = db.fetchall("SELECT * FROM analysis_catalog WHERE analysis_id = ?", [analysis_id])
    for row in rows:
        row["details"] = _loads(row.pop("details_json"), {})
    return rows


def _containers(analysis_id: str) -> list[dict]:
    return db.fetchall("SELECT name, path, ecosystem, kind, manifest FROM analysis_containers WHERE analysis_id = ? ORDER BY path", [analysis_id])


def _container_of(path: str, containers: list[dict]) -> dict | None:
    best = None
    for c in containers:
        prefix = (c["path"] or "").strip("/")
        if not prefix or path == prefix or path.startswith(prefix + "/"):
            if best is None or len(prefix) > len((best["path"] or "").strip("/")):
                best = c
    return best


def _file_text(analysis_id: str, path: str) -> str:
    row = db.fetchone("SELECT f.file_id FROM analysis_files f WHERE f.analysis_id = ? AND f.path = ?", [analysis_id, path])
    if not row:
        return ""
    content = db.fetchone("SELECT content FROM analysis_file_contents WHERE file_id = ?", [row["file_id"]])
    return (content or {}).get("content") or ""


def _manifest_version(analysis_id: str, manifest: str) -> str:
    if not manifest:
        return ""
    text = _file_text(analysis_id, manifest)
    if not text:
        return ""
    if manifest.endswith("pom.xml"):
        body = re.sub(r"<parent>[\s\S]*?</parent>|<dependencies>[\s\S]*|<build>[\s\S]*", "", text)
        m = re.search(r"<version>\s*([^<$\s]+)\s*</version>", body)
        return m.group(1) if m else ""
    if manifest.endswith("package.json"):
        m = re.search(r"\"version\"\s*:\s*\"([^\"]+)\"", text)
        return m.group(1) if m else ""
    if manifest.endswith((".gradle", ".gradle.kts")):
        m = re.search(r"^\s*version\s*=?\s*[\"']([^\"']+)[\"']", text, re.M)
        return m.group(1) if m else ""
    if manifest.endswith("pyproject.toml"):
        m = re.search(r"^\s*version\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        return m.group(1) if m else ""
    return ""


def is_rest(item: dict) -> bool:
    d = item["details"]
    style = str(d.get("style") or "REST")
    return item["kind"] == "endpoint" and style.startswith("REST") and str(d.get("method") or "").upper() in HTTP_METHODS


def overview(user: dict, analysis_id: str) -> dict:
    """Where a spec applies: services (containers) with REST endpoints, and committed specs (contract-first)."""
    require(user, "repositories", "read")
    analysis = _analysis(analysis_id)
    items = _items(analysis_id)
    containers = _containers(analysis_id)
    services: dict[str, dict] = {}
    spec_files: set[str] = set()
    for item in items:
        if item["kind"] != "endpoint":
            continue
        d = item["details"]
        if d.get("openapi", {}).get("spec"):
            spec_files.add(d["openapi"]["spec"])
        if not is_rest(item):
            continue
        container = _container_of(item["path"], containers)
        name = container["name"] if container else analysis["repo_name"]
        entry = services.setdefault(name, {"name": name, "path": (container or {}).get("path", ""), "ecosystem": (container or {}).get("ecosystem", ""),
                                           "endpoints": 0, "frameworks": set(), "spec_only": 0})
        entry["endpoints"] += 0 if d.get("from_spec") else 1
        entry["spec_only"] += 1 if d.get("from_spec") else 0
        entry["frameworks"].add(item["framework"] or "")
    out = []
    for entry in services.values():
        entry["frameworks"] = sorted(f for f in entry["frameworks"] if f)
        out.append(entry)
    from app.services import llm

    return {"analysis_id": analysis_id, "repo_name": analysis["repo_name"], "branch": analysis["branch"], "commit": analysis["commit_sha"], "applicable": bool(out),
            "services": sorted(out, key=lambda s: s["name"]), "spec_files": sorted(spec_files), "llm": llm.configured(),
            "message": "" if out else "No REST endpoints in this analysis (server-rendered pages, jobs and listeners are not part of an OpenAPI spec)."}


# --- building the document -------------------------------------------------------------------------------------------


def _options(raw: dict | None) -> dict:
    raw = raw or {}
    version = str(raw.get("version") or "3.0.3")
    if version in ("3.1", "3.1.0"):
        version = "3.1.0"
    if version not in VERSIONS:
        raise AppError(400, "version must be 3.0.3 or 3.1.0")
    fmt = str(raw.get("format") or "yaml").lower()
    if fmt not in ("yaml", "json"):
        raise AppError(400, "format must be yaml or json")
    tag_by = str(raw.get("tag_by") or "controller")
    if tag_by not in ("controller", "feature", "path"):
        raise AppError(400, "tag_by must be controller, feature or path")
    keys = raw.get("keys") or []
    if isinstance(keys, str):
        keys = [k for k in keys.split(",") if k]
    return {"version": version, "format": fmt, "tag_by": tag_by, "service": str(raw.get("service") or ""), "include_internal": _bool(raw.get("include_internal")),
            "extensions": _bool(raw.get("extensions"), True), "keys": [str(k) for k in keys], "enrich": _bool(raw.get("enrich")),
            "include_spec_only": _bool(raw.get("include_spec_only"), True)}


def _bool(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def _server_entries(items: list[dict], container: dict | None, context_paths: set[str]) -> list[dict]:
    configs = {i["name"]: i["details"].get("values") or [] for i in items if i["kind"] == "config" and
               (container is None or _container_of(i["path"], [container]) is not None)}
    ports = {v.get("profile", "default"): str(v.get("value")) for v in configs.get("server.port", []) if re.match(r"^\d+$", str(v.get("value", "")))}
    contexts = {v.get("profile", "default"): str(v.get("value") or "") for v in configs.get("server.servlet.context-path", []) + configs.get("server.context-path", [])}
    profiles = sorted(set(ports) | set(contexts) | {"default"}, key=lambda p: (p != "default", p))
    servers = []
    for profile in profiles:
        context = contexts.get(profile, contexts.get("default", next(iter(context_paths), "")))
        port = ports.get(profile, ports.get("default", ""))
        url = (f"http://localhost:{port}" if port else "") + (context or "")
        servers.append({"url": url or "/", "description": f"profile '{profile}'" + ("" if port else " (relative to the host it is deployed on)")})
    seen, out = set(), []
    for s in servers:
        if s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    return out[:8]


def _security_schemes(items: list[dict]) -> dict:
    text = " ".join(" ".join(i["details"].get("mechanisms") or []) for i in items if i["kind"] == "security").lower()
    expressions = " ".join(" ".join(i["details"].get("security", {}).get("expressions") or []) for i in items if i["kind"] == "endpoint").lower()
    schemes: dict[str, dict] = {}
    if re.search(r"jwt|bearer|resource server|oauth2resourceserver|token", text + " " + expressions):
        schemes["bearerAuth"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    if "basic" in text:
        schemes["basicAuth"] = {"type": "http", "scheme": "basic"}
    if re.search(r"oauth2 login|oauth2login", text):
        schemes["oauth2"] = {"type": "oauth2", "flows": {"authorizationCode": {"authorizationUrl": "/oauth2/authorize", "tokenUrl": "/oauth2/token", "scopes": {}}},
                             "x-inferred": True}
    if re.search(r"form login|formlogin|session", text):
        schemes["sessionCookie"] = {"type": "apiKey", "in": "cookie", "name": "JSESSIONID"}
    if re.search(r"api[-_ ]?key", text + " " + expressions):
        schemes["apiKey"] = {"type": "apiKey", "in": "header", "name": "X-API-Key", "x-inferred": True}
    if not schemes and (expressions.strip() or any(i["kind"] == "security" for i in items)):
        schemes["bearerAuth"] = {"type": "http", "scheme": "bearer", "x-inferred": True}
    return schemes


def _operation_security(details: dict, schemes: dict) -> tuple[list | None, list[str]]:
    sec = details.get("security") or {}
    rule = sec.get("http_rule") or {}
    roles = sorted(set((sec.get("roles") or []) + (rule.get("roles") or [])))
    access = str(rule.get("access") or "")
    expressions = [str(e) for e in sec.get("expressions") or []]
    if access == "permitAll" or any(re.search(r"permitAll|Public|AllowAny|anonymous", e) for e in expressions):
        return [], roles
    if not schemes:
        return None, roles
    if roles or access or expressions:
        return [{name: []} for name in schemes], roles
    return None, roles


def _humanize(name: str) -> str:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else ""


def _default_summary(handler: str, path: str) -> str:
    """'create' + /api/orders -> 'Create orders'; 'get' + /api/orders/{id} -> 'Get order'."""
    words = _humanize(handler)
    segs = [x for x in path.split("/") if x]
    literal = [x for x in segs if not x.startswith("{")]
    if not literal or len(words.split()) > 2:
        return words
    resource = literal[-1].replace("-", " ").replace("_", " ")
    if segs and segs[-1].startswith("{") and resource.endswith("s") and not resource.endswith("ss"):
        resource = resource[:-1]
    return f"{words} {resource}" if resource.lower() not in words.lower() else words


def _status_responses(details: dict, response_schema: dict | None, schemas: Schemas, media: str) -> dict:
    codes = [int(c) for c in details.get("status_codes") or [] if str(c).isdigit()]
    method = str(details.get("method") or "").upper()
    success = next((c for c in codes if 200 <= c < 300), 201 if method == "POST" and not codes else 200)
    responses: dict[str, dict] = {}
    body = {"description": REASONS.get(success, "Success")}
    if response_schema and success != 204:
        body["content"] = {media: {"schema": response_schema}}
    responses[str(success)] = body
    errors = {}
    for err in details.get("errors") or []:
        status = str(err.get("status") or "")
        if status.isdigit():
            errors.setdefault(status, []).append(err)
    for code in codes:
        if code >= 300 and str(code) not in errors:
            errors[str(code)] = []
    if (details.get("request") or {}).get("validated") and "400" not in errors:
        errors["400"] = [{"exception": "validation", "message": "request validation failed"}]
    for status, errs in sorted(errors.items()):
        names = ", ".join(sorted({e.get("exception", "") for e in errs if e.get("exception")}))
        messages = "; ".join(sorted({str(e.get("message")) for e in errs if e.get("message")}))[:300]
        text = REASONS.get(int(status), "Error") + (f" — {names}" if names else "") + (f": {messages}" if messages else "")
        responses[status] = {"description": text}
    return responses


def _parameter(param: dict, schemas: Schemas) -> dict | None:
    where = str(param.get("in") or "").lower()
    where = {"pathvariable": "path", "requestparam": "query", "requestheader": "header", "cookievalue": "cookie", "matrix": "path"}.get(where, where)
    if where not in ("path", "query", "header", "cookie"):
        return None
    name = str(param.get("name") or "").strip()
    if not name:
        return None
    schema = schemas.of_type(str(param.get("type") or "string")) or {"type": "string"}
    schema = apply_constraints(schema, param.get("validations") or [])
    if param.get("default") not in (None, ""):
        schema.setdefault("default", _literal(str(param["default"])))
    out = {"name": name, "in": where, "required": True if where == "path" else bool(param.get("required")), "schema": schema}
    if param.get("description"):
        out["description"] = str(param["description"])[:500]
    return out


def _request_body(details: dict, params: list[dict], schemas: Schemas) -> dict | None:
    request = details.get("request") or {}
    body_params = [p for p in params if str(p.get("in") or "").lower() in ("body", "requestbody")]
    form = [p for p in params if str(p.get("in") or "").lower() in ("form", "part", "requestpart", "file")]
    consumes = details.get("consumes") or []
    if request and (request.get("fields") or request.get("type")):
        schema = schemas.of_type(str(request.get("type") or request.get("name") or ""), request.get("fields"), request.get("enum"))
        if request.get("collection") and schema.get("type") != "array":
            schema = {"type": "array", "items": schema}
        if not schema:
            schema = schemas._inferred({"type": "object"})
        media = (consumes[0] if consumes else request.get("content_type")) or "application/json"
        required = bool(body_params[0].get("required", True)) if body_params else True
        return {"required": required, "content": {media: {"schema": schema}}}
    if body_params:
        p = body_params[0]
        schema = schemas.of_type(str(p.get("type") or "")) or schemas._inferred({"type": "object"})
        return {"required": bool(p.get("required", True)), "content": {(consumes[0] if consumes else "application/json"): {"schema": schema}}}
    if form:
        props, required = {}, []
        for p in form:
            props[p["name"]] = apply_constraints(schemas.of_type(str(p.get("type") or "string")) or {"type": "string"}, p.get("validations") or [])
            if p.get("required"):
                required.append(p["name"])
        media = "multipart/form-data" if any(str(p.get("in")).lower() in ("part", "requestpart", "file") or "file" in str(p.get("type", "")).lower() for p in form) \
            else "application/x-www-form-urlencoded"
        schema: dict = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return {"required": bool(required), "content": {media: {"schema": schema}}}
    return None


def _response_schema(details: dict, schemas: Schemas) -> dict | None:
    response = details.get("response") or {}
    if response.get("kind") in ("view", "redirect"):
        return None
    type_text = str(response.get("type") or "").strip()
    inner = type_text
    while True:
        m = re.match(r"^(ResponseEntity|Mono|Optional|CompletableFuture|Promise|HttpEntity|Awaitable)\s*<\s*(.*)\s*>$", inner)
        if not m:
            break
        inner = m.group(2).strip()
    if not type_text and not response.get("fields"):
        return schemas._inferred({}) if response.get("kind") == "body" or response else None
    if inner.lower() in NO_BODY:
        return None
    if inner in ("?", "") and not response.get("fields"):
        return schemas._inferred({})
    schema = schemas.of_type(type_text or str(response.get("name") or ""), response.get("fields"), response.get("enum"))
    if response.get("collection") and schema.get("type") != "array":
        schema = {"type": "array", "items": schema}
    return schema


def _path_template(path: str) -> str:
    """Express ':id', Flask '<int:id>', regex suffixes -> '{id}'."""
    p = re.sub(r":([A-Za-z_]\w*)", r"{\1}", path or "/")
    p = re.sub(r"<(?:[^:>]+:)?([^>]+)>", r"{\1}", p)
    p = re.sub(r"\{([^}:]+):[^}]*\}", r"{\1}", p)
    p = re.sub(r"/+", "/", p)
    return p if p.startswith("/") else "/" + p


def build(analysis_id: str, raw_options: dict | None = None) -> dict:
    """The spec for one service of an analysis plus warnings, per-operation completeness and validation results."""
    options = _options(raw_options)
    analysis = _analysis(analysis_id)
    items = _items(analysis_id)
    containers = _containers(analysis_id)
    service_name = options["service"]
    endpoints = [i for i in items if is_rest(i)]
    if not endpoints:
        raise AppError(409, "No REST endpoints in this analysis, so there is nothing to describe in OpenAPI.")
    by_service: dict[str, list[dict]] = {}
    container_by_name: dict[str, dict | None] = {}
    for item in endpoints:
        container = _container_of(item["path"], containers)
        name = container["name"] if container else analysis["repo_name"]
        container_by_name[name] = container
        by_service.setdefault(name, []).append(item)
    if not service_name:
        service_name = max(by_service, key=lambda n: len(by_service[n]))
    if service_name not in by_service:
        raise AppError(404, f"Service '{service_name}' has no REST endpoints")
    container = container_by_name[service_name]
    chosen = by_service[service_name]
    if options["keys"]:
        wanted = set(options["keys"])
        chosen = [i for i in chosen if i["item_key"] in wanted]
    if not options["include_spec_only"]:
        chosen = [i for i in chosen if not i["details"].get("from_spec")]
    warnings: list[dict] = []
    if not options["include_internal"]:
        before = len(chosen)
        chosen = [i for i in chosen if not INTERNAL_PATH.match(_path_template(i["details"].get("path") or ""))]
        if before != len(chosen):
            warnings.append({"level": "info", "operation": "", "message": f"{before - len(chosen)} internal endpoint(s) (actuator, health, error, docs) left out"})

    schemas = Schemas(options["version"])
    security_schemes = _security_schemes(items)
    feature_names: dict[str, list[str]] = {}
    if options["tag_by"] == "feature":
        from app.services.analyzer.impact import match_features
        from app.services.features import all_for

        feature_list = all_for(analysis["repo_id"])
        entries = [{"item_key": i["item_key"], "kind": "endpoint", "name": i["name"], "path": i["path"]} for i in chosen]
        match_features(entries, feature_list)
        names = {f["feature_id"]: f["name"] for f in feature_list}
        feature_names = {e["item_key"]: [names[f] for f in e["feature_ids"]] for e in entries}
        if not feature_list:
            warnings.append({"level": "info", "operation": "", "message": "The repository has no feature catalogue yet; operations are tagged 'Other'"})
    context_paths = {str(i["details"].get("context_path") or "") for i in chosen if i["details"].get("context_path")}

    paths: dict[str, dict] = {}
    operations: list[dict] = []
    tags: dict[str, str] = {}
    used_ids: set[str] = set()
    for item in sorted(chosen, key=lambda i: (_path_template(i["details"].get("path") or ""), i["details"].get("method", ""))):
        d = item["details"]
        method = str(d.get("method") or "GET").lower()
        path = _path_template(d.get("path") or "/")
        inferred_before, fields_before = schemas.inferred, schemas.fields
        handler = d.get("handler") or {}
        base_id = handler.get("method") or handler.get("function") or (d.get("openapi") or {}).get("operation_id") or ""
        base_id = (d.get("openapi") or {}).get("operation_id") or base_id or re.sub(r"\W+", "_", f"{method}_{path}").strip("_")
        op_id, n = base_id, 2
        while op_id in used_ids:
            op_id = f"{base_id}{n}"
            n += 1
        used_ids.add(op_id)
        if op_id != base_id:
            warnings.append({"level": "warning", "operation": op_id, "message": f"operationId '{base_id}' is used by several endpoints; renamed to '{op_id}'"})
        if options["tag_by"] == "feature":
            tag_list = feature_names.get(item["item_key"]) or ["Other"]
        elif options["tag_by"] == "path":
            segs = [s for s in path.split("/") if s and not s.startswith("{") and s.lower() not in ("api", "v1", "v2", "v3", "rest")]
            tag_list = [segs[0] if segs else "root"]
        else:
            tag_list = [handler.get("class") or (item["path"].rsplit("/", 1)[-1].rsplit(".", 1)[0] if item["path"] else "default")]
        for t in tag_list:
            tags.setdefault(t, "")
        params = [p for p in d.get("params") or [] if isinstance(p, dict)]
        parameters = [p for p in (_parameter(p, schemas) for p in params) if p]
        declared = {p["name"] for p in parameters if p["in"] == "path"}
        for name in re.findall(r"\{([^}]+)\}", path):
            if name not in declared:
                parameters.append({"name": name, "in": "path", "required": True, "schema": schemas._inferred({"type": "string"})})
                warnings.append({"level": "warning", "operation": op_id, "message": f"path parameter '{name}' has no declared type; assumed string"})
        media = ((d.get("produces") or [None])[0]) or ((d.get("response") or {}).get("content_type")) or "application/json"
        response_schema = _response_schema(d, schemas)
        operation: dict = {"tags": tag_list, "operationId": op_id}
        summary = (item.get("summary") or (d.get("openapi") or {}).get("summary") or "").strip()
        operation["summary"] = (summary.split("\n")[0][:120] if summary else _default_summary(base_id, path))
        description_parts = []
        if summary and len(summary) > 120 or "\n" in summary:
            description_parts.append(summary)
        if (d.get("openapi") or {}).get("description"):
            description_parts.append(d["openapi"]["description"])
        rules = [r for r in d.get("rules") or [] if isinstance(r, dict) and r.get("condition")]
        if rules:
            description_parts.append("**Business rules**\n" + "\n".join(f"- if `{r['condition']}` → {r.get('outcome', '')}" for r in rules[:12]))
        security, roles = _operation_security(d, security_schemes)
        if roles:
            description_parts.append(f"**Roles:** {', '.join(roles)}")
        if description_parts:
            operation["description"] = "\n\n".join(description_parts)[:4000]
        if parameters:
            operation["parameters"] = parameters
        body = _request_body(d, params, schemas)
        if body and method in ("post", "put", "patch", "delete"):
            operation["requestBody"] = body
        elif body:
            warnings.append({"level": "warning", "operation": op_id, "message": f"{method.upper()} with a request body is left out of the spec (not allowed for this method in OpenAPI 3.0)"})
        operation["responses"] = _status_responses(d, response_schema, schemas, media)
        if security is not None:
            operation["security"] = security
        if roles:
            operation["x-roles"] = roles
        if options["extensions"]:
            line = handler.get("line") or item["line_start"]
            operation["x-source"] = f"{handler.get('path') or item['path']}:{line}"
            if d.get("from_spec"):
                operation["x-spec-only"] = True
        if (d.get("openapi") or {}).get("deprecated") or any("Deprecated" in str(a) for a in d.get("annotations") or []):
            operation["deprecated"] = True
        paths.setdefault(path, {})[method] = operation
        inferred = schemas.inferred - inferred_before
        fields = schemas.fields - fields_before
        has_response = bool(response_schema) or method in ("delete",) or str(d.get("response", {}).get("type", "")).lower() in NO_BODY
        score = 100 - min(60, inferred * 15) - (0 if has_response else 25) - (0 if operation.get("summary") and summary else 10)
        if body is None and method in ("post", "put", "patch") and not params:
            score -= 15
            warnings.append({"level": "warning", "operation": op_id, "message": f"{method.upper()} {path} has no request body type in the code"})
        if not has_response:
            warnings.append({"level": "warning", "operation": op_id, "message": f"{method.upper()} {path}: response type unknown"})
        if inferred:
            warnings.append({"level": "info", "operation": op_id, "message": f"{inferred} part(s) inferred (untyped or library types)"})
        operations.append({"operation_id": op_id, "method": method.upper(), "path": path, "tags": tag_list, "summary": operation["summary"], "item_key": item["item_key"],
                           "source": f"{handler.get('path') or item['path']}:{handler.get('line') or item['line_start']}", "completeness": max(0, score),
                           "inferred": inferred, "fields": fields, "spec_only": bool(d.get("from_spec")), "deprecated": bool(operation.get("deprecated"))})

    version = _manifest_version(analysis_id, (container or {}).get("manifest", "")) or "0.0.0"
    macro = _loads(db.fetchone("SELECT macro_json FROM analyses WHERE analysis_id = ?", [analysis_id])["macro_json"], {})
    title = service_name if service_name != analysis["repo_name"] else analysis["repo_name"]
    info: dict = {"title": f"{title} API", "version": version,
                  "description": ((macro.get("business_purpose") or "").strip() + "\n\n" if macro.get("business_purpose") else "") +
                  f"Generated by Code Analyzer from `{analysis['repo_name']}`" + (f" @ `{analysis['branch']}`" if analysis["branch"] else "") +
                  (f" (commit `{(analysis['commit_sha'] or '')[:10]}`)" if analysis["commit_sha"] else "") + " — derived from the code, not hand-written."}
    if options["extensions"]:
        info["x-generated-by"] = "Code Analyzer"
        info["x-analysis-id"] = analysis_id
    spec: dict = {"openapi": options["version"], "info": info, "servers": _server_entries(items, container, context_paths), "tags": [{"name": t} for t in sorted(tags)],
                  "paths": dict(sorted(paths.items()))}
    components: dict = {}
    if schemas.components:
        components["schemas"] = dict(sorted(schemas.components.items()))
    if security_schemes:
        components["securitySchemes"] = security_schemes
    if components:
        spec["components"] = components
    if options["version"] == "3.1.0":
        spec = _exclusive_31(spec)
    if not options["extensions"]:
        spec = strip_extensions(spec)
    if options["enrich"]:
        enriched = enrich(analysis["repo_id"], spec)
        warnings.append({"level": "info", "operation": "", "message": f"LLM added descriptions / examples to {enriched} operation(s); paths, types and schemas are unchanged"})
    problems = validate(spec)
    for p in problems:
        warnings.append({"level": "error", "operation": "", "message": p})
    completeness = round(sum(o["completeness"] for o in operations) / len(operations)) if operations else 0
    text = dump(spec, options["format"])
    return {"service": service_name, "services": sorted(by_service), "options": options, "spec": spec, "text": text, "operations": operations,
            "warnings": warnings, "completeness": completeness, "valid": not problems, "stats": {"operations": len(operations), "schemas": len(schemas.components),
                                                                                             "inferred": schemas.inferred, "fields": schemas.fields},
            "filename": filename(analysis, service_name, options["format"])}


def _exclusive_31(node):
    """OpenAPI 3.1 (JSON Schema 2020-12): exclusiveMinimum / exclusiveMaximum are numbers, not flags."""
    if isinstance(node, dict):
        node = {k: _exclusive_31(v) for k, v in node.items()}
        for flag, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
            if node.get(flag) is True and bound in node:
                node[flag] = node.pop(bound)
        return node
    if isinstance(node, list):
        return [_exclusive_31(v) for v in node]
    return node


def filename(analysis: dict, service: str, fmt: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", service or analysis["repo_name"]).strip("-") or "api"
    branch = re.sub(r"[^A-Za-z0-9._-]+", "-", analysis.get("branch") or "").strip("-")
    return f"{safe}{'-' + branch if branch else ''}-openapi.{'yaml' if fmt == 'yaml' else 'json'}"


def strip_extensions(node):
    if isinstance(node, dict):
        return {k: strip_extensions(v) for k, v in node.items() if not str(k).startswith("x-")}
    if isinstance(node, list):
        return [strip_extensions(v) for v in node]
    return node


class _Dumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper, data):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_Dumper.add_representer(str, _str_presenter)


def dump(spec: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    return yaml.dump(spec, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


# --- validation (core rules of the OpenAPI 3.0 / 3.1 schema) -----------------------------------------------------------


def validate(spec: dict) -> list[str]:
    problems: list[str] = []
    if not str(spec.get("openapi", "")).startswith("3."):
        problems.append("openapi version must be 3.x")
    info = spec.get("info") or {}
    if not info.get("title") or not info.get("version"):
        problems.append("info.title and info.version are required")
    schemas = ((spec.get("components") or {}).get("schemas") or {})
    schemes = ((spec.get("components") or {}).get("securitySchemes") or {})
    op_ids: set[str] = set()

    def refs(node, where: str):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                if not ref.startswith("#/components/schemas/") or ref.split("/")[-1] not in schemas:
                    problems.append(f"{where}: unresolved $ref {ref}")
            for k, v in node.items():
                refs(v, where)
        elif isinstance(node, list):
            for v in node:
                refs(v, where)

    for path, item in (spec.get("paths") or {}).items():
        if not path.startswith("/"):
            problems.append(f"path '{path}' must start with /")
        template_params = set(re.findall(r"\{([^}]+)\}", path))
        for method, op in item.items():
            where = f"{method.upper()} {path}"
            if method.upper() not in HTTP_METHODS:
                problems.append(f"{where}: unknown method")
                continue
            if not op.get("responses"):
                problems.append(f"{where}: responses are required")
            oid = op.get("operationId")
            if oid in op_ids:
                problems.append(f"{where}: duplicate operationId {oid}")
            op_ids.add(oid)
            declared = {p["name"] for p in op.get("parameters") or [] if p.get("in") == "path"}
            if template_params - declared:
                problems.append(f"{where}: path parameter(s) {', '.join(sorted(template_params - declared))} not declared")
            if declared - template_params:
                problems.append(f"{where}: path parameter(s) {', '.join(sorted(declared - template_params))} not in the path")
            for p in op.get("parameters") or []:
                if p.get("in") == "path" and not p.get("required"):
                    problems.append(f"{where}: path parameter {p.get('name')} must be required")
            for requirement in op.get("security") or []:
                for name in requirement:
                    if name not in schemes:
                        problems.append(f"{where}: unknown security scheme {name}")
            refs(op, where)
    refs(schemas, "components")
    return problems[:200]


# --- LLM enrichment (descriptions and examples only) ---------------------------------------------------------------


def _example_for(schema: dict, components: dict, depth: int = 0):
    """A plausible example built from the schema (used to check the LLM's example keys)."""
    if depth > 4 or not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        return _example_for(components.get(schema["$ref"].split("/")[-1], {}), components, depth + 1)
    if schema.get("type") == "object":
        return {k: _example_for(v, components, depth + 1) for k, v in list((schema.get("properties") or {}).items())[:30]}
    if schema.get("type") == "array":
        return [_example_for(schema.get("items") or {}, components, depth + 1)]
    return None


def enrich(repo_id: str, spec: dict, limit: int = 40) -> int:
    """Adds summaries, descriptions and example payloads from the LLM (cached by content hash). Schemas stay as they are;
    an example is kept only when its keys are all properties of the schema."""
    from app.services import llm

    if not llm.configured():
        return 0
    components = ((spec.get("components") or {}).get("schemas") or {})
    todo = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            facts = {"method": method.upper(), "path": path, "summary": op.get("summary", ""), "description": op.get("description", "")[:600],
                     "parameters": [{"name": p["name"], "in": p["in"], "schema": p.get("schema")} for p in op.get("parameters") or []],
                     "request": _example_for(next(iter((op.get("requestBody") or {}).get("content", {}).values()), {}).get("schema", {}), components) if op.get("requestBody") else None,
                     "response": next((_example_for(next(iter(r.get("content", {}).values()), {}).get("schema", {}), components)
                                       for c, r in op.get("responses", {}).items() if c.startswith("2") and r.get("content")), None)}
            key = hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
            todo.append((key, op, facts))
    cached = {r["cache_key"]: _loads(r["payload_json"], {}) for r in db.fetchall(
        f"SELECT cache_key, payload_json FROM openapi_enrichment WHERE cache_key IN ({', '.join('?' for _ in todo)})", [k for k, _, _ in todo])} if todo else {}
    missing = [(k, op, f) for k, op, f in todo if k not in cached][:limit]
    for start in range(0, len(missing), 10):
        batch = missing[start:start + 10]
        prompt = ("OPERATIONS:\n" + json.dumps([{"id": i, **f} for i, (_, _, f) in enumerate(batch)], default=str)[:30000] +
                  "\n\nFor each operation write a short summary (max 10 words), a plain-language description (1-3 sentences) and realistic example "
                  "JSON for its request and response using ONLY the field names given (null where there is no body). Return JSON: "
                  '{"operations": [{"id": 0, "summary": "...", "description": "...", "request_example": {...}, "response_example": {...}}]}')
        try:
            raw = llm.complete_json("You document REST APIs precisely. Never invent fields or endpoints.", prompt, 0.2)
        except Exception:  # noqa: BLE001
            logger.exception("OpenAPI enrichment failed")
            break
        for entry in raw.get("operations") or []:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), int) or not 0 <= entry["id"] < len(batch):
                continue
            key = batch[entry["id"]][0]
            payload = {k: entry.get(k) for k in ("summary", "description", "request_example", "response_example")}
            cached[key] = payload
            db.exec_sql("DELETE FROM openapi_enrichment WHERE cache_key = ?", [key])
            db.insert("openapi_enrichment", {"cache_key": key, "repo_id": repo_id, "payload_json": json.dumps(payload, default=str), "created_at": db.now()})
    changed = 0
    for key, op, facts in todo:
        payload = cached.get(key)
        if not payload:
            continue
        if payload.get("summary") and (not facts["summary"] or facts["summary"] == _humanize(op.get("operationId", ""))):
            op["summary"] = str(payload["summary"])[:120]
        if payload.get("description"):
            op["description"] = (str(payload["description"])[:800] + ("\n\n" + op["description"] if op.get("description") else ""))
        if op.get("requestBody") and _fits(payload.get("request_example"), facts["request"]):
            for media in op["requestBody"].get("content", {}).values():
                media["example"] = payload["request_example"]
        if _fits(payload.get("response_example"), facts["response"]):
            for code, resp in op.get("responses", {}).items():
                if code.startswith("2"):
                    for media in resp.get("content", {}).values():
                        media["example"] = payload["response_example"]
        op["x-enriched-by"] = "llm"
        changed += 1
    return changed


def _fits(example, shape) -> bool:
    """True when every key of the example exists in the schema-derived shape (recursively)."""
    if example is None or shape is None:
        return False
    if isinstance(shape, dict):
        return isinstance(example, dict) and all(k in shape and (shape[k] is None or _fits(v, shape[k]) or v is None) for k, v in example.items())
    if isinstance(shape, list):
        return isinstance(example, list) and all(_fits(v, shape[0]) or shape[0] is None for v in example[:5])
    return True


# --- diff (breaking-change detection) ------------------------------------------------------------------------------


def _resolve(schema: dict, components: dict, depth: int = 0) -> dict:
    if not isinstance(schema, dict) or depth > 6:
        return {}
    if "$ref" in schema:
        return _resolve(components.get(schema["$ref"].split("/")[-1], {}), components, depth + 1)
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _resolve(schema["allOf"][0], components, depth + 1)
    return schema


def _type_of(schema: dict) -> str:
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "")
    return f"{t or 'any'}{'/' + schema['format'] if schema.get('format') else ''}"


def _compare_schema(old: dict, new: dict, old_c: dict, new_c: dict, where: str, direction: str, out: list, depth: int = 0) -> None:
    """direction: 'request' (stricter = breaking) or 'response' (removing = breaking)."""
    if depth > 5:
        return
    a, b = _resolve(old, old_c), _resolve(new, new_c)
    if not a or not b:
        return
    ta, tb = _type_of(a), _type_of(b)
    if ta != tb and "any" not in (ta, tb):
        out.append({"severity": "breaking", "where": where, "change": f"type changed from {ta} to {tb}"})
        return
    if a.get("type") == "array" or b.get("type") == "array":
        _compare_schema(a.get("items") or {}, b.get("items") or {}, old_c, new_c, f"{where}[]", direction, out, depth + 1)
        return
    if a.get("enum") and b.get("enum"):
        removed = set(map(str, a["enum"])) - set(map(str, b["enum"]))
        added = set(map(str, b["enum"])) - set(map(str, a["enum"]))
        if removed:
            out.append({"severity": "breaking", "where": where, "change": f"enum value(s) removed: {', '.join(sorted(removed))}"})
        if added:
            out.append({"severity": "breaking" if direction == "response" else "non-breaking", "where": where, "change": f"enum value(s) added: {', '.join(sorted(added))}"})
    for key, label in (("maxLength", "max length"), ("maximum", "maximum")):
        if key in b and (key not in a or b[key] < a[key]) and direction == "request":
            out.append({"severity": "breaking", "where": where, "change": f"{label} tightened to {b[key]}"})
    for key, label in (("minLength", "min length"), ("minimum", "minimum")):
        if key in b and (key not in a or b[key] > a[key]) and direction == "request":
            out.append({"severity": "breaking", "where": where, "change": f"{label} tightened to {b[key]}"})
    if "pattern" in b and b.get("pattern") != a.get("pattern") and direction == "request":
        out.append({"severity": "breaking", "where": where, "change": f"pattern changed to {b['pattern']}"})
    pa, pb = a.get("properties") or {}, b.get("properties") or {}
    ra, rb = set(a.get("required") or []), set(b.get("required") or [])
    for name in sorted(set(pa) | set(pb)):
        field = f"{where}.{name}"
        if name in pa and name not in pb:
            out.append({"severity": "breaking" if direction == "response" or name in ra else "non-breaking", "where": field,
                        "change": "field removed" + (" (clients reading it break)" if direction == "response" else "")})
        elif name in pb and name not in pa:
            out.append({"severity": "breaking" if direction == "request" and name in rb else "non-breaking", "where": field,
                        "change": "required field added" if name in rb and direction == "request" else "field added"})
        else:
            if direction == "request" and name in rb and name not in ra:
                out.append({"severity": "breaking", "where": field, "change": "field became required"})
            if direction == "response" and name in ra and name not in rb:
                out.append({"severity": "breaking", "where": field, "change": "field no longer guaranteed (not required)"})
            _compare_schema(pa[name], pb[name], old_c, new_c, field, direction, out, depth + 1)


def diff(old: dict, new: dict) -> dict:
    """Changes from spec `old` to spec `new` with severity breaking / non-breaking."""
    out: list[dict] = []
    oc = ((old.get("components") or {}).get("schemas") or {})
    nc = ((new.get("components") or {}).get("schemas") or {})

    def ops(spec: dict) -> dict[tuple[str, str], dict]:
        result = {}
        for path, item in (spec.get("paths") or {}).items():
            norm = re.sub(r"\{[^}]+\}", "{}", path)
            for method, op in item.items():
                if method.upper() in HTTP_METHODS:
                    result[(method.upper(), norm)] = {**op, "_path": path}
        return result

    a, b = ops(old), ops(new)
    for key in sorted(set(a) - set(b)):
        out.append({"severity": "breaking", "operation": f"{key[0]} {a[key]['_path']}", "where": "", "change": "operation removed"})
    for key in sorted(set(b) - set(a)):
        out.append({"severity": "non-breaking", "operation": f"{key[0]} {b[key]['_path']}", "where": "", "change": "operation added"})
    for key in sorted(set(a) & set(b)):
        oa, ob = a[key], b[key]
        name = f"{key[0]} {ob['_path']}"
        changes: list[dict] = []
        pa = {(p.get("in"), p.get("name")): p for p in oa.get("parameters") or []}
        pb = {(p.get("in"), p.get("name")): p for p in ob.get("parameters") or []}
        for pk in sorted(set(pa) - set(pb), key=str):
            if pk[0] != "path":
                changes.append({"severity": "non-breaking" if not pa[pk].get("required") else "breaking", "where": f"{pk[0]} parameter {pk[1]}", "change": "parameter removed"})
        for pk in sorted(set(pb) - set(pa), key=str):
            if pk[0] != "path":
                changes.append({"severity": "breaking" if pb[pk].get("required") else "non-breaking", "where": f"{pk[0]} parameter {pk[1]}",
                                "change": "required parameter added" if pb[pk].get("required") else "optional parameter added"})
        for pk in sorted(set(pa) & set(pb), key=str):
            if pb[pk].get("required") and not pa[pk].get("required"):
                changes.append({"severity": "breaking", "where": f"{pk[0]} parameter {pk[1]}", "change": "parameter became required"})
            _compare_schema(pa[pk].get("schema") or {}, pb[pk].get("schema") or {}, oc, nc, f"{pk[0]} parameter {pk[1]}", "request", changes)
        rba, rbb = oa.get("requestBody"), ob.get("requestBody")
        if rbb and not rba and rbb.get("required"):
            changes.append({"severity": "breaking", "where": "request body", "change": "required request body added"})
        if rba and rbb:
            sa = next(iter((rba.get("content") or {}).values()), {}).get("schema") or {}
            sb = next(iter((rbb.get("content") or {}).values()), {}).get("schema") or {}
            _compare_schema(sa, sb, oc, nc, "request body", "request", changes)
            ma, mb = set((rba.get("content") or {})), set((rbb.get("content") or {}))
            if ma and mb and not ma & mb:
                changes.append({"severity": "breaking", "where": "request body", "change": f"media type changed from {', '.join(ma)} to {', '.join(mb)}"})
        ok_a = {c: r for c, r in (oa.get("responses") or {}).items() if str(c).startswith("2")}
        ok_b = {c: r for c, r in (ob.get("responses") or {}).items() if str(c).startswith("2")}
        if ok_a and ok_b and not set(ok_a) & set(ok_b):
            changes.append({"severity": "breaking", "where": "responses", "change": f"success status changed from {', '.join(ok_a)} to {', '.join(ok_b)}"})
        for code in set(ok_a) & set(ok_b):
            sa = next(iter((ok_a[code].get("content") or {}).values()), {}).get("schema") or {}
            sb = next(iter((ok_b[code].get("content") or {}).values()), {}).get("schema") or {}
            _compare_schema(sa, sb, oc, nc, f"response {code}", "response", changes)
        sec_a, sec_b = oa.get("security"), ob.get("security")
        if sec_a == [] and sec_b:
            changes.append({"severity": "breaking", "where": "security", "change": "operation now requires authentication"})
        if set(oa.get("x-roles") or []) != set(ob.get("x-roles") or []) and ob.get("x-roles"):
            changes.append({"severity": "breaking" if set(ob.get("x-roles") or []) - set(oa.get("x-roles") or []) else "non-breaking", "where": "security",
                            "change": f"roles {', '.join(oa.get('x-roles') or ['(none)'])} → {', '.join(ob.get('x-roles') or ['(none)'])}"})
        if ob.get("deprecated") and not oa.get("deprecated"):
            changes.append({"severity": "non-breaking", "where": "", "change": "operation deprecated"})
        for c in changes:
            out.append({**c, "operation": name})
    breaking = [c for c in out if c["severity"] == "breaking"]
    return {"changes": out, "breaking": len(breaking), "non_breaking": len(out) - len(breaking),
            "operations": {"old": len(a), "new": len(b), "added": len(set(b) - set(a)), "removed": len(set(a) - set(b))}}


# --- API: generate, download, drift, compare --------------------------------------------------------------------------


def generate(user: dict, analysis_id: str, options: dict | None = None) -> dict:
    require(user, "repositories", "read")
    return build(analysis_id, options)


def download(user: dict, analysis_id: str, options: dict | None = None) -> tuple[bytes, str, str]:
    """(content, filename, media type): one spec, or a zip with one spec per service when service == '*'."""
    require(user, "repositories", "read")
    opts = dict(options or {})
    if opts.get("service") == "*":
        info = overview(user, analysis_id)
        buffer = io.BytesIO()
        analysis = _analysis(analysis_id)
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            for service in info["services"]:
                result = build(analysis_id, {**opts, "service": service["name"]})
                z.writestr(result["filename"], result["text"])
        base = filename(analysis, analysis["repo_name"], "yaml").rsplit(".", 1)[0]
        return buffer.getvalue(), f"{base}-all-services.zip", "application/zip"
    result = build(analysis_id, opts)
    media = "application/json" if result["options"]["format"] == "json" else "application/yaml"
    return result["text"].encode("utf-8"), result["filename"], media


def _committed(analysis_id: str, spec_path: str) -> dict:
    text = _file_text(analysis_id, spec_path)
    if not text:
        raise AppError(404, f"Spec file {spec_path} is not stored in this analysis")
    try:
        data = json.loads(text) if spec_path.endswith(".json") else yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        raise AppError(400, f"{spec_path} could not be parsed: {exc}") from exc
    if not isinstance(data, dict) or not (data.get("openapi") or data.get("swagger")):
        raise AppError(400, f"{spec_path} is not an OpenAPI document")
    return swagger2_to_3(data) if data.get("swagger") else data


def swagger2_to_3(doc: dict) -> dict:
    """Enough of a Swagger 2.0 -> OpenAPI 3 conversion for comparing contracts (parameters, bodies, responses, models)."""
    text = json.dumps(doc).replace("#/definitions/", "#/components/schemas/")
    doc = json.loads(text)
    paths = {}
    for path, item in (doc.get("paths") or {}).items():
        shared = [p for p in item.get("parameters") or [] if isinstance(p, dict)]
        for method, op in item.items():
            if method.upper() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            new = {k: v for k, v in op.items() if k not in ("parameters", "responses", "consumes", "produces")}
            params, body = [], None
            for p in shared + list(op.get("parameters") or []):
                if p.get("in") == "body":
                    body = {"required": bool(p.get("required")), "content": {(op.get("consumes") or doc.get("consumes") or ["application/json"])[0]: {"schema": p.get("schema") or {}}}}
                elif p.get("in") == "formData":
                    continue
                else:
                    schema = {k: p[k] for k in ("type", "format", "enum", "minimum", "maximum", "minLength", "maxLength", "pattern", "items", "default") if k in p}
                    params.append({"name": p.get("name"), "in": p.get("in"), "required": bool(p.get("required")) or p.get("in") == "path", "schema": schema})
            if params:
                new["parameters"] = params
            if body:
                new["requestBody"] = body
            responses = {}
            media = (op.get("produces") or doc.get("produces") or ["application/json"])[0]
            for code, resp in (op.get("responses") or {}).items():
                r = {"description": (resp or {}).get("description", "")}
                if (resp or {}).get("schema"):
                    r["content"] = {media: {"schema": resp["schema"]}}
                responses[str(code)] = r
            new["responses"] = responses
            paths.setdefault(path, {})[method] = new
    return {"openapi": "3.0.3", "info": doc.get("info") or {}, "paths": paths, "components": {"schemas": doc.get("definitions") or {}},
            "x-converted-from": "swagger 2.0"}


def drift(user: dict, analysis_id: str, spec_path: str = "", service: str = "") -> dict:
    """Committed spec (contract-first) vs what the code implements: operations missing on either side and contract
    differences, using the same comparison as the breaking-change check (spec = before, code = after)."""
    require(user, "repositories", "read")
    info = overview(user, analysis_id)
    if not info["spec_files"]:
        raise AppError(409, "No committed OpenAPI / Swagger file was found in this analysis")
    spec_path = spec_path or info["spec_files"][0]
    committed = _committed(analysis_id, spec_path)
    code = build(analysis_id, {"service": service, "include_spec_only": False, "include_internal": True, "extensions": True})["spec"]
    result = diff(committed, code)
    for change in result["changes"]:
        if change["change"] == "operation removed":
            change["change"] = "in the spec but not implemented in the code"
        elif change["change"] == "operation added":
            change["change"] = "implemented in the code but not in the spec"
            change["severity"] = "undocumented"
    result["spec_path"] = spec_path
    result["spec_files"] = info["spec_files"]
    result["swagger2"] = bool(committed.get("swagger"))
    return result


def compare_analyses(left_id: str, right_id: str) -> dict:
    """Breaking-change diff of the generated specs of two analyses (every service merged per side)."""
    def merged(analysis_id: str) -> dict | None:
        try:
            info_rows = _items(analysis_id)
        except AppError:
            return None
        if not any(is_rest(i) for i in info_rows):
            return None
        containers = _containers(analysis_id)
        names = sorted({(_container_of(i["path"], containers) or {}).get("name") or "" for i in info_rows if is_rest(i)})
        spec: dict = {"paths": {}, "components": {"schemas": {}}}
        for name in names:
            part = build(analysis_id, {"service": name or "", "extensions": True, "include_internal": False})["spec"]
            for path, item in part["paths"].items():
                spec["paths"].setdefault(path, {}).update(item)
            spec["components"]["schemas"].update(((part.get("components") or {}).get("schemas") or {}))
        return spec

    try:
        old, new = merged(left_id), merged(right_id)
    except AppError as exc:
        return {"available": False, "message": exc.detail}
    if old is None or new is None:
        return {"available": False, "message": "One side has no REST endpoints."}
    return {"available": True, **diff(old, new)}


def compare_user(user: dict, left_id: str, right_id: str) -> dict:
    require(user, "repositories", "read")
    _analysis(left_id)
    _analysis(right_id)
    return compare_analyses(left_id, right_id)


# --- external APIs (drafted from client calls in a landscape) -----------------------------------------------------------


def external_spec(user: dict, landscape_id: str, name: str, version: str = "3.0.3", fmt: str = "yaml") -> dict:
    """An OpenAPI draft of an external API *as it is used*: every call the landscape's repositories make to it, with the
    request / response types their clients declare (Retrofit, Feign, typed fetch clients)."""
    require(user, "insights", "read")
    options = _options({"version": version, "format": fmt})
    rows = db.fetchall(
        "SELECT * FROM landscape_links WHERE landscape_id = ? AND kind = 'http' AND status = 'external' AND external_name = ?", [landscape_id, name])
    if not rows:
        raise AppError(404, f"No HTTP calls to {name} in this landscape")
    schemas = Schemas(options["version"])
    paths: dict[str, dict] = {}
    operations, used_ids = [], set()
    repos = {r["repo_id"]: r["name"] for r in db.fetchall("SELECT repo_id, name FROM repositories")}
    hosts: set[str] = set()
    for row in rows:
        base_key = row["source_key"].split("#")[0]
        item = db.fetchone("SELECT * FROM analysis_catalog WHERE analysis_id = ? AND item_key = ?", [row["source_analysis_id"], base_key])
        details = _loads(item["details_json"], {}) if item else {}
        label = row["label"].split(" · ")[0]
        method, _, url = label.partition(" ")
        method = (method or "GET").upper()
        if method not in HTTP_METHODS:
            method, url = "GET", label
        from urllib.parse import urlparse

        parsed = urlparse(re.sub(r"^/?(https?):/+", r"\1://", url.strip()))
        if parsed.scheme:
            hosts.add(f"{parsed.scheme}://{parsed.netloc}")
            path = parsed.path or "/"
        else:
            path = url.split("?")[0] or "/"
        path = _path_template(re.sub(r"\$\{([^}]+)\}", r"{\1}", path))
        path = re.sub(r"\{([^}]*[^A-Za-z0-9_}][^}]*)\}", lambda m: "{" + re.sub(r"\W+", "_", m.group(1)).strip("_") + "}", path)
        op_id_base = re.sub(r"\W+", "_", (details.get("operation") or f"{method.lower()}_{path}")).strip("_") or "call"
        op_id, n = op_id_base, 2
        while op_id in used_ids:
            op_id, n = f"{op_id_base}{n}", n + 1
        existing = paths.get(path, {}).get(method.lower())
        consumer = f"{repos.get(row['source_repo_id'], '')}: {row['source_name']} ({row['source_path']}:{row['source_line']})"
        if existing:
            existing.setdefault("x-consumers", []).append(consumer)
            continue
        used_ids.add(op_id)
        params = [p for p in details.get("params") or [] if isinstance(p, dict)]
        parameters = [p for p in (_parameter(p, schemas) for p in params) if p]
        for pname in re.findall(r"\{([^}]+)\}", path):
            if pname not in {p["name"] for p in parameters if p["in"] == "path"}:
                parameters.append({"name": pname, "in": "path", "required": True, "schema": schemas._inferred({"type": "string"})})
        op: dict = {"operationId": op_id, "summary": _humanize(details.get("operation") or "") or f"{method} {path}", "x-consumers": [consumer]}
        if parameters:
            op["parameters"] = parameters
        body = _request_body(details, params, schemas)
        if body and method in ("POST", "PUT", "PATCH", "DELETE"):
            op["requestBody"] = body
        response = _response_schema(details, schemas) if details.get("response") else None
        op["responses"] = {"200": {"description": "Response as the client expects it", **({"content": {"application/json": {"schema": response}}} if response else {})}}
        paths.setdefault(path, {})[method.lower()] = op
        operations.append({"operation_id": op_id, "method": method, "path": path, "consumer": consumer})
    spec = {"openapi": options["version"],
            "info": {"title": f"{name} (as used)", "version": "0.0.0-observed",
                     "description": f"Drafted by Code Analyzer from the calls the repositories of this landscape make to **{name}**. It covers only what is used, "
                                    "with the types the clients declare; check it against the provider's documentation."},
            "servers": [{"url": h} for h in sorted(hosts)] or [{"url": f"https://{name}"}], "paths": dict(sorted(paths.items()))}
    if schemas.components:
        spec["components"] = {"schemas": dict(sorted(schemas.components.items()))}
    problems = validate(spec)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    warnings = [{"level": "error", "operation": "", "message": p} for p in problems]
    if schemas.inferred:
        warnings.append({"level": "info", "operation": "", "message": f"{schemas.inferred} part(s) inferred from untyped calls"})
    return {"spec": spec, "text": dump(spec, options["format"]), "operations": operations, "valid": not problems, "warnings": warnings,
            "filename": f"{safe}-as-used-openapi.{'yaml' if options['format'] == 'yaml' else 'json'}", "service": name, "options": options,
            "completeness": None, "stats": {"operations": len(operations), "schemas": len(schemas.components), "inferred": schemas.inferred, "fields": schemas.fields}}


def copy_spec(spec: dict) -> dict:
    return deepcopy(spec)
