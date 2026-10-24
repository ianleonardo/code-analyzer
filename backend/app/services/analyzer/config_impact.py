"""Configuration, infrastructure and CI impact of a change, analysed at the key level.

Config files (YAML, .properties, .env, JSON, TOML, INI, XML, Helm values, tfvars) are parsed on both sides and diffed
by key and environment instead of by text line, so reordering and formatting do not show up as changes. Each changed
key is linked to the code that reads it (Spring @Value / @ConfigurationProperties, Environment.getProperty, getenv,
process.env, os.environ, settings objects, IConfiguration["A:B"], literal keys) and to the framework that consumes it
when no code reads it (spring.datasource.*, server.port, K8s resources, Helm replicaCount, ...).

Risk rules flag large numeric changes to timeouts / pools / retries, loosened TLS / auth / CORS, flipped feature flags,
changed endpoints, secrets committed in the diff, removed keys that code still reads and added keys nothing reads.
Kubernetes, Dockerfile, Terraform and CI pipeline changes get their own rules. Values that look secret are masked.
"""

from __future__ import annotations

import configparser
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from app.services.analyzer.catalog import config as catalog_config
from app.services.analyzer.diff import FileDiff

ENV_WORDS = {
    "dev": "dev", "development": "dev", "local": "local", "test": "test", "testing": "test", "qa": "qa", "sit": "sit", "uat": "uat",
    "stage": "staging", "staging": "staging", "stg": "staging", "preprod": "preprod", "pre-prod": "preprod", "perf": "perf",
    "prod": "prod", "production": "prod", "prd": "prod", "live": "prod", "dr": "dr",
}
PROD = {"prod", "default", "dr"}
SKIP_NAMES = catalog_config.SKIP_JSON | {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pom.xml.versionsbackup", "openapi.yaml", "openapi.yml",
                                         "swagger.yaml", "swagger.yml", ".pre-commit-config.yaml", "mkdocs.yml", ".eslintrc.yml", ".prettierrc.yml", "codecov.yml",
                                         "dependabot.yml", ".markdownlint.yml", "environment.yml", "conda.yml"}
CONFIG_SUFFIXES = (".yml", ".yaml", ".properties", ".json", ".toml", ".ini", ".cfg", ".conf", ".xml", ".tfvars", ".env", ".plist", ".xcconfig")
SECRET_KEY = catalog_config.SECRET
SECRET_LINE = re.compile(
    r"""(?ix)
    (?P<key>[\w.\-]*(?:password|passwd|pwd|secret|token|api[-_.]?key|apikey|access[-_.]?key|private[-_.]?key|client[-_.]?secret|credential|signing[-_.]?key)[\w.\-]*)
    ["']?\s*(?:=|:|=>)\s*["']?(?P<value>[^\s"',;}{)]{6,})
    """
)
PLACEHOLDER = re.compile(r"^(\$\{.*\}|\$\(.*\)|\{\{.*\}\}|<.*>|%\(.*\)s|\*+|x+|changeme|change_me|your[-_].*|example.*|dummy.*|placeholder|null|none|true|false|test|password|secret|\$\w+|process\.env.*|os\.environ.*|env\(.*|getenv.*)$", re.I)
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----")
NUMERIC_KEY = re.compile(r"(timeout|pool|retry|retries|max|min|size|limit|ttl|threads?|connections?|interval|delay|capacity|batch|replicas?|concurrency|backoff|expir|duration|memory|cpu|workers?|queue)", re.I)
SECURITY_KEY = re.compile(r"(ssl|tls|verify|secure|cors|csrf|auth|https|strict|cert|allowed[-_.]?origins?|allow[-_.]?origin|xss|hsts|sameSite|insecure|permit|anonymous)", re.I)
FLAG_KEY = re.compile(r"(feature|flag|toggle|enabled?$|disabled?$|\.enabled\b|_enabled\b|\.enable\b|switch)", re.I)
ENDPOINT_KEY = re.compile(r"(url|uri|host|endpoint|address|base[-_.]?path|server|broker|bootstrap[-_.]?servers|dsn|queue[-_.]?name|topic|bucket|region)", re.I)
DEBUG_KEY = re.compile(r"(^|[._])(debug|logging\.level|log[-_.]?level|show[-_]sql)($|[._])", re.I)
TRUTHY = {"true", "yes", "on", "1", "enabled", "enable"}
FALSY = {"false", "no", "off", "0", "disabled", "disable", "none", "never"}

