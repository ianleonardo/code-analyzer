"""System landscapes: end-to-end linkage of interfaces across repositories.

A landscape is a named group of repositories. For each member the latest completed analysis of its default branch (or a
pinned analysis) supplies the layer catalogue, and this module links:

  http       a caller (screen / API client / Feign or Retrofit interface / RestTemplate, WebClient, fetch call) in one
             repository to an endpoint in another — method + path template, context path, base-URL configuration and
             service identity (spring.application.name, repository and container names, Feign name) as evidence
  messaging  a publisher of a Kafka / RabbitMQ / JMS / SQS topic to its consumers in other repositories
  database   repositories that use the same table (same datasource / engine as evidence); device-local stores excluded
  external   calls nobody in the landscape provides (by host), topics without a consumer, databases, brokers and
             external APIs found in each repository

Every candidate gets a score and a confidence (high >= 0.75, medium >= 0.5, else low). Candidates of one caller form a
group (group_key); a group whose best two candidates are close is ambiguous. The landscape's auto-accept policy
('high' or 'high_medium') decides which unambiguous groups are accepted without asking; the rest wait in the review
queue. Review decisions are stored per group_key and re-applied whenever the links are recomputed (after a new analysis
of a member repository), so a question is asked once.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app import db
from app.errors import AppError
from app.permissions import require

logger = logging.getLogger(__name__)

POLICIES = ("high", "high_medium")
KINDS = ("http", "messaging", "database", "external")
ACTIVE = ("auto", "confirmed")
LOCAL_STORES = ("sqlite", "room", "core data", "coredata", "realm", "indexeddb", "userdefaults", "datastore", "hive")
ENTRY_KINDS = ("screen", "endpoint", "job", "listener", "api_client")
CORE_STOPWORDS = {"service", "services", "svc", "api", "apis", "app", "application", "server", "backend", "frontend", "web", "ms", "micro",
                  "microservice", "client", "rest", "http", "https", "www", "com", "net", "org", "io", "internal", "local", "prod", "uat", "sit",
                  "dev", "test", "v1", "v2", "v3", "gateway", "the", "mobile", "android", "ios", "core", "lib", "module", "impl", "main", "src"}


def _loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


# --- normalisation ------------------------------------------------------------------------------------------------

_PARAM = re.compile(r"^(\{[^}]*\}|\$\{[^}]*\}|:[A-Za-z_]\w*|<[^>]+>|\d+|[0-9a-f]{8}-[0-9a-f-]{27,})$", re.I)


def split_url(url: str) -> tuple[str, str]:
    """'https://host:8080/a/b?x' -> ('host', '/a/b'); relative paths keep host ''."""
    text = re.sub(r"^/?(https?):/+", r"\1://", (url or "").strip().strip("\"'`"))
    if re.match(r"^[a-z][a-z0-9+.-]*://", text, re.I):
        parsed = urlparse(text)
        return (parsed.hostname or "").lower(), parsed.path or "/"
    text = text.split("?")[0].split("#")[0]
    return "", text


def path_segments(path: str) -> list[str]:
    out = []
    for raw in re.sub(r"\$\{[^}]*\}", "{}", path or "").split("/"):
        seg = raw.strip()
        if not seg:
            continue
        out.append("{}" if _PARAM.match(seg) or "{" in seg or "$" in seg else seg.lower())
    return out


def norm_path(path: str) -> str:
    return "/" + "/".join(path_segments(path))


def tokens(text: str) -> set[str]:
    """Identity tokens of a name / host / key: lower-cased words, camelCase split, without generic words."""
    if not text:
        return set()
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    words = {w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if len(w) > 1}
    return {w for w in words if w not in CORE_STOPWORDS}


def ident(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def topic_norm(name: str) -> str:
    return (name or "").strip().lower()


def topic_fuzzy(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", topic_norm(name))


def datasource_id(url: str) -> str:
    """jdbc:oracle:thin:@//db:1521/SHOP -> 'db:1521/shop'; jdbc:mariadb://maria:3306/shop -> 'maria:3306/shop'."""
    text = (url or "").strip()
    if not text or "${" in text and ":" not in text.split("${", 1)[1]:
        return ""
    text = re.sub(r"^\$\{[^:}]+:(.*)\}$", r"\1", text)
    m = re.search(r"@(?://)?([\w.-]+)(?::(\d+))?[/:]([\w.$-]+)", text)
    if m:
        return f"{m.group(1)}:{m.group(2) or ''}/{m.group(3)}".lower()
    m = re.search(r"//([\w.-]+)(?::(\d+))?/([\w.$-]+)", text)
    if m:
        return f"{m.group(1)}:{m.group(2) or ''}/{m.group(3)}".lower()
    return ""


# --- loading a member repository ----------------------------------------------------------------------------------


@dataclass
class Member:
    repo: dict
    analysis_id: str
    branch: str = ""
    items: dict[str, dict] = field(default_factory=dict)
    links: list[dict] = field(default_factory=list)
    config: dict[str, list[dict]] = field(default_factory=dict)
    identity: set[str] = field(default_factory=set)
    cores: set[str] = field(default_factory=set)
    context_paths: set[str] = field(default_factory=set)
    base_hints: list[tuple[str, str, str]] = field(default_factory=list)
    ports: set[str] = field(default_factory=set)
    datasources: set[str] = field(default_factory=set)
    externals: list[dict] = field(default_factory=list)
    features: dict[str, list[str]] = field(default_factory=dict)

    @property
    def repo_id(self) -> str:
        return self.repo["repo_id"]

    @property
    def name(self) -> str:
        return self.repo["name"]


def latest_analysis(repo: dict) -> dict | None:
    return db.fetchone(
        """
        SELECT analysis_id, branch, finished_at FROM analyses WHERE repo_id = ? AND status = 'completed' AND coalesce(purpose, 'branch') = 'branch'
        ORDER BY CASE WHEN branch = ? THEN 0 ELSE 1 END, finished_at DESC LIMIT 1
        """,
        [repo["repo_id"], repo.get("default_branch") or ""],
    )


def load_member(repo: dict, pinned: str = "") -> Member | None:
    from app.services.analyzer.impact import match_features
    from app.services.features import all_for

    row = db.fetchone("SELECT analysis_id, branch FROM analyses WHERE analysis_id = ? AND status = 'completed'", [pinned]) if pinned else None
    row = row or latest_analysis(repo)
    if row is None:
        return None
    member = Member(repo=repo, analysis_id=row["analysis_id"], branch=row.get("branch") or "")
    for item in db.fetchall("SELECT * FROM analysis_catalog WHERE analysis_id = ?", [member.analysis_id]):
        item["details"] = _loads(item.pop("details_json"), {})
        member.items[item["item_key"]] = item
    member.links = db.fetchall("SELECT source_key, target_key, kind, detail FROM analysis_catalog_links WHERE analysis_id = ?", [member.analysis_id])
    for item in member.items.values():
        if item["kind"] == "config":
            member.config[item["name"]] = [v for v in item["details"].get("values") or [] if isinstance(v, dict)]
    # identity: repository, slug, application name, container names
    names = [repo["name"], repo.get("slug") or "", repo["name"].split("/")[-1]]
    names += [v.get("value", "") for v in member.config.get("spring.application.name", [])]
    names += [c["name"] for c in db.fetchall("SELECT name FROM analysis_containers WHERE analysis_id = ?", [member.analysis_id])]
    member.identity = {ident(n) for n in names if ident(n) and len(ident(n)) > 2}
    for n in names:
        member.cores |= tokens(n)
    for key in ("server.servlet.context-path", "server.context-path", "spring.webflux.base-path", "server.servlet.contextPath"):
        member.context_paths |= {norm_path(v.get("value", "")) for v in member.config.get(key, []) if v.get("value")}
    for item in member.items.values():
        if item["kind"] == "endpoint" and item["details"].get("context_path"):
            member.context_paths.add(norm_path(item["details"]["context_path"]))
    member.context_paths.discard("/")
    for key, values in member.config.items():
        if re.search(r"(?:server\.port|\bport\b|http\.port)", key, re.I):
            for v in values:
                val = str(v.get("value") or "")
                pm = re.search(r":(\d{2,5})\b", val) or re.search(r"\b(\d{2,5})\b", val)
                if pm and 1000 <= int(pm.group(1)) <= 65535:
                    member.ports.add(pm.group(1))
        for v in values:
            value = str(v.get("value") or "")
            if re.match(r"^https?://", value):
                member.base_hints.append((key, v.get("profile", "default"), value))
            if "datasource" in key.lower() and "url" in key.lower() or value.startswith("jdbc:"):
                ds = datasource_id(value)
                if ds:
                    member.datasources.add(ds)
    for c in db.fetchall("SELECT name FROM analysis_containers WHERE analysis_id = ?", [member.analysis_id]):
        cname = c["name"]
        cpm = re.search(r":(\d{2,5})\b", cname)
        if cpm and 1000 <= int(cpm.group(1)) <= 65535:
            member.ports.add(cpm.group(1))
    for item in member.items.values():
        if item["kind"] in ("config", "constants"):
            sig = str(item.get("signature") or item.get("name") or "")
            for m in re.finditer(r"https?://[^\s'\"`,]+", sig):
                url = m.group(0).rstrip("/)")
                member.base_hints.append((item["name"], "default", url))
        elif item["kind"] in ("api_client", "integration", "service"):
            b = item["details"].get("base_url") or item["details"].get("base")
            if b and isinstance(b, str) and b.startswith("http"):
                member.base_hints.append((item["name"], "default", b))
    analysis = db.fetchone("SELECT macro_json FROM analyses WHERE analysis_id = ?", [member.analysis_id]) or {}
    member.externals = _loads(analysis.get("macro_json"), {}).get("external_systems", []) or []
    # feature names of entry points (from the repository's feature catalogue)
    feature_list = all_for(repo["repo_id"])
    if feature_list:
        entries = [{"item_key": i["item_key"], "kind": i["kind"], "name": i["name"], "path": i["path"]} for i in member.items.values() if i["kind"] in ENTRY_KINDS]
        match_features(entries, feature_list)
        names_by_id = {f["feature_id"]: f["name"] for f in feature_list}
        member.features = {e["item_key"]: [names_by_id[f] for f in e["feature_ids"]] for e in entries if e["feature_ids"]}
    return member


# --- interfaces of one member ---------------------------------------------------------------------------------------


def _resolve_variants(text: str, member: Member) -> list[tuple[str, str]]:
    """Expands ${key} placeholders with the member's configuration (every profile). Returns [(profile, value)]."""
    if "${" not in (text or ""):
        return [("", text or "")]
    out: list[tuple[str, str]] = []
    keys = re.findall(r"\$\{([^}:]+)(?::[^}]*)?\}", text)
    key = keys[0] if keys else ""
    for v in member.config.get(key, [])[:6]:
        out.append((v.get("profile", "default"), re.sub(r"\$\{" + re.escape(key) + r"(?::[^}]*)?\}", str(v.get("value") or ""), text, count=1)))
    default = re.sub(r"\$\{[^}:]+:([^}]*)\}", r"\1", text)
    if default != text and "${" not in default:
        out.append(("default", default))
    return out or [("", text)]


def http_calls(member: Member) -> list[dict]:
    """Outbound HTTP calls of a member: API clients / integrations, calls made inside services, unresolved screen calls."""
    calls: dict[str, dict] = {}
    screens_of: dict[str, list[str]] = {}
    for link in member.links:
        source = member.items.get(link["source_key"])
        if source and source["kind"] == "screen":
            screens_of.setdefault(link["target_key"], []).append(source["name"])

    def add(key: str, item: dict, method: str, url: str, base: str = "", target: str = "", client: str = "", via: str = "", line: int = 0, name: str = "", more: dict | None = None) -> None:
        if not url and not base:
            return
        url = re.sub(r"^/?(https?):/+", r"\1://", url or "")
        name = re.sub(r"/?(https?):/+", r"\1://", name or item["name"])
        base = re.sub(r"^/?(https?):/+", r"\1://", base or "")
        calls.setdefault(key, {
            "source_key": key, "item_key": item["item_key"], "source_name": name, "source_kind": item["kind"], "path": item["path"],
            "line": line or item["line_start"], "method": (method or "").upper(), "url": url or "", "base": base or "", "target": target or "",
            "client": client or "", "via": via or "", "screens": sorted(set(screens_of.get(item["item_key"], [])))[:10],
            "features": member.features.get(item["item_key"], []),
            **(more or {}),
        })

    for item in member.items.values():
        d = item["details"]
        if item["kind"] in ("api_client", "integration"):
            for used in d.get("used_by") or []:
                if isinstance(used, dict) and used.get("kind") == "screen":
                    screens_of.setdefault(item["item_key"], []).append(used.get("name", ""))
            if d.get("endpoint"):
                continue  # resolved inside the same repository
            more = {"unresolved": d.get("unresolved") or [], "fraction": float(d.get("confidence", 1.0) or 0), "variants": (d.get("base_variants") or []) + (d.get("variants") or []),
                    "version": d.get("version", ""), "condition": d.get("condition", ""), "note": d.get("method_source", "") if d.get("passthrough") else ""}
            add(item["item_key"], item, d.get("method", ""), d.get("path", ""), d.get("base_url", ""), d.get("target", ""), d.get("client", ""), d.get("via", ""), more=more)
    client_sigs = {(c["method"], norm_path(split_url(c["url"])[1])) for c in calls.values()}
    client_sigs |= {(str(i["details"].get("method", "")).upper(), norm_path(split_url(i["details"].get("path", ""))[1]))
                    for i in member.items.values() if i["kind"] in ("api_client", "integration")}
    for item in member.items.values():
        d = item["details"]
        outbound = list(d.get("http_calls") or [])
        for method in d.get("methods") or []:
            if isinstance(method, dict):
                outbound += [dict(h, _method=method.get("name", "")) for h in method.get("http_calls") or []]
        for h in outbound:
            if not isinstance(h, dict) or h.get("endpoint"):
                continue
            label = f"{item['name']}{'.' + h['_method'] if h.get('_method') else ''} → {h.get('method', '')} {h.get('url', '')}"
            add(f"{item['item_key']}#http:{h.get('method', '')} {h.get('url', '')}", item, h.get("method", ""), h.get("url", ""),
                base=h.get("base", "") or h.get("base_url", ""), target=h.get("target", ""), client=h.get("client", ""),
                via=h.get("client", "") or h.get("via", ""), line=h.get("line", 0), name=label)
        if item["kind"] == "screen":
            for call in d.get("backend_calls") or []:
                if not isinstance(call, dict) or call.get("endpoint") or call.get("client"):
                    continue
                if (str(call.get("method", "")).upper(), norm_path(split_url(call.get("url", ""))[1])) in client_sigs:
                    continue  # the same call is already listed through the API client the screen uses
                add(f"{item['item_key']}#call:{call.get('method', '')} {call.get('url', '')}", item, call.get("method", ""), call.get("url", ""), via=call.get("via", ""),
                    line=call.get("line", 0), name=f"{item['name']} → {call.get('method', '')} {call.get('url', '')}")
    for call in calls.values():
        call["screens"] = sorted(set(call["screens"] + screens_of.get(call["item_key"], [])))[:10]
    return list(calls.values())


def endpoints(member: Member) -> list[dict]:
    out = []
    for item in member.items.values():
        if item["kind"] != "endpoint":
            continue
        d = item["details"]
        path = d.get("path") or re.sub(r"^[A-Z]+\s+", "", item["name"])
        full = d.get("url") or path
        out.append({"item": item, "method": (d.get("method") or item["name"].split(" ")[0]).upper(), "path": norm_path(path), "full": norm_path(full),
                    "segments": path_segments(path), "full_segments": path_segments(full), "has_context": norm_path(full) != norm_path(path)})
    return out


BROKER_MAPPINGS = {
    "kafka": ("Kafka", "kafka"),
    "rabbitmq": ("RabbitMQ", "rabbitmq"),
    "rabbit": ("RabbitMQ", "rabbitmq"),
    "amqp": ("RabbitMQ", "rabbitmq"),
    "activemq": ("ActiveMQ", "activemq"),
    "artemis": ("ActiveMQ Artemis", "activemq"),
    "ibm-mq": ("IBM MQ", "ibm-mq"),
    "ibmmq": ("IBM MQ", "ibm-mq"),
    "ibm_mq": ("IBM MQ", "ibm-mq"),
    "wmq": ("IBM MQ", "ibm-mq"),
    "mq": ("IBM MQ", "ibm-mq"),
    "jms": ("JMS Broker", "jms"),
    "sjms": ("JMS Broker", "jms"),
    "sqs": ("AWS SQS", "sqs"),
    "pubsub": ("Google Cloud Pub/Sub", "pubsub"),
    "servicebus": ("Azure Service Bus", "servicebus"),
    "mqtt": ("MQTT Broker", "mqtt"),
    "paho": ("MQTT Broker", "mqtt"),
    "pulsar": ("Apache Pulsar", "pulsar"),
    "nats": ("NATS", "nats"),
}


def _broker_info(transport: str, default_name: str = "") -> tuple[str, str]:
    t = (transport or "").lower().strip()
    for key, (bname, btype) in BROKER_MAPPINGS.items():
        if key in t:
            return bname, btype
    d = (default_name or "").lower().strip()
    for key, (bname, btype) in BROKER_MAPPINGS.items():
        if key in d:
            return bname, btype
    if default_name:
        return default_name, "messaging"
    return "Message Broker", "messaging"


def topics(member: Member) -> tuple[list[dict], list[dict]]:
    """(publishers, consumers): [{topic_key, transport, name, item}]."""
    pubs, cons = [], []
    for link in member.links:
        if link["kind"] == "publishes" and link["target_key"] in member.items:
            topic, source = member.items[link["target_key"]], member.items.get(link["source_key"])
            if source:
                pubs.append({"topic": topic, "transport": topic["details"].get("transport", ""), "name": topic["details"].get("name") or topic["name"], "item": source})
        elif link["kind"] == "delivers to" and link["source_key"] in member.items:
            topic, target = member.items[link["source_key"]], member.items.get(link["target_key"])
            if target:
                cons.append({"topic": topic, "transport": topic["details"].get("transport", ""), "name": topic["details"].get("name") or topic["name"], "item": target})
    return pubs, cons


def tables(member: Member) -> list[dict]:
    out = []
    for item in member.items.values():
        if item["kind"] != "table":
            continue
        d = item["details"]
        stores = " ".join(str(x) for x in d.get("databases") or []).lower() + " " + str(d.get("orm") or "").lower()
        if any(s in stores for s in LOCAL_STORES):
            continue
        crud = d.get("crud") or {}
        ops = sorted(op for op, users in crud.items() if users)
        name = (d.get("table") or item["name"]).split(".")[-1].lower()
        out.append({"item": item, "name": name, "ops": ops or ["DDL"], "engines": {str(x).lower() for x in d.get("databases") or []}})
    return out


# --- matching -----------------------------------------------------------------------------------------------------


def _seg_match(call: list[str], ep: list[str]) -> tuple[bool, bool]:
    """(matches, exact): segment-wise compare where a template parameter on either side matches any one segment."""
    if len(call) != len(ep):
        return False, False
    exact = True
    for c, e in zip(call, ep):
        if c == e:
            continue
        if e == "{}" or c == "{}":
            exact = exact and e == c
            continue
        return False, False
    return True, exact


def _version(item: dict) -> str:
    d = item.get("details") or {}
    if d.get("version"):
        return str(d["version"])
    m = re.search(r"(?i)(?:^|/)(v\d+(?:\.\d+)?)(?=/|$)", str(d.get("url") or d.get("path") or item.get("name") or ""))
    return m.group(1).lower() if m else ""


def _unversioned(segments: list[str]) -> list[str]:
    return [seg for seg in segments if not re.match(r"^v\d+(?:\.\d+)?$", seg)]


def _literal(call: list[str], ep: list[str]) -> bool:
    """At least one literal segment must be equal on both sides: a {parameter} matching a word says nothing about which
    endpoint is meant (/legacy/{path} is not /accounts)."""
    return any(c == e and c != "{}" for c, e in zip(call, ep))


def _suffix(call: list[str], ep: list[str]) -> bool:
    if not ep or len(call) <= len(ep) or len(call) - len(ep) > 3:
        return False
    tail = call[len(call) - len(ep):]
    return _literal(tail, ep) and _seg_match(tail, ep)[0]


def _prefix_stripped_match(call: list[str], ep: list[str]) -> bool:
    """Matches when the backend endpoint has an API gateway or version prefix (e.g. /api/v1/products)
    while the client call uses the relative resource path (e.g. /products)."""
    if not call or len(call) >= len(ep) or len(ep) - len(call) > 3:
        return False
    suffix = ep[len(ep) - len(call):]
    if not _literal(call, suffix) or not _seg_match(call, suffix)[0]:
        return False
    prefix = ep[: len(ep) - len(call)]
    common_api_prefixes = {"api", "v1", "v2", "v3", "v4", "v5", "rest", "services", "public", "app", "internal", "api-v1"}
    return any(p.lower() in common_api_prefixes or re.match(r"^v\d+$", p, re.I) for p in prefix)