# Keys consumed by a framework or platform rather than read by the repository's own code: prefix -> (category, consumer)
FRAMEWORK_KEYS: list[tuple[str, str, str]] = [
    ("spring.datasource", "database connection", "Spring Boot"), ("spring.r2dbc", "database connection", "Spring Boot"),
    ("spring.jpa", "persistence", "Spring Data JPA"), ("hibernate", "persistence", "Hibernate"), ("spring.flyway", "migrations", "Flyway"),
    ("spring.liquibase", "migrations", "Liquibase"), ("server.", "web server", "Spring Boot"), ("logging.", "logging", "Spring Boot"),
    ("spring.security", "security", "Spring Security"), ("management.", "monitoring", "Spring Actuator"), ("spring.kafka", "messaging", "Spring Kafka"),
    ("spring.rabbitmq", "messaging", "Spring AMQP"), ("spring.cache", "cache", "Spring Cache"), ("spring.redis", "cache", "Spring Data Redis"),
    ("spring.data.redis", "cache", "Spring Data Redis"), ("spring.mail", "mail", "Spring Boot"), ("spring.task", "scheduling", "Spring Boot"),
    ("spring.batch", "batch", "Spring Batch"), ("spring.quartz", "scheduling", "Quartz"), ("spring.cloud", "cloud", "Spring Cloud"), ("eureka.", "cloud", "Eureka"),
    ("resilience4j", "resilience", "Resilience4j"), ("feign.", "integration", "OpenFeign"), ("spring.profiles", "profiles", "Spring Boot"),
    ("spring.application", "application", "Spring Boot"), ("spring.servlet", "web server", "Spring Boot"), ("spring.mvc", "web server", "Spring MVC"),
    ("Logging.", "logging", ".NET"), ("ConnectionStrings.", "database connection", ".NET"), ("Kestrel.", "web server", ".NET"), ("AllowedHosts", "security", ".NET"),
    ("NODE_ENV", "application", "Node.js"), ("PORT", "web server", "platform"), ("DATABASE_URL", "database connection", "platform"), ("REDIS_URL", "cache", "platform"),
    ("LOG_LEVEL", "logging", "platform"), ("DEBUG", "application", "framework"), ("ALLOWED_HOSTS", "security", "Django"), ("DJANGO_", "application", "Django"),
    ("replicaCount", "scaling", "Helm"), ("autoscaling.", "scaling", "Helm"), ("resources.", "resources", "Kubernetes / Helm"), ("image.", "deployment", "Helm"),
    ("ingress.", "networking", "Helm"), ("service.", "networking", "Helm"), ("spec.replicas", "scaling", "Kubernetes"), ("spec.template", "deployment", "Kubernetes"),
    ("services.", "deployment", "Docker Compose"), ("project.properties", "build", "Maven"), ("project.dependencies", "build", "Maven"),
]


@dataclass
class Entry:
    key: str
    value: str
    line: int
    profile: str
    path: str


@dataclass
class ConfigResult:
    keys: list[dict] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    infra: list[dict] = field(default_factory=list)
    ci: list[dict] = field(default_factory=list)
    environments: dict[str, dict] = field(default_factory=dict)
    files: list[dict] = field(default_factory=list)
    external_config: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"keys": self.keys, "findings": self.findings, "infra": self.infra, "ci": self.ci, "environments": self.environments, "files": self.files,
                "external_config": self.external_config}


# --- classification ---------------------------------------------------------------------------------------------