def _call_variants(call: dict, member: Member) -> list[dict]:
    """Full-URL variants of a call: [{host, segments, source, profile}] from the call's own URL, its base and the
    repository's base-URL configuration (hints)."""
    variants = []
    for profile, base in _resolve_variants(call["base"], member) if call["base"] else [("", "")]:
        for uprofile, url in _resolve_variants(call["url"], member):
            host, path = split_url(url)
            if not host and base:
                bhost, bpath = split_url(base)
                host, path = bhost, (bpath.rstrip("/") + "/" + path.lstrip("/")) if path else bpath
                source = "base"
            else:
                source = "url"
            variants.append({"host": host, "segments": path_segments(path), "source": source, "profile": uprofile or profile, "raw": url})
    for alt in call.get("variants") or []:
        host, path = split_url(alt)
        if host:
            full = path if re.match(r"^/", call["url"] or "/") and not call["base"] else path
            if call["base"] and call["url"]:
                full = path.rstrip("/") + "/" + (call["url"] or "").lstrip("/")
            elif not call["base"] and call["url"] and path in ("", "/"):
                full = call["url"]
            variants.append({"host": host, "segments": path_segments(full), "source": "variant", "profile": "other profile", "raw": alt})
    if not any(v["host"] for v in variants):
        for key, profile, value in member.base_hints[:8]:
            bhost, bpath = split_url(value)
            for v in list(variants):
                if v["source"] == "url":
                    variants.append({"host": bhost, "segments": path_segments(bpath) + v["segments"], "source": f"hint:{key}", "profile": profile, "raw": value})
    seen, out = set(), []
    for v in variants:
        sig = (v["host"], tuple(v["segments"]))
        if sig not in seen:
            seen.add(sig)
            out.append(v)
    return out[:20]


def _identity_evidence(call: dict, variants: list[dict], provider: Member) -> tuple[float, list[str]]:
    """How strongly the call names the provider: Feign name, host of the URL, base-URL hints, client / key names, port matching.
    Independent kinds of evidence add up (at most 0.7)."""
    evidence: list[str] = []
    score = 0.0
    hosts = {v["host"] for v in variants if v["host"] and not v["source"].startswith("hint:")}
    hint_hosts = {v["host"] for v in variants if v["host"] and v["source"].startswith("hint:")}

    # Port matching: check if caller's URL host or base has a port that matches provider's listening ports
    call_ports = set()
    for v in variants:
        if v.get("host"):
            pm = re.search(r":(\d{2,5})$", v["host"])
            if pm:
                call_ports.add(pm.group(1))
    if call.get("base"):
        bpm = re.search(r":(\d{2,5})(?:/|$)", call["base"])
        if bpm:
            call_ports.add(bpm.group(1))
    matched_ports = call_ports & provider.ports
    if matched_ports:
        score += 0.35
        evidence.append(f"port {', '.join(sorted(matched_ports))} matches configured server port of {provider.name}")

    target = ident(call.get("target", ""))
    if target and target in provider.identity:
        score += 0.35
        evidence.append(f"client / target name '{call['target']}' is an application / artifact name of {provider.name}")
    elif tokens(call.get("target", "")) & provider.cores:
        score += 0.30
        evidence.append(f"call target '{call['target']}' matches {provider.name}")

    host_score = 0.0
    for host in hosts:
        clean_host = host.split(":")[0]
        labels = {ident(part) for part in re.split(r"[.]", clean_host)}
        if labels & provider.identity:
            host_score = max(host_score, 0.3)
            evidence.append(f"host {host} names {provider.name}")
        elif tokens(clean_host) & provider.cores:
            host_score = max(host_score, 0.15)
            evidence.append(f"host {host} shares the name '{', '.join(sorted(tokens(clean_host) & provider.cores))}' with {provider.name}")
    score += host_score
    if not host_score:
        for host in hint_hosts:
            clean_host = host.split(":")[0]
            if {ident(part) for part in clean_host.split(".")} & provider.identity or tokens(clean_host) & provider.cores:
                score += 0.1
                evidence.append(f"repository base URL host {host} resembles {provider.name}")
                break

    # Target / service name clues from client, source_name, via, and config keys
    key_names = re.findall(r"\$\{([^}:]+)", call.get("base", "") + call.get("url", ""))
    words = set()
    for key in key_names:
        words |= tokens(key.replace("base-url", "").replace("url", ""))
    stop = {"client", "service", "api", "controller", "impl", "util", "helper", "base", "url", "http", "rest", "config", "fetch", "get", "post", "delete", "patch", "put", "shared", "app"}
    caller_text = f"{call.get('client', '')} {call.get('source_name', '')} {call.get('via', '')} {call.get('base', '')}"
    words |= {w for w in tokens(caller_text) if len(w) > 2 and w not in stop}
    common = words & provider.cores
    if common and score < 0.25:
        score += 0.20
        evidence.append(f"client / configuration name shares '{', '.join(sorted(common))}' with {provider.name}")
    return min(score, 0.7), evidence


def match_http(call: dict, caller: Member, providers: list[Member]) -> list[dict]:
    variants = _call_variants(call, caller)
    candidates = []
    for provider in providers:
        if provider.repo_id == caller.repo_id:
            continue
        id_score, id_evidence = _identity_evidence(call, variants, provider)
        best: dict | None = None
        ties: list[dict] = []
        for ep in endpoints(provider):
            if call["method"] and ep["method"] not in ("", "ANY", "*", "REQUEST") and call["method"] != ep["method"]:
                continue
            for v in variants:
                structural, why = 0.0, ""
                ok, exact = _seg_match(v["segments"], ep["full_segments"])
                if ok and ep["has_context"]:
                    structural, why = 0.6, f"path {norm_path('/'.join(v['segments']))} = {ep['item']['name']} including context path"
                else:
                    ok2, exact2 = _seg_match(v["segments"], ep["segments"])
                    if ok2:
                        structural, why, exact = 0.5, f"path matches {ep['item']['name']}", exact2
                    elif _suffix(v["segments"], ep["full_segments"]) or _suffix(v["segments"], ep["segments"]):
                        structural, why, exact = 0.35, f"path ends with {ep['item']['name']} (prefix {'/'.join(v['segments'][: len(v['segments']) - len(ep['segments'])])} looks like a gateway / base path)", False
                    elif _prefix_stripped_match(v["segments"], ep["segments"]) or _prefix_stripped_match(v["segments"], ep["full_segments"]):
                        structural, why, exact = 0.45, f"path matches {ep['item']['name']} (under backend API prefix)", True
                    elif any(_seg_match(v["segments"], _unversioned(segs))[0] and _unversioned(segs) != segs for segs in (ep["full_segments"], ep["segments"])):
                        structural, why, exact = 0.45, f"path matches {ep['item']['name']} when its version segment is left out (the call does not name a version)", True
                if not structural:
                    continue
                if not exact:
                    structural -= 0.03
                if len(ep["segments"]) <= 1:
                    structural -= 0.15
                if v["source"].startswith("hint:"):
                    structural -= 0.05
                    why += f" (with base URL {v['raw']} from {v['source'][5:]})"
                elif v["source"] == "base":
                    why += f" (base URL {call['base']})"
                context_bonus = 0.2 if ok and ep["has_context"] else 0.0
                raw = structural + id_score + context_bonus
                fraction = float(call.get("fraction", 1.0))
                if fraction < 1.0:
                    raw *= 0.75 + 0.25 * fraction  # part of the URL is only known at run time
                if not call["method"] and (ep["method"] not in ("", "ANY", "*", "REQUEST")):
                    raw -= 0.05  # the call's HTTP method is not known
                total = round(min(0.99, raw), 3)
                evidence = [f"method {call['method'] or '(any)'} · {why}"] + (["context path of the provider is part of the URL"] if context_bonus else []) + id_evidence
                if call.get("unresolved"):
                    evidence.append(f"could not resolve statically: {', '.join(call['unresolved'][:4])} (confidence lowered)")
                if call.get("condition"):
                    evidence.append(f"only when {call['condition']}")
                if call.get("note"):
                    evidence.append(call["note"])
                cand = {"provider": provider, "endpoint": ep["item"], "score": total, "raw": round(raw, 3), "evidence": evidence, "profile": v["profile"]}
                if best is None or raw > best["raw"] + 0.001:
                    best = cand
                    ties = []
                elif abs(raw - best["raw"]) <= 0.001 and ep["item"]["item_key"] != best["endpoint"]["item_key"]:
                    ties.append(cand)
        if best and best["score"] >= 0.3:
            versions = {_version(t["endpoint"]) for t in [best] + ties}
            if ties and len(versions) > 1:
                note = "the call does not say which version: also matches " + ", ".join(f"{t['endpoint']['name']} ({_version(t['endpoint']) or 'unversioned'})" for t in ties[:3])
                best["evidence"].append(note)
                best["tie"] = True
                candidates.append(best)
                for t in ties[:3]:
                    t["evidence"].append(note)
                    t["tie"] = True
                    candidates.append(t)
            else:
                candidates.append(best)
    candidates.sort(key=lambda c: -c["raw"])
    return candidates


def level(score: float) -> str:
    return "high" if score >= 0.75 else "medium" if score >= 0.5 else "low"


def _accepts(policy: str, confidence: str) -> bool:
    return confidence == "high" or (policy == "high_medium" and confidence == "medium")


def _external_host(call: dict, caller: Member) -> str:
    for v in _call_variants(call, caller):
        if v["host"] and not v["source"].startswith("hint:"):
            return v["host"]
    return ""


# --- computing a landscape ----------------------------------------------------------------------------------------


def _row(landscape_id: str, group_key: str, kind: str, source: Member, src: dict, **kw) -> dict:
    return {
        "link_id": str(uuid.uuid4()), "landscape_id": landscape_id, "group_key": group_key[:1500], "kind": kind, "direction": kw.pop("direction", "out"),
        "source_repo_id": source.repo_id, "source_analysis_id": source.analysis_id, "source_key": src["key"][:1000], "source_name": src["name"][:1000],
        "source_kind": src.get("kind", "")[:40], "source_path": src.get("path", "")[:1000], "source_line": int(src.get("line") or 0),
        "target_repo_id": "", "target_analysis_id": "", "target_key": "", "target_name": "", "target_kind": "", "target_path": "", "target_line": 0,
        "external_name": "", "external_type": "", "label": "", "score": 0.0, "confidence": "", "ambiguous": False, "status": "auto",
        "features_json": "[]", "evidence_json": "[]", "explanation": "", "created_at": db.now(), **kw,
    }


def _target(member: Member, item: dict) -> dict:
    return {"target_repo_id": member.repo_id, "target_analysis_id": member.analysis_id, "target_key": item["item_key"][:1000], "target_name": item["name"][:1000],
            "target_kind": item["kind"], "target_path": item["path"][:1000], "target_line": int(item.get("line_start") or 0)}


def compute_links(landscape: dict, members: list[Member]) -> list[dict]:
    lid, policy = landscape["landscape_id"], landscape.get("auto_accept") or "high_medium"
    rows: list[dict] = []
    resolved_hosts: dict[str, set[str]] = {}

    def group(kind: str, caller: Member, src: dict, candidates: list[dict], fallback_external: tuple[str, str] | None, features: list[str], label: str) -> None:
        group_key = f"{kind}|{caller.repo_id}|{src['key']}"
        top = candidates[0].get("raw", candidates[0]["score"]) if candidates else 0
        ambiguous = len(candidates) > 1 and candidates[1].get("raw", candidates[1]["score"]) >= top - 0.1 and not candidates[0].get("fanout") \
            and (candidates[1]["provider"].repo_id != candidates[0]["provider"].repo_id or bool(candidates[0].get("tie") and candidates[1].get("tie")))
        for index, cand in enumerate(candidates):
            confidence = level(cand["score"])
            if cand.get("fanout"):
                status = "auto" if _accepts(policy, confidence) else "review"
            elif ambiguous:
                status = "review"
            elif index == 0:
                status = "auto" if _accepts(policy, confidence) else "review"
            else:
                status = "candidate"
            feats = sorted(set(features + cand["provider"].features.get(cand["item"]["item_key"], [])))
            rows.append(_row(lid, group_key, kind, caller, src, **_target(cand["provider"], cand["item"]), label=label[:1000], score=cand["score"],
                             confidence=confidence, ambiguous=ambiguous, status=status, features_json=json.dumps(feats), evidence_json=json.dumps(cand["evidence"][:12])))
        if not candidates and fallback_external:
            name, etype = fallback_external
            target_name = f"Topic {label.split(' ', 1)[-1]}" if kind == "messaging" else ""
            rows.append(_row(lid, group_key, kind, caller, src, external_name=name[:500], external_type=etype, label=label[:1000],
                             target_name=target_name,
                             score=0.0, confidence="", status="external" if name and not name.startswith("Unresolved") else "unresolved",
                             features_json=json.dumps(features), evidence_json=json.dumps([f"no repository in the landscape provides {label}"])))

    # HTTP
    for caller in members:
        for call in http_calls(caller):
            raw = []
            for cand in match_http(call, caller, members):
                raw.append({"provider": cand["provider"], "item": cand["endpoint"], "score": cand["score"], "raw": cand["raw"], "evidence": cand["evidence"], "tie": cand.get("tie", False)})
            label = f"{call['method']} {call['url'] or call['base']}".strip()
            host = _external_host(call, caller)
            if raw:
                resolved_hosts.setdefault(caller.repo_id, set()).add(host)
            src = {"key": call["source_key"], "name": call["source_name"], "kind": call["source_kind"], "path": call["path"], "line": call["line"]}
            ext = (host, "external api") if host else (f"Unresolved: {label}", "unknown")
            feats = list(call["features"])
            for screen in call["screens"]:
                feats += caller.features.get(f"ui:{screen}", [])
            group("http", caller, src, raw, ext, feats, label + (f" · from {', '.join(call['screens'][:3])}" if call["screens"] else ""))

    # messaging
    published: dict[str, list[tuple[Member, dict]]] = {}
    consumed: dict[str, list[tuple[Member, dict]]] = {}
    for member in members:
        pubs, cons = topics(member)
        for p in pubs:
            published.setdefault(topic_fuzzy(p["name"]), []).append((member, p))
        for c in cons:
            consumed.setdefault(topic_fuzzy(c["name"]), []).append((member, c))
    for fuzzy, pubs in published.items():
        for member, pub in pubs:
            candidates = []
            for cmember, con in consumed.get(fuzzy, []):
                if cmember.repo_id == member.repo_id:
                    continue
                exact = topic_norm(con["name"]) == topic_norm(pub["name"])
                same_transport = con["transport"] == pub["transport"]
                score = (0.85 if exact else 0.6) + (0.1 if same_transport else -0.1)
                evidence = [f"{pub['transport']} topic '{pub['name']}' is published by {pub['item']['name']}",
                            f"{con['item']['name']} in {cmember.name} listens to '{con['name']}'" + ("" if exact else " (name differs only in separators / case)")]
                if not same_transport:
                    evidence.append(f"transports differ: {pub['transport']} vs {con['transport']}")
                candidates.append({"provider": cmember, "item": con["item"], "score": round(score, 3), "evidence": evidence, "fanout": exact})
            candidates.sort(key=lambda c: -c["score"])
            own_consumer = any(m.repo_id == member.repo_id for m, _ in consumed.get(fuzzy, []))
            src = {"key": f"{pub['item']['item_key']}#topic:{pub['name']}", "name": f"{pub['item']['name']} → {pub['transport']} {pub['name']}", "kind": pub["item"]["kind"],
                   "path": pub["item"]["path"], "line": pub["item"]["line_start"]}
            broker_name, broker_type = _broker_info(pub["transport"])
            ext = None if (own_consumer and pub["transport"] in ("event", "direct", "vm", "seda")) else (broker_name, broker_type)
            group("messaging", member, src, candidates, ext, member.features.get(pub["item"]["item_key"], []), f"{pub['transport']} {pub['name']}")
    for fuzzy, cons in consumed.items():
        if fuzzy in published:
            continue
        for member, con in cons:
            broker_name, broker_type = _broker_info(con["transport"])
            src = {"key": f"{con['item']['item_key']}#consumes:{con['name']}", "name": f"{con['item']['name']} ← {con['transport']} {con['name']}", "kind": con["item"]["kind"],
                   "path": con["item"]["path"], "line": con["item"]["line_start"]}
            rows.append(_row(lid, f"messaging|{member.repo_id}|{src['key']}", "messaging", member, src, direction="in",
                             external_name=broker_name, external_type=broker_type, label=f"{con['transport']} {con['name']}",
                             target_name=f"Topic {con['name']}",
                             status="external", features_json=json.dumps(member.features.get(con["item"]["item_key"], [])),
                             evidence_json=json.dumps([f"{con['item']['name']} consumes '{con['name']}'; no repository in the landscape publishes it"])))

    # shared database tables
    by_table: dict[str, list[tuple[Member, dict]]] = {}
    for member in members:
        for table in tables(member):
            by_table.setdefault(table["name"], []).append((member, table))
    for name, users in by_table.items():
        repos = {m.repo_id for m, _ in users}
        if len(repos) < 2:
            continue
        users.sort(key=lambda mt: (mt[0].name, mt[1]["item"]["item_key"]))
        for i, (member, table) in enumerate(users):
            candidates = []
            for other, otable in users[i + 1:]:
                if other.repo_id == member.repo_id:
                    continue
                shared_ds = member.datasources & other.datasources
                shared_engine = table["engines"] & otable["engines"]
                ddl_only = table["ops"] == ["DDL"] and otable["ops"] == ["DDL"]
                score = 0.45 + (0.35 if shared_ds else 0) + (0.1 if shared_engine else 0) - (0.1 if ddl_only else 0)
                evidence = [f"both use table {name.upper()}: {member.name} ({', '.join(table['ops'])}) and {other.name} ({', '.join(otable['ops'])})"]
                if shared_ds:
                    evidence.append(f"same datasource {', '.join(sorted(shared_ds))}")
                elif member.datasources and other.datasources:
                    evidence.append(f"different datasources: {', '.join(sorted(member.datasources))} vs {', '.join(sorted(other.datasources))} — may be separate databases")
                else:
                    evidence.append("datasource unknown on one side — the same table name could be a different database")
                if shared_engine:
                    evidence.append(f"same engine: {', '.join(sorted(shared_engine))}")
                candidates.append({"provider": other, "item": otable["item"], "score": round(min(score, 0.95), 3), "evidence": evidence, "fanout": True})
            if candidates:
                src = {"key": table["item"]["item_key"], "name": f"{table['item']['name']} ({', '.join(table['ops'])})", "kind": "table", "path": table["item"]["path"],
                       "line": table["item"]["line_start"]}
                group("database", member, src, candidates, None, [], f"shared table {name.upper()}")

    # external systems detected in each repository
    linked_external_brokers = {(r["source_repo_id"], r["external_name"]) for r in rows if r["status"] == "external" and r["external_name"]}
    for member in members:
        for system in member.externals:
            if not isinstance(system, dict) or not system.get("name"):
                continue
            stype = str(system.get("type") or "external")
            if stype == "cloud" and str(system["name"]).lower().startswith("spring"):
                continue  # a framework, not a system the repository talks to
            if stype == "external api" and system["name"] in resolved_hosts.get(member.repo_id, set()):
                continue
            sname = str(system["name"])
            if stype in ("messaging", "kafka", "rabbitmq", "activemq", "ibm-mq", "jms", "sqs"):
                bname, btype = _broker_info(stype, sname)
                sname = bname
                stype = btype
            if (member.repo_id, sname) in linked_external_brokers:
                continue
            src = {"key": f"repo:{member.repo_id}#external:{sname}", "name": member.name, "kind": "repository", "path": "", "line": 0}
            rows.append(_row(lid, f"external|{member.repo_id}|{sname}", "external", member, src, external_name=sname[:500], external_type=stype[:40],
                             label=f"{stype}: {sname}", status="external", evidence_json=json.dumps([str(system.get("evidence") or "")])))
    return rows