def is_ci(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return path.startswith((".github/workflows/", ".circleci/", ".buildkite/")) or name in (
        "jenkinsfile", ".gitlab-ci.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".travis.yml", "cloudbuild.yaml", "buildspec.yml", ".drone.yml", "appveyor.yml")


def is_dockerfile(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name.startswith("dockerfile") or name.endswith(".dockerfile") or name == "containerfile"


def is_terraform(path: str) -> bool:
    return path.endswith((".tf", ".tf.json"))


def is_config(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name
    lowered = name.lower()
    if lowered in SKIP_NAMES or is_ci(path) or lowered.endswith((".lock", ".lockb")):
        return False
    if lowered == ".env" or lowered.startswith(".env.") or lowered.endswith(".env") or lowered in ("gradle.properties",):
        return True
    if lowered.endswith(".json") and not re.search(r"(config|settings|appsettings|env|values|parameters|secrets?|properties)", lowered) and "/config/" not in f"/{path}":
        return False
    if lowered.endswith(".xml") and not (lowered == "pom.xml" or re.search(r"(config|settings|application|web|context|beans|log4j|logback)", lowered)):
        return False
    return lowered.endswith(CONFIG_SUFFIXES)


def environment_of(path: str, profile: str = "") -> str:
    """Environment a config file applies to; 'default' when it applies everywhere unless overridden."""
    if profile:
        return ENV_WORDS.get(profile.lower(), profile.lower())
    pure = PurePosixPath(path)
    name = pure.name.lower()
    patterns = [
        r"^(?:application|bootstrap)-([\w.-]+)\.(?:properties|ya?ml)$", r"^\.env\.([\w.-]+)$", r"^appsettings\.([\w.-]+)\.json$",
        r"^values[-.]([\w.-]+)\.ya?ml$", r"^(?:terraform\.)?([\w-]+)\.tfvars$", r"^(?:config|settings)[-.]([\w-]+)\.\w+$", r"^([\w-]+)\.(?:env|properties|ya?ml|json|toml)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, name)
        if match:
            label = match.group(1).lower()
            if label in ENV_WORDS:
                return ENV_WORDS[label]
            if not pattern.startswith(r"^([\w-]+)"):
                return label
    for part in reversed(pure.parts[:-1]):
        if part.lower() in ENV_WORDS:
            return ENV_WORDS[part.lower()]
    return "default"


def is_messages(path: str) -> bool:
    """Translation bundles: keys are read by templates / i18n libraries, so 'nothing reads it' is not a finding."""
    lowered = path.lower()
    name = PurePosixPath(lowered).name
    return bool(re.match(r"^(messages|strings|resources?|labels|translations?|lang|locale)([_-][\w-]+)?\.(properties|json|ya?ml|resx)$", name)) or bool(
        re.search(r"(^|/)(i18n|l10n|locales?|lang|translations?|messages)/", lowered)) or name.endswith((".po", ".pot", ".xlf", ".xliff", ".arb"))


def is_build_tooling(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in ("maven-wrapper.properties", "gradle-wrapper.properties", ".npmrc", ".yarnrc", ".yarnrc.yml", ".nvmrc", ".tool-versions", ".sdkmanrc",
                    "rust-toolchain.toml", "global.json", ".python-version") or "/wrapper/" in f"/{path.lower()}"


def framework_consumer(key: str) -> tuple[str, str] | None:
    for prefix, category, consumer in FRAMEWORK_KEYS:
        if key == prefix.rstrip(".") or key.startswith(prefix):
            return category, consumer
    return None


# --- parsing ----------------------------------------------------------------------------------------------------


def parse(path: str, text: str) -> list[Entry]:
    """Flattens one config file into (key, value, line, profile) entries. Unknown or broken files give none."""
    if not text or len(text) > 2_000_000:
        return []
    name = PurePosixPath(path).name.lower()
    out: list[Entry] = []
    try:
        if name.endswith((".yml", ".yaml")):
            for key, value, line, _comment, profile in catalog_config.parse_yaml(text):
                if key:
                    out.append(Entry(key, value, line, profile, path))
        elif name.endswith(".properties") or name == ".env" or name.startswith(".env.") or name.endswith(".env") or name.endswith(".xcconfig"):
            for key, value, line, _comment in catalog_config.parse_properties(text):
                out.append(Entry(key.removeprefix("export "), value.strip("\"'"), line, "", path))
        elif name.endswith(".json"):
            data = json.loads(text)
            lines = text.splitlines()
            for key, value in catalog_config._flatten_json(data):
                leaf = key.split(".")[-1].split("[")[0]
                out.append(Entry(key, value, next((i + 1 for i, row in enumerate(lines) if f'"{leaf}"' in row), 0), "", path))
        elif name.endswith(".toml"):
            data = tomllib.loads(text)
            for key, value in catalog_config._flatten_json(data):
                out.append(Entry(key, value, _line_of(text, key.split(".")[-1]), "", path))
        elif name.endswith((".ini", ".cfg", ".conf")):
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read_string(text if re.search(r"^\s*\[", text, re.M) else "[root]\n" + text)
            for section in parser.sections():
                for key, value in parser.items(section, raw=True):
                    out.append(Entry(f"{section}.{key}" if section != "root" else key, value, _line_of(text, key), "", path))
        elif name.endswith(".xml"):
            root = ET.fromstring(text)
            for key, value in _flatten_xml(root):
                out.append(Entry(key, value, _line_of(text, key.split(".")[-1]), "", path))
        elif name.endswith(".tfvars"):
            for match in re.finditer(r"^\s*([A-Za-z_][\w-]*)\s*=\s*(.+?)\s*$", text, re.M):
                out.append(Entry(match.group(1), match.group(2).strip('"'), text.count("\n", 0, match.start()) + 1, "", path))
    except (ValueError, ET.ParseError, configparser.Error, tomllib.TOMLDecodeError, yaml.YAMLError, RecursionError):
        return []
    return out[:3000]


def _line_of(text: str, leaf: str) -> int:
    index = text.find(leaf)
    return text.count("\n", 0, index) + 1 if index >= 0 else 0


def _flatten_xml(node, prefix: str = "", out: list | None = None, depth: int = 0) -> list[tuple[str, str]]:
    out = out if out is not None else []
    if depth > 8 or len(out) > 1500:
        return out
    tag = re.sub(r"^\{[^}]*\}", "", node.tag if isinstance(node.tag, str) else "")
    key = f"{prefix}.{tag}" if prefix else tag
    children = list(node)
    for attr, value in node.attrib.items():
        out.append((f"{key}@{re.sub(r'^{[^}]*}', '', attr)}", value))
    if not children:
        if (node.text or "").strip():
            out.append((key, node.text.strip()[:300]))
        return out
    seen: dict[str, int] = {}
    for child in children:
        ctag = re.sub(r"^\{[^}]*\}", "", child.tag if isinstance(child.tag, str) else "")
        seen[ctag] = seen.get(ctag, 0) + 1
    index: dict[str, int] = {}
    for child in children:
        ctag = re.sub(r"^\{[^}]*\}", "", child.tag if isinstance(child.tag, str) else "")
        if seen[ctag] > 1:
            ident = child.findtext("{*}artifactId") or child.findtext("artifactId") or child.attrib.get("name") or child.attrib.get("id")
            n = index[ctag] = index.get(ctag, -1) + 1
            _flatten_xml_named(child, f"{key}.{ctag}[{ident or n}]", out, depth + 1)
        else:
            _flatten_xml(child, key, out, depth + 1)
    return out


def _flatten_xml_named(node, key: str, out: list, depth: int) -> None:
    children = list(node)
    if not children:
        if (node.text or "").strip():
            out.append((key, node.text.strip()[:300]))
        return
    for child in children:
        _flatten_xml(child, key, out, depth + 1)


# --- values -----------------------------------------------------------------------------------------------------

_DURATION = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hours?|d|days?|k|ki|m|mi|g|gi|kb|mb|gb)?\s*$", re.I)
_UNIT = {"ms": 0.001, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "h": 3600, "hr": 3600,
         "hour": 3600, "hours": 3600, "d": 86400, "day": 86400, "days": 86400, "k": 1e3, "ki": 1024, "mi": 1024 ** 2, "g": 1e9, "gi": 1024 ** 3, "kb": 1e3, "mb": 1e6, "gb": 1e9}


def number(value: str, key: str = "") -> float | None:
    match = _DURATION.match(str(value or ""))
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "m" and re.search(r"(memory|mem|size|limit|request)", key, re.I):
        unit = "mb"  # Kubernetes memory "512m" is not minutes
    if re.search(r"(cpu)", key, re.I) and unit == "m":
        return amount / 1000
    return amount * _UNIT.get(unit, 1)


def mask(key: str, value: str) -> tuple[str, bool]:
    return catalog_config.mask(key, value)


# --- readers ----------------------------------------------------------------------------------------------------


def _reader_patterns(key: str) -> list[tuple[re.Pattern, str]]:
    pats: list[tuple[re.Pattern, str]] = []
    escaped = re.escape(key)
    pats.append((re.compile(rf"""["'`]{escaped}["'`]"""), "literal key"))
    pats.append((re.compile(rf"\$\{{{escaped}(?:[:}}])"), "${placeholder}"))
    pats.append((re.compile(rf"#\{{{escaped}[}}(]"), "template message key"))
    if ":" not in key and "." in key:
        pats.append((re.compile(rf"""["']{re.escape(key.replace('.', ':'))}["']"""), "IConfiguration key"))
    if re.match(r"^[A-Z][A-Z0-9_]+$", key):
        pats.append((re.compile(rf"\b{escaped}\b"), "environment variable"))
        pats.append((re.compile(rf"\b(?:settings|config|cfg|conf)\.{re.escape(key.lower())}\b|^\s*{re.escape(key.lower())}\s*:\s*\w", re.M), "settings field"))
    elif "." in key or "-" in key:
        env = re.sub(r"[.\-\[\]]+", "_", key).strip("_").upper()
        if len(env) > 4:
            pats.append((re.compile(rf"\b{re.escape(env)}\b"), "relaxed environment binding"))
    return pats


def find_readers(keys: list[str], code: dict[str, str], catalog_used: dict[str, list[dict]] | None = None, limit: int = 12) -> dict[str, list[dict]]:
    """Code locations that read each key. `code` is path -> text of the head revision's code files."""
    readers: dict[str, list[dict]] = {key: list((catalog_used or {}).get(key, []))[:limit] for key in keys}
    props_prefixes: list[tuple[str, str, str]] = []  # (prefix, path, text) for @ConfigurationProperties classes
    for path, text in code.items():
        for match in re.finditer(r"@ConfigurationProperties\s*\(\s*(?:prefix\s*=\s*|value\s*=\s*)?\"([\w.\-]+)\"", text):
            props_prefixes.append((match.group(1), path, text))
    for key in keys:
        seen = {(r.get("path"), r.get("line")) for r in readers[key]}
        patterns = _reader_patterns(key)
        for path, text in code.items():
            if len(readers[key]) >= limit:
                break
            for pattern, via in patterns:
                match = pattern.search(text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    if (path, line) not in seen:
                        readers[key].append({"path": path, "line": line, "via": via})
                        seen.add((path, line))
                    break
        for prefix, path, text in props_prefixes:
            if key.startswith(prefix + ".") and len(readers[key]) < limit:
                leaf = key[len(prefix) + 1:].split(".")[0]
                camel = re.sub(r"[-_](\w)", lambda m: m.group(1).upper(), leaf)
                match = re.search(rf"\b{re.escape(camel)}\b", text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    if (path, line) not in seen:
                        readers[key].append({"path": path, "line": line, "via": f"@ConfigurationProperties({prefix})"})
                        seen.add((path, line))
    return readers


# --- diff -------------------------------------------------------------------------------------------------------


def diff_entries(base: list[Entry], head: list[Entry]) -> list[dict]:
    def keyed(entries: list[Entry]) -> dict[tuple[str, str], Entry]:
        out: dict[tuple[str, str], Entry] = {}
        for entry in entries:
            out[(entry.key, environment_of(entry.path, entry.profile))] = entry
        return out

    before, after = keyed(base), keyed(head)
    changes = []
    for ident in sorted(set(before) | set(after)):
        a, b = before.get(ident), after.get(ident)
        if a and b and str(a.value) == str(b.value):
            continue
        key, env = ident
        change = "added" if not a else "removed" if not b else "changed"
        changes.append({"key": key, "environment": env, "change": change, "before": a.value if a else None, "after": b.value if b else None,
                        "path": (b or a).path, "line": (b or a).line})
    return changes


def analyse(file_diffs: list[FileDiff], base_text: dict[str, str], head_text: dict[str, str], head_code: dict[str, str],
            head_defined_keys: set[str], catalog_used: dict[str, list[dict]] | None = None, secret_diffs: list[FileDiff] | None = None) -> ConfigResult:
    """Everything config / infra / CI for one PR. Texts are path -> content for the changed files on each side.

    `secret_diffs` (default: file_diffs) is scanned for committed secrets: pass every changed file, not only config.
    """
    result = ConfigResult()
    all_changes: list[dict] = []
    for fd in file_diffs:
        path, old = fd.path, fd.old_path or fd.path
        before_text = base_text.get(old, "") if fd.status != "A" else ""
        after_text = head_text.get(path, "") if fd.status != "D" else ""
        kind = "ci" if is_ci(path) else "dockerfile" if is_dockerfile(path) else "terraform" if is_terraform(path) else "config" if is_config(path) else ""
        if not kind:
            continue
        k8s = kind == "config" and path.endswith((".yml", ".yaml")) and bool(re.search(r"^\s*apiVersion:", after_text or before_text, re.M)) and bool(re.search(r"^\s*kind:", after_text or before_text, re.M))
        compose = kind == "config" and re.match(r"^(docker-)?compose[\w.-]*\.ya?ml$", PurePosixPath(path).name.lower()) is not None
        helm = kind == "config" and (PurePosixPath(path).name.lower().startswith("values") and path.endswith((".yml", ".yaml")) or "/charts/" in f"/{path}")
        label = "Kubernetes manifest" if k8s else "Docker Compose" if compose else "Helm values" if helm else {"ci": "CI pipeline", "dockerfile": "Dockerfile", "terraform": "Terraform"}.get(kind, "Configuration")
        if kind == "config" and is_messages(path):
            label = "Translations"
        elif kind == "config" and is_build_tooling(path):
            label = "Build tooling"
        result.files.append({"path": path, "status": fd.status, "kind": label, "environment": environment_of(path)})
        if kind == "config":
            changes = diff_entries(parse(old, before_text), parse(path, after_text))
            for change in changes:
                change["file_kind"] = label
            all_changes += changes
        elif kind == "dockerfile":
            result.infra += _dockerfile_rules(fd)
        elif kind == "terraform":
            result.infra += _terraform_rules(fd, after_text or before_text)
        elif kind == "ci":
            result.ci += _ci_rules(fd)
    # readers for every changed key (head revision code)
    keys = sorted({c["key"] for c in all_changes})
    readers = find_readers(keys, head_code, catalog_used) if keys else {}
    for change in all_changes:
        key = change["key"]
        before, secret_a = mask(key, change["before"]) if change["before"] is not None else (None, False)
        after, secret_b = mask(key, change["after"]) if change["after"] is not None else (None, False)
        consumer = framework_consumer(key)
        k8s_like = change["file_kind"] in ("Kubernetes manifest", "Helm values", "Docker Compose")
        if change["file_kind"] in ("Translations", "Build tooling"):
            consumer = ("i18n", "templates / i18n library") if change["file_kind"] == "Translations" else ("build", "build tool")
        change.update({
            "before": before, "after": after, "secret": secret_a or secret_b, "readers": readers.get(key, []),
            "category": consumer[0] if consumer else ("deployment" if k8s_like else catalog_config.category(key)),
            "consumer": consumer[1] if consumer else ("Kubernetes" if change["file_kind"] == "Kubernetes manifest" else change["file_kind"] if k8s_like else ""),
            "_raw_before": change["before"], "_raw_after": change["after"],
        })
        change["findings"] = _key_rules(change, head_defined_keys)
        result.findings += [dict(f, key=key, environment=change["environment"], path=change["path"]) for f in change["findings"]]
        change.pop("_raw_before", None)
        change.pop("_raw_after", None)
        env = result.environments.setdefault(change["environment"], {"added": 0, "removed": 0, "changed": 0, "findings": 0})
        env[change["change"]] += 1
        env["findings"] += len(change["findings"])
    result.keys = sorted(all_changes, key=lambda c: (_severity_rank(c["findings"]), c["environment"] != "prod", c["key"]))[:1500]
    result.findings += _secret_rules(secret_diffs if secret_diffs is not None else file_diffs)
    result.findings += [dict(f, key=f.get("key", ""), environment=environment_of(f.get("path", ""))) for f in result.infra + result.ci if f["severity"] in ("critical", "high", "medium")]
    for text in list(head_text.values())[:400]:
        for marker, label in (("spring.cloud.config.uri", "Spring Cloud Config"), ("vault://", "HashiCorp Vault"), ("spring.cloud.vault", "HashiCorp Vault"),
                              ("ssm:", "AWS Parameter Store"), ("secretsmanager", "AWS Secrets Manager"), ("azure.keyvault", "Azure Key Vault"), ("keyvault", "Azure Key Vault")):
            if marker in text and label not in result.external_config:
                result.external_config.append(label)
    result.findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f.get("environment") != "prod", f.get("key", "")))
    return result


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _severity_rank(findings: list[dict]) -> int:
    return min((SEVERITY_ORDER.get(f["severity"], 9) for f in findings), default=9)


def _finding(severity: str, rule: str, message: str) -> dict:
    return {"severity": severity, "rule": rule, "message": message}


def _key_rules(change: dict, head_defined_keys: set[str]) -> list[dict]:
    key, env, kind = change["key"], change["environment"], change["change"]
    if change["file_kind"] == "Translations":
        return []
    if change["file_kind"] == "Build tooling":
        return [_finding("low", "build-tool", f"{key}: {change['_raw_before'] or '—'} → {change['_raw_after'] or '—'}")] if kind == "changed" else []
    raw_a, raw_b = change.get("_raw_before"), change.get("_raw_after")
    prod = env in PROD
    out: list[dict] = []
    leaf = key.split(".")[-1]
    a_norm, b_norm = str(raw_a or "").strip().lower(), str(raw_b or "").strip().lower()
    if kind == "changed":
        na, nb = number(str(raw_a), key), number(str(raw_b), key)
        if NUMERIC_KEY.search(leaf) and na is not None and nb is not None and na > 0 and nb > 0:
            factor = nb / na
            if factor >= 3 or factor <= 1 / 3:
                out.append(_finding("high" if prod else "medium", "numeric-factor",
                                    f"{key} changes by ×{factor:.2g} ({raw_a} → {raw_b}){' in ' + env if env != 'default' else ''}"))
            if re.search(r"replica", key, re.I) and nb < na:
                out.append(_finding("medium", "scale-down", f"{key} scales down from {raw_a} to {raw_b}"))
        if SECURITY_KEY.search(key) and not change["secret"]:
            loosened = (a_norm in TRUTHY and b_norm in FALSY) or (b_norm in ("*", "'*'", '"*"') and a_norm not in ("*",)) or ("http://" in b_norm and "https://" in a_norm)
            if re.search(r"(insecure|permit|anonymous|skip|disable)", key, re.I):
                loosened = a_norm in FALSY and b_norm in TRUTHY
            if loosened:
                out.append(_finding("critical" if prod else "high", "security-loosened", f"{key} loosened: {raw_a} → {raw_b}"))
        if FLAG_KEY.search(key) and {a_norm, b_norm} <= (TRUTHY | FALSY) and (a_norm in TRUTHY) != (b_norm in TRUTHY):
            out.append(_finding("medium", "feature-flag", f"Feature flag {key} flipped {'on' if b_norm in TRUTHY else 'off'}{' in ' + env if env != 'default' else ''}"))
        if ENDPOINT_KEY.search(leaf) and not change["secret"] and not NUMERIC_KEY.search(leaf):
            out.append(_finding("high" if prod else "medium", "endpoint-changed", f"{key} now points to {change['after']}{' in ' + env if env != 'default' else ''}"))
        if DEBUG_KEY.search(key) and env == "prod" and (b_norm in TRUTHY or b_norm in ("debug", "trace")):
            out.append(_finding("high", "debug-in-prod", f"{key} enables debug output in production"))
        if re.search(r"(^|\.)image(\.tag)?$|\.image$", key) and b_norm.endswith(":latest"):
            out.append(_finding("medium", "latest-tag", f"{key} uses the mutable :latest tag"))
        if re.search(r"(privileged|allowPrivilegeEscalation|hostNetwork|hostPID)$", key) and b_norm in TRUTHY:
            out.append(_finding("high", "privileged", f"{key} grants elevated container privileges"))
        if re.search(r"runAsNonRoot$", key) and b_norm in FALSY:
            out.append(_finding("high", "root-container", f"{key} allows the container to run as root"))
        if re.search(r"(resources\.limits|resources\.requests)\.", key):
            out.append(_finding("low", "resources", f"{key}: {raw_a} → {raw_b}"))
    elif kind == "removed":
        if change["readers"] and key not in head_defined_keys:
            out.append(_finding("high", "removed-but-read", f"{key} was removed{' from ' + env if env != 'default' else ''} but code still reads it ({change['readers'][0]['path']})"))
        if re.search(r"(Probe|probe)\b|livenessProbe|readinessProbe|startupProbe", key):
            out.append(_finding("medium", "probe-removed", f"Health probe setting {key} removed"))
        if re.search(r"resources\.limits", key):
            out.append(_finding("medium", "limit-removed", f"Resource limit {key} removed"))
    elif kind == "added":
        if not change["readers"] and not change["consumer"] and not change["secret"] and change["file_kind"] == "Configuration":
            out.append(_finding("low", "unread-key", f"{key} is added but nothing reads it (a typo, or wired outside this repository?)"))
        if SECURITY_KEY.search(key) and (b_norm in FALSY and not re.search(r"(insecure|permit|anonymous|skip|disable)", key, re.I) or b_norm == "*"):
            out.append(_finding("high" if prod else "medium", "security-loosened", f"{key} added as {raw_b}"))
        if FLAG_KEY.search(key) and b_norm in TRUTHY | FALSY:
            out.append(_finding("low", "feature-flag", f"New feature flag {key} = {raw_b}"))
        if re.search(r"(privileged|allowPrivilegeEscalation|hostNetwork|hostPID)$", key) and b_norm in TRUTHY:
            out.append(_finding("high", "privileged", f"{key} grants elevated container privileges"))
    return out


def _secret_rules(file_diffs: list[FileDiff]) -> list[dict]:
    out = []
    for fd in file_diffs:
        if fd.status == "D" or re.search(r"(^|/)(tests?|__tests__|spec|fixtures?|mocks?|testdata)/|\.(md|lock)$|_test\.|\.test\.|\.spec\.", fd.path, re.I):
            continue
        for hunk in fd.hunks:
            for line_no, text in hunk.added:
                if PRIVATE_KEY.search(text):
                    out.append({"severity": "critical", "rule": "secret-committed", "message": "A private key was committed", "path": fd.path, "line": line_no, "key": "private key", "environment": environment_of(fd.path)})
                    continue
                match = SECRET_LINE.search(text)
                if not match:
                    continue
                value = match.group("value").strip()
                if PLACEHOLDER.match(value) or re.search(r"[(){}\[\]$]", value) or value.lower().startswith(("http://", "https://", "env.", "process.", "config.", "settings.", "self.", "this.")):
                    continue
                if re.fullmatch(r"[A-Za-z_][\w.]*", value) and not re.search(r"\d", value) and len(value) < 24:
                    continue  # an identifier (password = user_password), not a literal
                out.append({"severity": "critical", "rule": "secret-committed", "message": f"Literal secret committed for {match.group('key')} ({value[:2]}••••)",
                            "path": fd.path, "line": line_no, "key": match.group("key"), "environment": environment_of(fd.path)})
    return out[:50]


def _dockerfile_rules(fd: FileDiff) -> list[dict]:
    out = []
    for hunk in fd.hunks:
        added = [t for _, t in hunk.added]
        removed = [t for _, t in hunk.removed]
        for text in added:
            stripped = text.strip()
            if re.match(r"(?i)^FROM\s+", stripped):
                image = stripped.split()[1] if len(stripped.split()) > 1 else ""
                old = next((r.strip().split()[1] for r in removed if re.match(r"(?i)^FROM\s+\S+", r.strip())), "")
                sev = "medium" if image.endswith(":latest") or ":" not in image else "low"
                out.append({"severity": sev, "rule": "base-image", "message": f"Base image {old + ' → ' if old else ''}{image}" + (" (mutable tag)" if sev == "medium" else ""), "path": fd.path})
            if re.match(r"(?i)^USER\s+(root|0)\b", stripped):
                out.append({"severity": "high", "rule": "root-container", "message": "Container switches to USER root", "path": fd.path})
            if re.match(r"(?i)^ADD\s+https?://", stripped):
                out.append({"severity": "medium", "rule": "remote-add", "message": "ADD downloads from a URL at build time (no checksum)", "path": fd.path})
            if re.match(r"(?i)^(EXPOSE|ENTRYPOINT|CMD)\b", stripped):
                out.append({"severity": "low", "rule": "runtime", "message": f"Runtime changed: {stripped[:120]}", "path": fd.path})
        for text in removed:
            if re.match(r"(?i)^USER\s+(?!root|0)\S+", text.strip()):
                out.append({"severity": "high", "rule": "root-container", "message": f"Non-root USER removed ({text.strip()})", "path": fd.path})
            if re.match(r"(?i)^HEALTHCHECK\b", text.strip()):
                out.append({"severity": "medium", "rule": "probe-removed", "message": "HEALTHCHECK removed", "path": fd.path})
    return out


SENSITIVE_TF = re.compile(r"(security_group|firewall|iam|policy|role|kms|key_vault|secret|network_acl|nsg|public_access|bucket_policy|_acl|waf|certificate|route53|dns)", re.I)


def _terraform_rules(fd: FileDiff, text: str) -> list[dict]:
    blocks = [(m.start(), m.group(1), m.group(2), m.group(3)) for m in re.finditer(r'^\s*(resource|data|module)\s+"([\w-]+)"\s*(?:"([\w-]+)")?\s*\{', text, re.M)]
    starts = [(text.count("\n", 0, pos) + 1, kind, rtype, name) for pos, kind, rtype, name in blocks]
    touched: dict[str, dict] = {}
    lines = [n for h in fd.hunks for n, _ in (h.added if fd.status != "D" else h.removed)] or [h.new_start for h in fd.hunks]
    for line in lines:
        owner = None
        for start, kind, rtype, name in starts:
            if start <= line:
                owner = (kind, rtype, name)
        if owner:
            ident = f"{owner[0]}.{owner[1]}.{owner[2] or ''}".rstrip(".")
            entry = touched.setdefault(ident, {"kind": owner[0], "type": owner[1], "name": owner[2] or "", "lines": 0})
            entry["lines"] += 1
    out = []
    for ident, entry in touched.items():
        severity = "high" if SENSITIVE_TF.search(entry["type"]) else "medium" if entry["kind"] == "resource" else "low"
        out.append({"severity": severity, "rule": "terraform-resource", "message": f"{entry['kind']} {entry['type']}.{entry['name']} changed ({entry['lines']} line(s))".replace(". ", " "), "path": fd.path})
    for hunk in fd.hunks:
        for _, added in hunk.added:
            if "0.0.0.0/0" in added or "::/0" in added:
                out.append({"severity": "high", "rule": "open-cidr", "message": "Opens access to the whole internet (0.0.0.0/0)", "path": fd.path})
            if re.search(r"(publicly_accessible|public_network_access_enabled|block_public_acls)\s*=\s*(true|false)", added):
                out.append({"severity": "high", "rule": "public-access", "message": f"Public access setting changed: {added.strip()[:120]}", "path": fd.path})
    if fd.status in ("A", "D") and not touched:
        out.append({"severity": "medium", "rule": "terraform-file", "message": f"Terraform file {'added' if fd.status == 'A' else 'removed'}", "path": fd.path})
    return out


def _ci_rules(fd: FileDiff) -> list[dict]:
    out = []
    gate = re.compile(r"(\btest\b|pytest|jest|vitest|mvn\s+(verify|test)|gradle\w*\s+(test|check)|go\s+test|npm\s+(run\s+)?test|lint|eslint|ruff|sonar|codeql|scan|audit|snyk|trivy|coverage)", re.I)
    bypass = re.compile(r"(continue-on-error:\s*true|\|\|\s*true|--no-verify|-DskipTests|skipTests|-Dmaven\.test\.skip|skip[_-]?tests?|allow_failure:\s*true|if:\s*false|when:\s*manual|exit\s+0)", re.I)
    deploy = re.compile(r"(environment:|deploy|branches:|tags:|on:\s*$|workflow_dispatch|kubectl|helm\s+upgrade|terraform\s+apply)", re.I)
    for hunk in fd.hunks:
        for _, text in hunk.removed:
            if gate.search(text) and not text.strip().startswith("#"):
                out.append({"severity": "medium", "rule": "quality-gate-removed", "message": f"Quality gate removed: {text.strip()[:120]}", "path": fd.path})
        for _, text in hunk.added:
            if bypass.search(text) and not text.strip().startswith("#"):
                out.append({"severity": "medium", "rule": "gate-bypass", "message": f"Failure is ignored or tests skipped: {text.strip()[:120]}", "path": fd.path})
            elif deploy.search(text):
                out.append({"severity": "low", "rule": "deploy-change", "message": f"Deployment trigger or target changed: {text.strip()[:120]}", "path": fd.path})
    if fd.status == "D":
        out.append({"severity": "medium", "rule": "pipeline-removed", "message": "CI pipeline file removed", "path": fd.path})
    return out[:40]


def read_text(root: Path | None, path: str, limit: int = 2_000_000) -> str:
    if root is None:
        return ""
    target = (root / path)
    try:
        if target.is_file() and target.stat().st_size <= limit:
            return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""