def _apply_decisions(landscape_id: str, rows: list[dict]) -> None:
    decisions = {d["group_key"]: _loads(d["choice_json"], {}) for d in db.fetchall("SELECT group_key, choice_json FROM landscape_decisions WHERE landscape_id = ?", [landscape_id])}
    if not decisions:
        return
    for row in rows:
        choice = decisions.get(row["group_key"])
        if not choice:
            continue
        if row["target_repo_id"]:
            chosen = f"{row['target_repo_id']}|{row['target_key']}" in set(choice.get("targets") or [])
            row["status"] = "confirmed" if chosen and not choice.get("reject") else "rejected"
        elif row["status"] in ("external", "unresolved"):
            if choice.get("external"):
                row["status"] = "external"
                if choice.get("external_name"):
                    row["external_name"] = str(choice["external_name"])[:500]
            elif choice.get("reject"):
                row["status"] = "rejected"
    # a group decided as external that has internal candidates gets an explicit external row
    for group_key, choice in decisions.items():
        if not choice.get("external"):
            continue
        group_rows = [r for r in rows if r["group_key"] == group_key]
        if group_rows and not any(r["status"] == "external" for r in group_rows):
            base = group_rows[0]
            rows.append({**base, "link_id": str(uuid.uuid4()), "target_repo_id": "", "target_analysis_id": "", "target_key": "", "target_name": "", "target_kind": "",
                         "target_path": "", "target_line": 0, "external_name": str(choice.get("external_name") or "External system")[:500], "external_type": "external api",
                         "status": "external", "score": 0.0, "confidence": "", "evidence_json": json.dumps(["marked external by a reviewer"])})


def recompute(landscape_id: str) -> dict:
    landscape = _landscape_row(landscape_id)
    members_rows = db.fetchall("SELECT * FROM landscape_repos WHERE landscape_id = ?", [landscape_id])
    members, missing = [], []
    for m in members_rows:
        repo = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [m["repo_id"]])
        if repo is None:
            continue
        member = load_member(repo, m.get("analysis_id") or "")
        if member is None:
            missing.append(repo["name"])
        else:
            members.append(member)
    old = {(r["group_key"], r["target_repo_id"], r["target_key"]): r["explanation"] for r in db.fetchall(
        "SELECT group_key, target_repo_id, target_key, explanation FROM landscape_links WHERE landscape_id = ? AND explanation <> ''", [landscape_id])}
    rows = compute_links(landscape, members)
    _apply_decisions(landscape_id, rows)
    for row in rows:
        row["explanation"] = old.get((row["group_key"], row["target_repo_id"], row["target_key"]), "")
    db.exec_sql("DELETE FROM landscape_links WHERE landscape_id = ?", [landscape_id])
    for row in rows:
        db.insert("landscape_links", row)
    stats = _stats(rows)
    stats["members"] = [{"repo_id": m.repo_id, "name": m.name, "analysis_id": m.analysis_id, "branch": m.branch} for m in members]
    stats["missing"] = missing
    message = f"{len(members)} repositor{'y' if len(members) == 1 else 'ies'} linked" + (f"; not analysed yet: {', '.join(missing)}" if missing else "")
    db.exec_sql("UPDATE landscapes SET status = 'ready', message = ?, stats_json = ?, computed_at = ? WHERE landscape_id = ?",
                [message, json.dumps(stats), db.now(), landscape_id])
    return stats


def _stats(rows: list[dict]) -> dict:
    groups_review = {r["group_key"] for r in rows if r["status"] == "review"}
    by_kind: dict[str, int] = {}
    for r in rows:
        if r["status"] in ACTIVE + ("external",):
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    return {"links": sum(1 for r in rows if r["status"] in ACTIVE and r["target_repo_id"]), "external": sum(1 for r in rows if r["status"] == "external"),
            "review": len(groups_review), "unresolved": sum(1 for r in rows if r["status"] == "unresolved"), "by_kind": by_kind}


def on_analysis_completed(repo_id: str, analysis_id: str) -> list[str]:
    """Recomputes every landscape the repository belongs to (unless it pins another analysis). Never raises."""
    done = []
    for row in db.fetchall("SELECT landscape_id, analysis_id FROM landscape_repos WHERE repo_id = ?", [repo_id]):
        if row["analysis_id"] and row["analysis_id"] != analysis_id:
            continue
        try:
            recompute(row["landscape_id"])
            done.append(row["landscape_id"])
        except Exception:  # noqa: BLE001
            logger.exception("Landscape %s recompute failed", row["landscape_id"])
    return done


def on_analysis_removed(repo_id: str, analysis_id: str) -> None:
    """A deleted analysis: unpin it and recompute the landscapes that used it (they fall back to the next latest)."""
    db.exec_sql("UPDATE landscape_repos SET analysis_id = '' WHERE repo_id = ? AND analysis_id = ?", [repo_id, analysis_id])
    used = {r["landscape_id"] for r in db.fetchall(
        "SELECT DISTINCT landscape_id FROM landscape_links WHERE source_analysis_id = ? OR target_analysis_id = ?", [analysis_id, analysis_id])}
    for landscape_id in used:
        try:
            recompute(landscape_id)
        except Exception:  # noqa: BLE001
            logger.exception("Landscape %s recompute failed", landscape_id)


def delete_for_repo(repo_id: str) -> None:
    landscapes = [r["landscape_id"] for r in db.fetchall("SELECT landscape_id FROM landscape_repos WHERE repo_id = ?", [repo_id])]
    db.exec_sql("DELETE FROM landscape_repos WHERE repo_id = ?", [repo_id])
    for landscape_id in landscapes:
        try:
            recompute(landscape_id)
        except Exception:  # noqa: BLE001
            logger.exception("Landscape %s recompute failed", landscape_id)


# --- CRUD -----------------------------------------------------------------------------------------------------------


def _landscape_row(landscape_id: str) -> dict:
    row = db.fetchone("SELECT * FROM landscapes WHERE landscape_id = ?", [landscape_id])
    if row is None:
        raise AppError(404, "Landscape not found")
    return row


def _payload(row: dict) -> dict:
    repos = db.fetchall(
        """
        SELECT lr.repo_id, lr.analysis_id AS pinned_analysis_id, r.name, r.source_type, r.default_branch FROM landscape_repos lr
        JOIN repositories r ON r.repo_id = lr.repo_id WHERE lr.landscape_id = ? ORDER BY r.name
        """,
        [row["landscape_id"]],
    )
    creator = db.fetchone("SELECT username FROM users WHERE user_id = ?", [row["created_by"]])
    return {**{k: row[k] for k in ("landscape_id", "name", "description", "auto_accept", "status", "message", "created_by", "created_at", "updated_at", "computed_at")},
            "created_by_name": creator["username"] if creator else row["created_by"], "stats": _loads(row["stats_json"], {}), "repos": repos}


def list_landscapes(user: dict) -> list[dict]:
    require(user, "insights", "read")
    return [_payload(r) for r in db.fetchall("SELECT * FROM landscapes ORDER BY name")]


def get_landscape(user: dict, landscape_id: str) -> dict:
    require(user, "insights", "read")
    return _payload(_landscape_row(landscape_id))


def _set_repos(landscape_id: str, repo_ids: list, pins: dict | None = None) -> None:
    clean = []
    for repo_id in repo_ids:
        repo_id = str(repo_id)
        if repo_id in clean:
            continue
        if not db.fetchone("SELECT repo_id FROM repositories WHERE repo_id = ?", [repo_id]):
            raise AppError(400, f"Unknown repository {repo_id}")
        clean.append(repo_id)
    if len(clean) < 2:
        raise AppError(400, "Pick at least two repositories to link")
    if len(clean) > 200:
        raise AppError(400, "A landscape can hold at most 200 repositories")
    db.exec_sql("DELETE FROM landscape_repos WHERE landscape_id = ?", [landscape_id])
    for repo_id in clean:
        db.insert("landscape_repos", {"landscape_id": landscape_id, "repo_id": repo_id, "analysis_id": str((pins or {}).get(repo_id) or ""), "added_at": db.now()})


def _policy(value) -> str:
    value = str(value or "high_medium")
    if value not in POLICIES:
        raise AppError(400, "auto_accept must be 'high' or 'high_medium'")
    return value


def create_landscape(user: dict, body: dict) -> dict:
    require(user, "insights", "write")
    name = str(body.get("name") or "").strip()[:300]
    if not name:
        raise AppError(400, "Give the landscape a name")
    landscape_id = str(uuid.uuid4())
    now = db.now()
    db.insert("landscapes", {"landscape_id": landscape_id, "name": name, "description": str(body.get("description") or "")[:4000],
                             "auto_accept": _policy(body.get("auto_accept")), "status": "new", "message": "", "stats_json": "{}", "created_by": user["user_id"],
                             "created_at": now, "updated_at": now})
    try:
        _set_repos(landscape_id, list(body.get("repo_ids") or []), body.get("pins"))
    except AppError:
        db.exec_sql("DELETE FROM landscapes WHERE landscape_id = ?", [landscape_id])
        raise
    recompute(landscape_id)
    return get_landscape(user, landscape_id)


def update_landscape(user: dict, landscape_id: str, body: dict) -> dict:
    require(user, "insights", "write")
    row = _landscape_row(landscape_id)
    name = str(body.get("name", row["name"]) or "").strip()[:300] or row["name"]
    db.exec_sql("UPDATE landscapes SET name = ?, description = ?, auto_accept = ?, updated_at = ? WHERE landscape_id = ?",
                [name, str(body.get("description", row["description"]) or "")[:4000], _policy(body.get("auto_accept", row["auto_accept"])), db.now(), landscape_id])
    if "repo_ids" in body:
        _set_repos(landscape_id, list(body.get("repo_ids") or []), body.get("pins"))
    recompute(landscape_id)
    return get_landscape(user, landscape_id)


def delete_landscape(user: dict, landscape_id: str) -> None:
    require(user, "insights", "write")
    _landscape_row(landscape_id)
    for table in ("landscape_links", "landscape_decisions", "landscape_repos", "landscapes"):
        db.exec_sql(f"DELETE FROM {table} WHERE landscape_id = ?", [landscape_id])


def recompute_landscape(user: dict, landscape_id: str) -> dict:
    require(user, "insights", "write")
    _landscape_row(landscape_id)
    recompute(landscape_id)
    return get_landscape(user, landscape_id)


# --- links, review and decisions -------------------------------------------------------------------------------------


def _repo_names(landscape_id: str) -> dict[str, str]:
    return {r["repo_id"]: r["name"] for r in db.fetchall(
        "SELECT r.repo_id, r.name FROM landscape_repos lr JOIN repositories r ON r.repo_id = lr.repo_id WHERE lr.landscape_id = ?", [landscape_id])}


def _link_payload(row: dict, names: dict[str, str]) -> dict:
    out = {k: v for k, v in row.items() if not k.endswith("_json")}
    out["features"] = _loads(row.get("features_json"), [])
    out["evidence"] = _loads(row.get("evidence_json"), [])
    out["source_repo_name"] = names.get(row["source_repo_id"], row["source_repo_id"])
    out["target_repo_name"] = names.get(row["target_repo_id"], "") if row["target_repo_id"] else ""
    return out


def links(user: dict, landscape_id: str, kind: str = "", status: str = "", repo_id: str = "", feature: str = "", q: str = "", limit: int = 2000) -> dict:
    require(user, "insights", "read")
    _landscape_row(landscape_id)
    where, params = ["landscape_id = ?"], [landscape_id]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if status == "active":
        where.append("status IN ('auto', 'confirmed', 'external')")
    elif status:
        where.append("status = ?")
        params.append(status)
    if repo_id:
        where.append("(source_repo_id = ? OR target_repo_id = ?)")
        params += [repo_id, repo_id]
    if q.strip():
        where.append("(lower(source_name) LIKE ? OR lower(target_name) LIKE ? OR lower(label) LIKE ? OR lower(external_name) LIKE ?)")
        params += [f"%{q.strip().lower()}%"] * 4
    rows = db.fetchall(f"SELECT * FROM landscape_links WHERE {' AND '.join(where)} ORDER BY kind, source_name, score DESC LIMIT {max(1, min(int(limit), 10000))}", params)
    names = _repo_names(landscape_id)
    items = [_link_payload(r, names) for r in rows]
    if feature:
        items = [i for i in items if feature in i["features"]]
    return {"items": items, "total": len(items)}


def review(user: dict, landscape_id: str) -> dict:
    """Groups waiting for a decision, with all their candidates, plus groups already decided (for undo)."""
    require(user, "insights", "read")
    _landscape_row(landscape_id)
    names = _repo_names(landscape_id)
    rows = db.fetchall("SELECT * FROM landscape_links WHERE landscape_id = ? AND kind <> 'external' ORDER BY group_key, score DESC", [landscape_id])
    decisions = {d["group_key"]: d for d in db.fetchall("SELECT * FROM landscape_decisions WHERE landscape_id = ?", [landscape_id])}
    groups: dict[str, dict] = {}
    for row in rows:
        g = groups.setdefault(row["group_key"], {"group_key": row["group_key"], "kind": row["kind"], "source_repo_id": row["source_repo_id"],
                                                 "source_repo_name": names.get(row["source_repo_id"], ""), "source_name": row["source_name"], "source_kind": row["source_kind"],
                                                 "source_path": row["source_path"], "source_line": row["source_line"], "label": row["label"], "candidates": [], "features": set(),
                                                 "ambiguous": False, "pending": False, "unresolved": False})
        g["ambiguous"] = g["ambiguous"] or bool(row["ambiguous"])
        g["pending"] = g["pending"] or row["status"] == "review"
        g["unresolved"] = g["unresolved"] or row["status"] == "unresolved"
        g["features"] |= set(_loads(row["features_json"], []))
        if row["target_repo_id"] or row["status"] in ("external", "unresolved"):
            g["candidates"].append(_link_payload(row, names))
    out_pending, out_decided, out_unresolved = [], [], []
    for g in groups.values():
        g["features"] = sorted(g["features"])
        decision = decisions.get(g["group_key"])
        if decision:
            g["decision"] = {**_loads(decision["choice_json"], {}), "note": decision["note"], "decided_at": decision["decided_at"], "decided_by": decision["decided_by"]}
            out_decided.append(g)
        elif g["pending"]:
            g["top_score"] = max((c["score"] for c in g["candidates"]), default=0)
            out_pending.append(g)
        elif g["unresolved"]:
            out_unresolved.append(g)
    out_pending.sort(key=lambda g: (not g["ambiguous"], -g["top_score"], g["source_repo_name"], g["source_name"]))
    return {"pending": out_pending, "decided": out_decided, "unresolved": out_unresolved[:500]}


def decide(user: dict, landscape_id: str, body: dict) -> dict:
    """{group_key, targets: ["repo_id|target_key"], external: bool, external_name, reject: bool, reset: bool, note}"""
    require(user, "insights", "write")
    _landscape_row(landscape_id)
    keys = body.get("group_keys") or ([body.get("group_key")] if body.get("group_key") else [])
    if not keys:
        raise AppError(400, "group_key is required")
    action = str(body.get("action") or "")
    for group_key in keys:
        group_key = str(group_key)
        rows = db.fetchall("SELECT * FROM landscape_links WHERE landscape_id = ? AND group_key = ? ORDER BY score DESC", [landscape_id, group_key])
        if not rows:
            raise AppError(404, f"Unknown link group {group_key}")
        db.exec_sql("DELETE FROM landscape_decisions WHERE landscape_id = ? AND group_key = ?", [landscape_id, group_key])
        if body.get("reset") or action == "reset":
            continue
        if action == "accept_top":
            internal = [r for r in rows if r["target_repo_id"]]
            choice = {"targets": [f"{internal[0]['target_repo_id']}|{internal[0]['target_key']}"]} if internal else {"external": True}
        elif action == "reject":
            choice = {"reject": True}
        else:
            targets = [str(t) for t in body.get("targets") or []]
            valid = {f"{r['target_repo_id']}|{r['target_key']}" for r in rows if r["target_repo_id"]}
            unknown = [t for t in targets if t not in valid]
            if unknown:
                raise AppError(400, f"Not a candidate of this link: {unknown[0]}")
            choice = {"targets": targets, "external": bool(body.get("external")), "external_name": str(body.get("external_name") or "")[:300], "reject": bool(body.get("reject"))}
            if not targets and not choice["external"]:
                choice["reject"] = True
        db.insert("landscape_decisions", {"landscape_id": landscape_id, "group_key": group_key, "choice_json": json.dumps(choice), "note": str(body.get("note") or "")[:2000],
                                          "decided_by": user["user_id"], "decided_at": db.now()})
    recompute(landscape_id)
    return review(user, landscape_id)


def explain(user: dict, landscape_id: str, group_key: str) -> dict:
    """LLM explanation of one link group (why which candidate is likely), cached on the links. Deterministic text without an LLM."""
    from app.services import llm

    require(user, "insights", "read")
    _landscape_row(landscape_id)
    names = _repo_names(landscape_id)
    rows = db.fetchall("SELECT * FROM landscape_links WHERE landscape_id = ? AND group_key = ? ORDER BY score DESC", [landscape_id, group_key])
    if not rows:
        raise AppError(404, "Unknown link group")
    if rows[0]["explanation"]:
        return {"group_key": group_key, "explanation": rows[0]["explanation"], "source": "cache"}
    caller = rows[0]
    facts = {
        "caller": {"repository": names.get(caller["source_repo_id"]), "item": caller["source_name"], "kind": caller["source_kind"], "file": f"{caller['source_path']}:{caller['source_line']}",
                   "interface": caller["label"]},
        "candidates": [{"repository": names.get(r["target_repo_id"], r["external_name"] or "external"), "target": r["target_name"] or r["external_name"], "score": r["score"],
                        "confidence": r["confidence"], "evidence": _loads(r["evidence_json"], [])} for r in rows],
        "link_type": caller["kind"],
    }
    text = _deterministic_explanation(facts)
    source = "rules"
    if llm.configured():
        try:
            raw = llm.complete_json(
                "You are a software architect linking interfaces across repositories. Use only the facts given. Be concise and concrete.",
                "FACTS:\n" + json.dumps(facts, default=str)[:12000] +
                '\n\nExplain which candidate this caller most likely connects to and why, and what would settle any doubt. Return JSON: '
                '{"recommendation": "repository name or external", "explanation": "3-5 sentences in plain language", "check": "one thing a reviewer can verify"}',
                0.1,
            )
            if raw.get("explanation"):
                text = f"**Likely:** {raw.get('recommendation', '')}\n\n{raw['explanation']}" + (f"\n\n**Verify:** {raw['check']}" if raw.get("check") else "")
                source = "llm"
        except Exception:  # noqa: BLE001
            logger.exception("Landscape explanation failed")
    if source == "llm":  # rule-based text is cheap to rebuild; only LLM answers are cached
        db.exec_sql("UPDATE landscape_links SET explanation = ? WHERE landscape_id = ? AND group_key = ?", [text[:6000], landscape_id, group_key])
    return {"group_key": group_key, "explanation": text, "source": source}


def _deterministic_explanation(facts: dict) -> str:
    cands = facts["candidates"]
    if not cands:
        return "No candidate."
    lines = [f"{facts['caller']['item']} in **{facts['caller']['repository']}** calls `{facts['caller']['interface']}`."]
    top = cands[0]
    if len(cands) > 1 and cands[1]["score"] >= top["score"] - 0.1:
        lines.append(f"**{top['repository']}** ({top['score']}) and **{cands[1]['repository']}** ({cands[1]['score']}) match almost equally well, so the link needs a decision.")
    else:
        lines.append(f"Best match: **{top['repository']}** → {top['target']} (score {top['score']}, {top['confidence']}).")
    for ev in top["evidence"][:5]:
        lines.append(f"- {ev}")
    lines.append("")
    lines.append("Check the base URL / service name configured for this client in each environment to settle it.")
    return "\n".join(lines)


# --- views ---------------------------------------------------------------------------------------------------------


def system_map(user: dict, landscape_id: str, kinds: str = "", feature: str = "", include_review: bool = True) -> dict:
    """Nodes (repositories and external systems) and edges aggregated per (source, target, kind)."""
    require(user, "insights", "read")
    landscape = get_landscape(user, landscape_id)
    names = _repo_names(landscape_id)
    wanted = {k for k in kinds.split(",") if k} or set(KINDS)
    statuses = set(ACTIVE) | {"external"} | ({"review"} if include_review else set())
    rows = db.fetchall("SELECT * FROM landscape_links WHERE landscape_id = ?", [landscape_id])
    nodes: dict[str, dict] = {r["repo_id"]: {"id": r["repo_id"], "label": r["name"], "type": "repository", "source_type": r["source_type"]} for r in landscape["repos"]}
    edges: dict[tuple, dict] = {}
    features: set[str] = set()
    for row in rows:
        feats = _loads(row["features_json"], [])
        features |= set(feats)
        if row["kind"] not in wanted or row["status"] not in statuses or (feature and feature not in feats):
            continue
        if row["target_repo_id"]:
            target = row["target_repo_id"]
        else:
            target = f"ext:{row['external_type']}:{row['external_name']}"
            nodes.setdefault(target, {"id": target, "label": row["external_name"], "type": "external", "external_type": row["external_type"]})
        source = row["source_repo_id"]
        if row["direction"] == "in":
            source, target = target, source
        key = (source, target, row["kind"])
        edge = edges.setdefault(key, {"source": source, "target": target, "kind": row["kind"], "count": 0, "review": 0, "labels": [], "features": set(), "links": []})
        if row["status"] == "review":
            if row["group_key"] in edge.setdefault("_groups", set()):
                continue
            edge["_groups"].add(row["group_key"])
            edge["review"] += 1
        else:
            edge["count"] += 1
        lbl = row["label"] or row["target_name"] or row["source_name"]
        if lbl and lbl not in edge["labels"] and len(edge["labels"]) < 20:
            edge["labels"].append(lbl)
        edge["features"] |= set(feats)
        edge["links"].append(_link_payload(row, names))
    out_edges = []
    for edge in edges.values():
        edge.pop("_groups", None)
        edge["features"] = sorted(edge["features"])
        out_edges.append(edge)
    used = {e["source"] for e in out_edges} | {e["target"] for e in out_edges}
    out_nodes = [n for n in nodes.values() if n["type"] == "repository" or n["id"] in used]
    return {"nodes": out_nodes, "edges": out_edges, "features": sorted(features), "mermaid": _map_mermaid(out_nodes, out_edges)}


def _mid(value: str) -> str:
    return "n" + re.sub(r"[^A-Za-z0-9]", "_", value)[:60] + str(abs(hash(value)) % 10000)


def _safe(text: str, limit: int = 50) -> str:
    clean = re.sub(r"\$?\{([^}]*)\}", r":\1", str(text or ""))
    clean = re.sub(r"[\"<>{}\[\]|#;`]", " ", clean).strip()
    return clean[: limit - 1] + "…" if len(clean) > limit else clean


EDGE_STYLE = {"http": "stroke:#0ea5e9", "messaging": "stroke:#a855f7", "database": "stroke:#f59e0b", "external": "stroke:#94a3b8"}


def _map_mermaid(nodes: list[dict], edges: list[dict]) -> str:
    ids = {n["id"]: _mid(n["id"]) for n in nodes}
    lines = ["flowchart LR"]
    for n in nodes:
        if n["type"] == "repository":
            lines.append(f'  {ids[n["id"]]}["{_safe(n["label"])}"]:::repo')
        else:
            shape = ("[(", ")]") if n.get("external_type") in ("database", "cache") else ("{{", "}}") if n.get("external_type") in ("kafka", "rabbitmq", "activemq", "ibm-mq", "jms", "sqs", "messaging", "stream", "event", "pubsub", "servicebus", "mqtt", "pulsar", "nats") else ("([", "])")
            lines.append(f'  {ids[n["id"]]}{shape[0]}"{_safe(n["label"])}"{shape[1]}:::ext')
    for e in edges:
        if e["source"] not in ids or e["target"] not in ids:
            continue
        arrow = "-.->" if e["review"] and not e["count"] else "-->"
        label = (f"{e['kind']} ×{e['count']}" if e["count"] else e["kind"]) + (f" · {e['review']} to review" if e["review"] else "")
        lines.append(f'  {ids[e["source"]]} {arrow}|"{_safe(label, 40)}"| {ids[e["target"]]}')
    lines += ["  classDef repo stroke-width:2px", "  classDef ext stroke-dasharray:4 3"]
    # linkStyle indexes follow the order the links were written
    written = [e for e in edges if e["source"] in ids and e["target"] in ids]
    lines += [f"  linkStyle {i} {EDGE_STYLE.get(e['kind'], '')},stroke-width:{min(1 + e['count'] // 3, 4)}px" for i, e in enumerate(written)]
    return "\n".join(lines)


def interfaces(user: dict, landscape_id: str) -> dict:
    """Provided interfaces (endpoints, topics, tables) with their consumers, and each repository's outbound dependencies."""
    require(user, "insights", "read")
    _landscape_row(landscape_id)
    names = _repo_names(landscape_id)
    rows = db.fetchall("SELECT * FROM landscape_links WHERE landscape_id = ? AND status IN ('auto', 'confirmed', 'external') ORDER BY target_name", [landscape_id])
    provided: dict[tuple, dict] = {}
    externals: dict[tuple, dict] = {}
    for row in rows:
        if row["target_repo_id"] and row["kind"] == "messaging":
            # a topic is provided by its publisher and consumed by the listeners
            key = (row["source_repo_id"], row["label"])
            entry = provided.setdefault(key, {"repo_id": row["source_repo_id"], "repo_name": names.get(row["source_repo_id"], ""), "key": row["source_key"],
                                              "name": row["label"], "kind": row["kind"], "item_kind": f"topic · published by {row['source_name'].split(' → ')[0]}",
                                              "path": row["source_path"], "line": row["source_line"], "analysis_id": row["source_analysis_id"], "consumers": [], "features": set()})
            entry["consumers"].append({"repo_id": row["target_repo_id"], "repo_name": names.get(row["target_repo_id"], ""), "name": row["target_name"],
                                       "path": row["target_path"], "line": row["target_line"], "status": row["status"], "confidence": row["confidence"]})
            entry["features"] |= set(_loads(row["features_json"], []))
        elif row["target_repo_id"]:
            key = (row["target_repo_id"], row["target_key"])
            entry = provided.setdefault(key, {"repo_id": row["target_repo_id"], "repo_name": names.get(row["target_repo_id"], ""), "key": row["target_key"],
                                              "name": row["target_name"], "kind": row["kind"], "item_kind": row["target_kind"], "path": row["target_path"],
                                              "line": row["target_line"], "analysis_id": row["target_analysis_id"], "consumers": [], "features": set()})
            entry["consumers"].append({"repo_id": row["source_repo_id"], "repo_name": names.get(row["source_repo_id"], ""), "name": row["source_name"],
                                       "path": row["source_path"], "line": row["source_line"], "status": row["status"], "confidence": row["confidence"]})
            entry["features"] |= set(_loads(row["features_json"], []))
        else:
            key = (row["external_type"], row["external_name"])
            entry = externals.setdefault(key, {"name": row["external_name"], "type": row["external_type"], "users": []})
            entry["users"].append({"repo_id": row["source_repo_id"], "repo_name": names.get(row["source_repo_id"], ""), "name": row["source_name"], "kind": row["kind"],
                                   "direction": row["direction"]})
    out = []
    for entry in provided.values():
        entry["features"] = sorted(entry["features"])
        out.append(entry)
    out.sort(key=lambda e: (e["repo_name"], e["kind"], e["name"]))
    return {"provided": out, "external": sorted(externals.values(), key=lambda e: (e["type"], e["name"]))}


def entry_points(user: dict, landscape_id: str) -> list[dict]:
    """Starting points for a journey: screens, endpoints, jobs and listeners of every member."""
    require(user, "insights", "read")
    landscape = get_landscape(user, landscape_id)
    out = []
    for repo in landscape["repos"]:
        member_row = latest_analysis(db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [repo["repo_id"]]) or {"repo_id": repo["repo_id"]})
        analysis_id = repo.get("pinned_analysis_id") or (member_row or {}).get("analysis_id")
        if not analysis_id:
            continue
        for item in db.fetchall(
            "SELECT item_id, item_key, kind, name, path FROM analysis_catalog WHERE analysis_id = ? AND kind IN ('screen', 'endpoint', 'job', 'listener') ORDER BY kind, name",
            [analysis_id],
        ):
            out.append({**item, "analysis_id": analysis_id, "repo_id": repo["repo_id"], "repo_name": repo["name"]})
    return out


def journey(user: dict, landscape_id: str, repo_id: str, key: str, depth: int = 6) -> dict:
    """End-to-end path from a screen / endpoint / job / listener: hops inside each repository (catalogue links) and
    across repositories (landscape links), as steps and a Mermaid sequence diagram."""
    require(user, "insights", "read")
    _landscape_row(landscape_id)
    names = _repo_names(landscape_id)
    if repo_id not in names:
        raise AppError(404, "The repository is not part of this landscape")
    links_rows = db.fetchall("SELECT * FROM landscape_links WHERE landscape_id = ? AND status IN ('auto', 'confirmed', 'external', 'review')", [landscape_id])
    by_source: dict[tuple[str, str], list[dict]] = {}
    for row in links_rows:
        if row["direction"] != "out":
            continue
        base = row["source_key"].split("#")[0]
        by_source.setdefault((row["source_repo_id"], base), []).append(row)
    graphs: dict[str, tuple[dict, dict]] = {}

    def graph_of(rid: str, analysis_id: str) -> tuple[dict, dict]:
        if rid not in graphs:
            items = {r["item_key"]: r for r in db.fetchall("SELECT item_id, item_key, kind, name, path, line_start, details_json FROM analysis_catalog WHERE analysis_id = ?", [analysis_id])}
            out: dict[str, list[dict]] = {}
            for link in db.fetchall("SELECT source_key, target_key, kind FROM analysis_catalog_links WHERE analysis_id = ?", [analysis_id]):
                out.setdefault(link["source_key"], []).append(link)
            graphs[rid] = (items, out)
        return graphs[rid]

    repo = db.fetchone("SELECT * FROM repositories WHERE repo_id = ?", [repo_id])
    pinned = db.fetchone("SELECT analysis_id FROM landscape_repos WHERE landscape_id = ? AND repo_id = ?", [landscape_id, repo_id])
    start_analysis = (pinned or {}).get("analysis_id") or (latest_analysis(repo) or {}).get("analysis_id")
    if not start_analysis:
        raise AppError(409, "The repository has no completed analysis")
    items, _ = graph_of(repo_id, start_analysis)
    if key not in items:
        raise AppError(404, "Starting item not found in the repository's catalogue")
    steps: list[dict] = []
    participants: list[str] = []
    seen: set[tuple[str, str]] = set()

    def participant(name: str) -> str:
        if name not in participants:
            participants.append(name)
        return name

    def walk(rid: str, analysis_id: str, start: str, level_: int) -> None:
        if level_ > depth or (rid, start) in seen:
            return
        seen.add((rid, start))
        repo_name = participant(names.get(rid, rid))
        items_, out = graph_of(rid, analysis_id)
        anchor = len(steps)
        frontier, inside, visited = [start], [], {start}
        hops = 0
        while frontier and hops < 60:
            current = frontier.pop(0)
            hops += 1
            item = items_.get(current)
            if item is None:
                continue
            if current != start:
                inside.append(item["name"])
            for row in by_source.get((rid, current), []):
                if row["status"] == "review":
                    target = participant(names.get(row["target_repo_id"], "?") if row["target_repo_id"] else row["external_name"])
                    steps.append({"from": repo_name, "to": target, "kind": row["kind"], "label": row["label"], "via": item["name"], "status": "review",
                                  "target": row["target_name"], "group_key": row["group_key"]})
                    continue
                if row["target_repo_id"]:
                    target = participant(names.get(row["target_repo_id"], row["target_repo_id"]))
                    steps.append({"from": repo_name, "to": target, "kind": row["kind"], "label": row["label"] if row["kind"] != "database" else row["label"],
                                  "via": item["name"], "status": row["status"], "target": row["target_name"], "group_key": row["group_key"]})
                    if row["kind"] in ("http", "messaging"):
                        walk(row["target_repo_id"], row["target_analysis_id"], row["target_key"], level_ + 1)
                elif row["external_name"]:
                    target = participant(row["external_name"])
                    steps.append({"from": repo_name, "to": target, "kind": row["kind"], "label": row["label"], "via": item["name"], "status": row["status"],
                                  "target": row["external_name"], "group_key": row["group_key"]})
            for link in out.get(current, []):
                if link["kind"] in ("calls", "uses", "submits", "accesses", "publishes", "delivers to", "invokes", "request") and link["target_key"] not in visited:
                    visited.add(link["target_key"])
                    target_item = items_.get(link["target_key"])
                    if target_item and target_item["kind"] == "table":
                        steps.append({"from": repo_name, "to": participant(f"DB {target_item['name']}"), "kind": "table", "label": f"{link['kind']} {target_item['name']}",
                                      "via": item["name"], "status": "internal", "target": target_item["name"], "group_key": ""})
                        continue
                    frontier.append(link["target_key"])
        if inside:
            steps.insert(anchor, {"from": repo_name, "to": repo_name, "kind": "internal", "label": " → ".join(dict.fromkeys(inside))[:300], "via": "", "status": "internal",
                                  "target": "", "group_key": ""})

    walk(repo_id, start_analysis, key, 0)
    start_item = items[key]
    title = f"{start_item['name']} ({names.get(repo_id)})"
    return {"title": title, "start": {"repo_id": repo_id, "key": key, "name": start_item["name"], "kind": start_item["kind"], "item_id": start_item.get("item_id"), "path": start_item.get("path"), "analysis_id": start_analysis}, "steps": steps,
            "participants": participants, "mermaid": _journey_mermaid(participants, steps, start_item["name"])}


def _journey_mermaid(participants: list[str], steps: list[dict], start: str) -> str:
    alias = {p: f"P{i}" for i, p in enumerate(participants)}
    lines = ["sequenceDiagram", "  autonumber"]
    for p in participants:
        lines.append(f"  participant {alias[p]} as {_safe(p, 40)}")
    if participants:
        lines.append(f"  Note over {alias[participants[0]]}: {_safe(start, 60)}")
    for s in steps[:80]:
        a, b = alias.get(s["from"]), alias.get(s["to"])
        if not a or not b:
            continue
        if s["kind"] == "internal":
            hops = [h.strip() for h in s["label"].split("→") if h.strip() and not re.match(r"^[A-Z]+ /", h.strip())]
            short = " → ".join(hops[:3]) + (f" (+{len(hops) - 3})" if len(hops) > 3 else "")
            if short:
                lines.append(f"  Note right of {a}: {_safe(short, 70)}")
            continue
        arrow = {"messaging": "-)", "database": "->>", "table": "->>"}.get(s["kind"], "->>")
        if s["status"] == "review":
            arrow = "--)"
        text = _safe(s["label"], 70) + (" (to review)" if s["status"] == "review" else "")
        lines.append(f"  {a}{arrow}{b}: {text}")
    return "\n".join(lines)


# --- pull-request impact link-up -----------------------------------------------------------------------------------


def consumers_of(repo_id: str, item_keys: list[str], paths: list[str]) -> list[dict]:
    """Consumers in other repositories of the given endpoints / listeners (by key) and of topics published from the
    given files, across every landscape the repository is in (active links only)."""
    if not item_keys and not paths:
        return []
    rows = db.fetchall(
        """
        SELECT l.*, s.name AS landscape_name FROM landscape_links l JOIN landscapes s ON s.landscape_id = l.landscape_id
        WHERE l.status IN ('auto', 'confirmed') AND ((l.target_repo_id = ? AND l.kind = 'http') OR (l.source_repo_id = ? AND l.kind = 'messaging'))
        """,
        [repo_id, repo_id],
    )
    keys, files = set(item_keys), set(paths)
    out: dict[tuple, dict] = {}
    repo_names = {r["repo_id"]: r["name"] for r in db.fetchall("SELECT repo_id, name FROM repositories")}
    for row in rows:
        if row["kind"] == "http" and row["target_key"] in keys:
            interface, consumer_repo, consumer = row["target_name"], row["source_repo_id"], row["source_name"]
            where = f"{row['source_path']}:{row['source_line']}"
        elif row["kind"] == "messaging" and row["target_repo_id"] and row["source_path"] in files:
            interface, consumer_repo, consumer = row["label"], row["target_repo_id"], row["target_name"]
            where = f"{row['target_path']}:{row['target_line']}"
        else:
            continue
        key = (row["landscape_id"], interface, consumer_repo, consumer)
        out.setdefault(key, {"landscape_id": row["landscape_id"], "landscape": row["landscape_name"], "kind": row["kind"], "interface": interface,
                             "consumer_repo_id": consumer_repo, "consumer_repo": repo_names.get(consumer_repo, consumer_repo), "consumer": consumer, "where": where,
                             "status": row["status"], "confidence": row["confidence"]})
    return sorted(out.values(), key=lambda c: (c["interface"], c["consumer_repo"], c["consumer"]))


def in_any_landscape(repo_id: str) -> bool:
    return db.fetchone("SELECT landscape_id FROM landscape_repos WHERE repo_id = ? LIMIT 1", [repo_id]) is not None


# --- exports -------------------------------------------------------------------------------------------------------


def _cell(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def report_markdown(user: dict, landscape_id: str) -> str:
    landscape = get_landscape(user, landscape_id)
    stats = landscape["stats"]
    data = interfaces(user, landscape_id)
    pending = review(user, landscape_id)["pending"]
    smap = system_map(user, landscape_id, include_review=False)
    names = {n["id"]: n["label"] for n in smap["nodes"]}
    lines = [f"# System Landscape — {landscape['name']}", ""]
    if landscape["description"]:
        lines += [landscape["description"], ""]
    lines += [
        f"- **Repositories:** {', '.join(r['name'] for r in landscape['repos'])}",
        f"- **Cross-repository links:** {stats.get('links', 0)} · **external interfaces:** {stats.get('external', 0)} · **waiting for review:** {stats.get('review', 0)}"
        f" · **unresolved calls:** {stats.get('unresolved', 0)}",
        f"- **Auto-accept:** {'high confidence only' if landscape['auto_accept'] == 'high' else 'high and medium confidence'}",
        f"- **Computed:** {str(landscape.get('computed_at') or '')[:19].replace('T', ' ')} UTC",
        "",
        "## Connections",
        "",
        "| From | To | Type | Interfaces |",
        "| :--- | :--- | :--- | ---: |",
    ]
    for e in sorted(smap["edges"], key=lambda e: (names.get(e["source"], ""), names.get(e["target"], ""))):
        lines.append(f"| {_cell(names.get(e['source'], e['source']))} | {_cell(names.get(e['target'], e['target']))} | {e['kind']} | {e['count']} |")
    lines += ["", "## Provided Interfaces and Their Consumers", ""]
    if data["provided"]:
        lines += ["| Provider | Interface | Type | Consumers | Feature |", "| :--- | :--- | :--- | :--- | :--- |"]
        for p in data["provided"][:400]:
            consumers = "; ".join(f"{c['repo_name']}: {c['name']}" for c in p["consumers"][:6]) + (f" (+{len(p['consumers']) - 6})" if len(p["consumers"]) > 6 else "")
            lines.append(f"| {_cell(p['repo_name'])} | `{_cell(p['name'])}` | {p['kind']} | {_cell(consumers)} | {_cell(', '.join(p['features']))} |")
    else:
        lines.append("No cross-repository interfaces found yet.")
    lines += ["", "## External Systems", ""]
    for ext in data["external"][:300]:
        users = ", ".join(sorted({u["repo_name"] for u in ext["users"]}))
        lines.append(f"- **{ext['name']}** ({ext['type']}) — used by {users}")
    if pending:
        lines += ["", f"## Waiting for Review ({len(pending)})", ""]
        for g in pending[:200]:
            cands = ", ".join(f"{c['target_repo_name'] or c['external_name']} → {c['target_name'] or c['external_name']} ({c['score']})" for c in g["candidates"][:4])
            lines.append(f"- {'**ambiguous** ' if g['ambiguous'] else ''}{g['source_repo_name']}: {g['source_name']} — candidates: {cands}")
    return "\n".join(lines).rstrip() + "\n"


def report_pdf(user: dict, landscape_id: str) -> bytes:
    from app.services import md_pdf

    return md_pdf.render(report_markdown(user, landscape_id), "Code Analyzer System Landscape")


def export_xlsx(user: dict, landscape_id: str) -> bytes:
    from app.services import xlsx

    all_links = links(user, landscape_id, limit=10000)["items"]
    data = interfaces(user, landscape_id)
    link_rows = [[lk["kind"], lk["status"], lk["confidence"], lk["score"], lk["source_repo_name"], lk["source_name"], f"{lk['source_path']}:{lk['source_line']}",
                  lk["target_repo_name"] or lk["external_name"], lk["target_name"], f"{lk['target_path']}:{lk['target_line']}" if lk["target_path"] else "", lk["label"],
                  ", ".join(lk["features"]), "; ".join(lk["evidence"])] for lk in all_links]
    provided = [[p["repo_name"], p["name"], p["kind"], p["item_kind"], f"{p['path']}:{p['line']}", len(p["consumers"]),
                 "; ".join(f"{c['repo_name']}: {c['name']}" for c in p["consumers"]), ", ".join(p["features"])] for p in data["provided"]]
    external = [[e["name"], e["type"], ", ".join(sorted({u["repo_name"] for u in e["users"]})), len(e["users"])] for e in data["external"]]
    return xlsx.workbook([
        ("Links", ["Type", "Status", "Confidence", "Score", "From repository", "From", "From location", "To repository / system", "To", "To location", "Interface",
                   "Features", "Evidence"], link_rows),
        ("Provided interfaces", ["Provider", "Interface", "Type", "Item kind", "Location", "Consumers", "Consumed by", "Features"], provided),
        ("External systems", ["System", "Type", "Used by", "References"], external),
    ])
