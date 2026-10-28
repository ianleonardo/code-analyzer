"""Configuration keys (shared layer): Spring application*.properties / *.yml (with profiles), .env files, JSON settings,
Android Gradle buildConfigField / manifest placeholders, iOS Info.plist and .xcconfig — plus where each key is used.
Secrets are masked; ${ENV:default} placeholders are kept so the environment variable stays visible.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

import yaml

from app.services.analyzer.catalog.model import Catalog, Context, Item

SECRET = re.compile(r"(password|passwd|pwd|(?<![a-z])pass(?![a-z])|secret|token|api[-_.]?key|credential|private[-_.]?key|client[-_.]?secret|access[-_.]?key|signing[-_.]?key|salt)", re.I)
MAX_KEYS = 900
SKIP_JSON = {"package.json", "package-lock.json", "tsconfig.json", "jsconfig.json", "composer.json", "angular.json", ".eslintrc.json", ".babelrc", ".prettierrc",
             "tslint.json", "lerna.json", "nx.json", "project.json", "turbo.json", "renovate.json", "vercel.json", "manifest.json", "components.json",
             "contents.json", "settings.json", "launch.json", "tasks.json", "extensions.json", "openapi.json", "swagger.json", "firebase.json", "google-services.json"}


def mask(key: str, value: str) -> tuple[str, bool]:
    value = "" if value is None else str(value)
    secret = bool(SECRET.search(key))
    if re.search(r"://[^/:@\s]+:[^@\s]+@", value):
        return re.sub(r"(://[^/:@\s]+:)[^@\s]+@", r"\1••••@", value)[:300], True
    if secret and value and not re.fullmatch(r"\$\{[^}]+\}", value.strip()):
        placeholder = re.search(r"\$\{([^}:]+)", value)
        return (f"${{{placeholder.group(1)}:••••}}" if placeholder else "••••••"), True
    return value[:300], secret


def category(key: str) -> str:
    k = key.lower()
    if k.startswith(("ocp.route.", "k8s.ingress.")):
        return "route / ingress"
    if k.startswith(("ocp.", "k8s.", "openshift.", "kubernetes.", "helm.")):
        return "openshift / k8s"
    if k.startswith(("docker.", "compose.", "container.")):
        return "container"
    for prefix, name in (("spring.datasource", "datasource"), ("spring.r2dbc", "datasource"), ("spring.jpa", "persistence"), ("hibernate", "persistence"),
                         ("spring.flyway", "migrations"), ("spring.liquibase", "migrations"), ("spring.sql", "persistence"), ("server.", "server"),
                         ("logging.", "logging"), ("spring.security", "security"), ("jwt", "security"), ("oauth", "security"), ("spring.kafka", "messaging"),
                         ("spring.rabbitmq", "messaging"), ("spring.jms", "messaging"), ("spring.cloud.stream", "messaging"), ("management.", "monitoring"),
                         ("spring.mail", "mail"), ("spring.cache", "cache"), ("spring.redis", "cache"), ("spring.data.redis", "cache"), ("spring.profiles", "profiles"),
                         ("spring.application", "application"), ("spring.task", "scheduling"), ("spring.batch", "batch"), ("spring.quartz", "scheduling"),
                         ("feign", "integration"), ("spring.cloud", "cloud"), ("eureka", "cloud")):
        if k.startswith(prefix):
            return name
    if re.search(r"(url|uri|host|endpoint|base)", k):
        return "integration"
    return "application"


def _profile(path: str) -> str:
    name = PurePosixPath(path).name
    m = re.match(r"^(?:application|bootstrap)-([\w.-]+)\.(?:properties|ya?ml)$", name)
    if m:
        return m.group(1)
    m = re.match(r"^\.env\.([\w.-]+)$", name)
    if m:
        return m.group(1)
    m = re.match(r"^appsettings\.([\w.-]+)\.json$", name, re.I)
    if m:
        return m.group(1)
    m = re.match(r"^values[-.]([\w.-]+)\.ya?ml$", name, re.I)
    if m:
        return m.group(1)
    parts = [p.lower() for p in PurePosixPath(path).parts]
    for env_name in ("prod", "production", "staging", "stage", "uat", "qa", "dev", "development", "sit", "dr", "ocp", "openshift"):
        if env_name in parts:
            return env_name
    return "default"


def parse_properties(text: str) -> list[tuple[str, str, int, str]]:
    out = []
    comment: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        lineno = i + 1
        i += 1
        if not line:
            comment = []
            continue
        if line.startswith(("#", "!")):
            comment.append(line.lstrip("#! ").strip())
            continue
        while line.endswith("\\") and i < len(lines):
            line = line[:-1] + lines[i].strip()
            i += 1
        m = re.match(r"^([^=:\s]+)\s*[=:\s]\s*(.*)$", line)
        if m:
            out.append((m.group(1), m.group(2).strip(), lineno, " ".join(comment)[:300]))
        comment = []
    return out


def parse_yaml(text: str) -> list[tuple[str, str, int, str, str]]:
    """(key, value, line, comment, profile) for each scalar leaf, across '---' documents."""
    out = []
    lines = text.splitlines()
    try:
        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        return out
    for doc in documents:
        if doc is None:
            continue
        leaves: list[tuple[str, str, int]] = []

        def walk(node, prefix: str) -> None:
            if isinstance(node, yaml.MappingNode):
                for key_node, value_node in node.value:
                    key = str(key_node.value)
                    walk(value_node, f"{prefix}.{key}" if prefix else key)
            elif isinstance(node, yaml.SequenceNode):
                if all(isinstance(v, yaml.ScalarNode) for v in node.value):
                    leaves.append((prefix, ", ".join(str(v.value) for v in node.value)[:300], node.start_mark.line + 1))
                else:
                    for index, value in enumerate(node.value[:20]):
                        walk(value, f"{prefix}[{index}]")
            elif isinstance(node, yaml.ScalarNode):
                leaves.append((prefix, str(node.value), node.start_mark.line + 1))

        walk(doc, "")
        profile = next((v for k, v, _ in leaves if k in ("spring.config.activate.on-profile", "spring.profiles")), "")
        for key, value, line in leaves:
            comment = ""
            if 1 < line <= len(lines):
                prev = lines[line - 2].strip()
                if prev.startswith("#"):
                    comment = prev.lstrip("# ").strip()
                inline = re.search(r"\s#\s*(.+)$", lines[line - 1])
                if inline and not comment:
                    comment = inline.group(1).strip()
            out.append((key, value, line, comment[:300], profile))
    return out


def _flatten_json(value, prefix: str = "", out: list | None = None, depth: int = 0) -> list[tuple[str, str]]:
    out = out if out is not None else []
    if depth > 6 or len(out) > 200:
        return out
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten_json(child, f"{prefix}.{key}" if prefix else str(key), out, depth + 1)
    elif isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            out.append((prefix, ", ".join(str(v) for v in value)[:300]))
        else:
            for index, child in enumerate(value[:10]):
                _flatten_json(child, f"{prefix}[{index}]", out, depth + 1)
    else:
        out.append((prefix, "" if value is None else str(value)))
    return out


def _is_spring_config(path: str) -> bool:
    name = PurePosixPath(path).name
    return bool(re.match(r"^(application|bootstrap)(-[\w.-]+)?\.(properties|ya?ml)$", name))


_OCP_K8S_KINDS = {
    "DeploymentConfig", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "ReplicationController",
    "Job", "CronJob", "Pod", "ConfigMap", "Secret", "Route", "Ingress", "Service", "BuildConfig",
    "ImageStream", "Template", "List", "HorizontalPodAutoscaler", "PersistentVolumeClaim",
    "ServiceAccount", "Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding", "NetworkPolicy",
}
_OCP_DIR_RE = re.compile(r"(^|/)(ocp|openshift|k8s|kubernetes|helm|charts|deploy|deployments|manifests|kustomize|overlays|base)/", re.I)


def _line_of_key(lines: list[str], needle: str, default: int = 1) -> int:
    if not needle:
        return default
    pat = re.compile(rf"^\s*-?\s*(?:name:\s*['\"]?{re.escape(needle)}['\"]?|['\"]?{re.escape(needle)}['\"]?\s*:)")
    for idx, line in enumerate(lines, start=1):
        if pat.search(line):
            return idx
    for idx, line in enumerate(lines, start=1):
        if needle in line:
            return idx
    return default


def _parse_ocp_k8s_doc(doc: dict, lines: list[str], path: str, profile: str, manifests: list[dict]) -> list[tuple[str, str, int, str, str]]:
    """Extracts configuration keys and manifest metadata from an OpenShift (OCP) or Kubernetes YAML document."""
    found: list[tuple[str, str, int, str, str]] = []
    if not isinstance(doc, dict):
        return found
    api_version = str(doc.get("apiVersion") or "")
    kind = str(doc.get("kind") or "")
    if not kind or (kind not in _OCP_K8S_KINDS and "/" not in api_version):
        return found
    meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    res_name = str(meta.get("name") or PurePosixPath(path).stem)
    namespace = str(meta.get("namespace") or "")
    is_ocp = "openshift.io" in api_version or kind in ("DeploymentConfig", "Route", "BuildConfig", "ImageStream", "Template")
    platform = "OpenShift (OCP)" if is_ocp else "Kubernetes"
    prefix_ns = "ocp" if is_ocp else "k8s"
    line_start = _line_of_key(lines, res_name, 1)

    if kind == "Template":
        params = doc.get("parameters") if isinstance(doc.get("parameters"), list) else []
        param_summary = []
        for p in params:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            pname = str(p["name"])
            pval = str(p.get("value") if p.get("value") is not None else p.get("generate") or "")
            pdesc = str(p.get("displayName") or p.get("description") or "OpenShift Template parameter")
            pline = _line_of_key(lines, pname, line_start)
            found.append((pname, pval, pline, pdesc, profile or "ocp-template"))
            param_summary.append({"name": pname, "value": pval, "required": bool(p.get("required")), "description": pdesc})
        objects = doc.get("objects") if isinstance(doc.get("objects"), list) else []
        manifests.append({
            "kind": "Template", "name": res_name, "platform": "OpenShift (OCP)", "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace,
            "details": {"parameters": param_summary, "objects_count": len(objects), "objects": [f"{o.get('kind')}/{((o.get('metadata') or {}).get('name') or '')}" for o in objects if isinstance(o, dict)]},
        })
        for obj in objects:
            if isinstance(obj, dict):
                found.extend(_parse_ocp_k8s_doc(obj, lines, path, profile or "ocp", manifests))
        return found

    if kind == "List":
        for item in (doc.get("items") if isinstance(doc.get("items"), list) else []):
            if isinstance(item, dict):
                found.extend(_parse_ocp_k8s_doc(item, lines, path, profile, manifests))
        return found

    if kind == "ConfigMap":
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        keys_extracted = []
        for k, v in data.items():
            k_str = str(k)
            v_str = "" if v is None else str(v)
            k_line = _line_of_key(lines, k_str, line_start)
            keys_extracted.append(k_str)
            if k_str.endswith(".properties") or k_str.endswith(".env"):
                for pk, pv, pl, pc in parse_properties(v_str):
                    found.append((pk, pv, k_line + pl - 1, pc or f"ConfigMap {res_name}/{k_str}", profile or "ocp"))
            elif k_str.endswith((".yml", ".yaml")):
                for yk, yv, yl, yc, yp in parse_yaml(v_str):
                    found.append((yk, yv, k_line + yl - 1, yc or f"ConfigMap {res_name}/{k_str}", yp or profile or "ocp"))
            else:
                found.append((k_str, v_str.strip(), k_line, f"{platform} ConfigMap {res_name}", profile or "ocp"))
        manifests.append({
            "kind": "ConfigMap", "name": res_name, "platform": platform, "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace, "details": {"keys": keys_extracted},
        })
        return found

    if kind == "Secret":
        sdata = {}
        if isinstance(doc.get("stringData"), dict):
            sdata.update(doc["stringData"])
        if isinstance(doc.get("data"), dict):
            sdata.update(doc["data"])
        keys_extracted = []
        for k in sdata:
            k_str = str(k)
            keys_extracted.append(k_str)
            found.append((k_str, "••••••", _line_of_key(lines, k_str, line_start), f"{platform} Secret {res_name}", profile or "ocp"))
        manifests.append({
            "kind": "Secret", "name": res_name, "platform": platform, "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace, "details": {"keys": keys_extracted, "type": str(doc.get("type") or "Opaque")},
        })
        return found

    spec = doc.get("spec") if isinstance(doc.get("spec"), dict) else {}

    if kind == "Route":
        host = str(spec.get("host") or "")
        route_path = str(spec.get("path") or "/")
        to_target = spec.get("to") if isinstance(spec.get("to"), dict) else {}
        target_svc = str(to_target.get("name") or "")
        port_spec = spec.get("port") if isinstance(spec.get("port"), dict) else {}
        target_port = str(port_spec.get("targetPort") or "")
        tls_spec = spec.get("tls") if isinstance(spec.get("tls"), dict) else {}
        tls_term = str(tls_spec.get("termination") or "")
        if host:
            found.append((f"ocp.route.{res_name}.host", host, _line_of_key(lines, "host", line_start), f"OpenShift Route {res_name} host", profile or "ocp"))
        if target_svc:
            found.append((f"ocp.route.{res_name}.service", f"{target_svc}{':' + target_port if target_port else ''} ({route_path})", line_start, f"OpenShift Route {res_name} target", profile or "ocp"))
        if tls_term:
            found.append((f"ocp.route.{res_name}.tls", tls_term, _line_of_key(lines, "termination", line_start), f"OpenShift Route {res_name} TLS", profile or "ocp"))
        manifests.append({
            "kind": "Route", "name": res_name, "platform": "OpenShift (OCP)", "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace,
            "details": {"host": host, "path": route_path, "target_service": target_svc, "target_port": target_port, "tls": tls_term},
        })
        return found

    if kind == "Ingress":
        rules = spec.get("rules") if isinstance(spec.get("rules"), list) else []
        hosts = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            h = str(rule.get("host") or "*")
            hosts.append(h)
            found.append((f"k8s.ingress.{res_name}.host", h, _line_of_key(lines, h, line_start), f"Kubernetes Ingress {res_name}", profile or "k8s"))
        manifests.append({
            "kind": "Ingress", "name": res_name, "platform": platform, "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace, "details": {"hosts": hosts},
        })
        return found

    if kind == "BuildConfig":
        strategy = spec.get("strategy") if isinstance(spec.get("strategy"), dict) else {}
        strat_type = str(strategy.get("type") or next((k for k in strategy if k.endswith("Strategy")), ""))
        source_spec = spec.get("source") if isinstance(spec.get("source"), dict) else {}
        git_uri = str(((source_spec.get("git") or {}) if isinstance(source_spec.get("git"), dict) else {}).get("uri") or "")
        output_to = str((((spec.get("output") or {}) if isinstance(spec.get("output"), dict) else {}).get("to") or {}).get("name") or "")
        found.append((f"ocp.buildconfig.{res_name}.strategy", strat_type, line_start, f"OpenShift BuildConfig {res_name}", profile or "ocp"))
        if output_to:
            found.append((f"ocp.buildconfig.{res_name}.output", output_to, line_start, f"OpenShift BuildConfig {res_name} output", profile or "ocp"))
        for strat_key in ("sourceStrategy", "dockerStrategy", "customStrategy", "jenkinsPipelineStrategy"):
            strat_obj = strategy.get(strat_key)
            if isinstance(strat_obj, dict):
                for env_item in (strat_obj.get("env") if isinstance(strat_obj.get("env"), list) else []):
                    if isinstance(env_item, dict) and env_item.get("name"):
                        ename = str(env_item["name"])
                        eval_str = str(env_item.get("value") or "")
                        found.append((ename, eval_str, _line_of_key(lines, ename, line_start), f"OpenShift BuildConfig {res_name} env", profile or "ocp"))
        manifests.append({
            "kind": "BuildConfig", "name": res_name, "platform": "OpenShift (OCP)", "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace,
            "details": {"strategy": strat_type, "git_uri": git_uri, "output": output_to},
        })
        return found

    if kind == "ImageStream":
        repo = str(spec.get("dockerImageRepository") or "")
        tags = [str(t.get("name")) for t in (spec.get("tags") if isinstance(spec.get("tags"), list) else []) if isinstance(t, dict) and t.get("name")]
        if repo or tags:
            found.append((f"ocp.imagestream.{res_name}.tags", ", ".join(tags) or repo, line_start, f"OpenShift ImageStream {res_name}", profile or "ocp"))
        manifests.append({
            "kind": "ImageStream", "name": res_name, "platform": "OpenShift (OCP)", "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace, "details": {"repository": repo, "tags": tags},
        })
        return found

    if kind == "Service":
        ports = []
        for p in (spec.get("ports") if isinstance(spec.get("ports"), list) else []):
            if isinstance(p, dict):
                ports.append(f"{p.get('port')}->{p.get('targetPort', p.get('port'))}/{p.get('protocol', 'TCP')}")
        if ports:
            found.append((f"{prefix_ns}.service.{res_name}.ports", ", ".join(ports), line_start, f"{platform} Service {res_name}", profile or prefix_ns))
        manifests.append({
            "kind": "Service", "name": res_name, "platform": platform, "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace,
            "details": {"type": str(spec.get("type") or "ClusterIP"), "ports": ports, "selector": spec.get("selector") or {}},
        })
        return found

    if kind == "HorizontalPodAutoscaler":
        min_r = spec.get("minReplicas")
        max_r = spec.get("maxReplicas")
        found.append((f"{prefix_ns}.hpa.{res_name}.replicas", f"min={min_r}, max={max_r}", line_start, f"{platform} HPA {res_name}", profile or prefix_ns))
        manifests.append({
            "kind": "HorizontalPodAutoscaler", "name": res_name, "platform": platform, "path": path, "line": line_start,
            "api_version": api_version, "namespace": namespace, "details": {"min_replicas": min_r, "max_replicas": max_r},
        })
        return found

    # Workloads: DeploymentConfig, Deployment, StatefulSet, DaemonSet, Job, CronJob, Pod
    replicas = spec.get("replicas")
    if replicas is not None:
        found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.replicas", str(replicas), _line_of_key(lines, "replicas", line_start), f"{platform} {kind} {res_name}", profile or prefix_ns))
    strategy_obj = spec.get("strategy") if isinstance(spec.get("strategy"), dict) else {}
    strat_name = str(strategy_obj.get("type") or "")
    if strat_name:
        found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.strategy", strat_name, _line_of_key(lines, "strategy", line_start), f"{platform} {kind} {res_name} strategy", profile or prefix_ns))

    pod_spec = spec
    if isinstance(spec.get("template"), dict) and isinstance(spec["template"].get("spec"), dict):
        pod_spec = spec["template"]["spec"]
    elif isinstance(spec.get("jobTemplate"), dict):
        jt_spec = spec["jobTemplate"].get("spec") if isinstance(spec["jobTemplate"].get("spec"), dict) else {}
        if isinstance(jt_spec.get("template"), dict) and isinstance(jt_spec["template"].get("spec"), dict):
            pod_spec = jt_spec["template"]["spec"]

    containers = pod_spec.get("containers") if isinstance(pod_spec.get("containers"), list) else []
    images = []
    env_names = []
    config_refs = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        cname = str(c.get("name") or res_name)
        image = str(c.get("image") or "")
        if image:
            images.append(image)
            found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.image", image, _line_of_key(lines, "image", line_start), f"{platform} {kind} {res_name} ({cname})", profile or prefix_ns))
        resources = c.get("resources") if isinstance(c.get("resources"), dict) else {}
        for rkind in ("requests", "limits"):
            rmap = resources.get(rkind) if isinstance(resources.get(rkind), dict) else {}
            if rmap:
                summary_res = ", ".join(f"{rk}={rv}" for rk, rv in rmap.items())
                found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.resources.{rkind}", summary_res, _line_of_key(lines, rkind, line_start), f"{platform} {kind} {res_name} {rkind}", profile or prefix_ns))
        for probe_name in ("readinessProbe", "livenessProbe", "startupProbe"):
            probe = c.get(probe_name) if isinstance(c.get(probe_name), dict) else {}
            if probe:
                http_get = probe.get("httpGet") if isinstance(probe.get("httpGet"), dict) else {}
                probe_desc = f"HTTP {http_get.get('path', '/')} :{http_get.get('port', '')}" if http_get else ("tcpSocket" if "tcpSocket" in probe else "exec")
                found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.{probe_name}", probe_desc, _line_of_key(lines, probe_name, line_start), f"{platform} {kind} {res_name}", profile or prefix_ns))
        for env_from in (c.get("envFrom") if isinstance(c.get("envFrom"), list) else []):
            if not isinstance(env_from, dict):
                continue
            cm_ref = (env_from.get("configMapRef") or {}).get("name") if isinstance(env_from.get("configMapRef"), dict) else ""
            sec_ref = (env_from.get("secretRef") or {}).get("name") if isinstance(env_from.get("secretRef"), dict) else ""
            if cm_ref:
                config_refs.append(f"ConfigMap:{cm_ref}")
                found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.envFrom.configMap", str(cm_ref), _line_of_key(lines, str(cm_ref), line_start), f"{platform} {kind} {res_name} envFrom", profile or prefix_ns))
            if sec_ref:
                config_refs.append(f"Secret:{sec_ref}")
                found.append((f"{prefix_ns}.{kind.lower()}.{res_name}.envFrom.secret", str(sec_ref), _line_of_key(lines, str(sec_ref), line_start), f"{platform} {kind} {res_name} envFrom", profile or prefix_ns))
        for env_item in (c.get("env") if isinstance(c.get("env"), list) else []):
            if not isinstance(env_item, dict) or not env_item.get("name"):
                continue
            ename = str(env_item["name"])
            env_names.append(ename)
            eline = _line_of_key(lines, ename, line_start)
            if "value" in env_item and env_item["value"] is not None:
                found.append((ename, str(env_item["value"]), eline, f"{platform} {kind} {res_name} env", profile or prefix_ns))
            elif isinstance(env_item.get("valueFrom"), dict):
                vf = env_item["valueFrom"]
                if isinstance(vf.get("configMapKeyRef"), dict):
                    cm_name = vf["configMapKeyRef"].get("name", "")
                    cm_key = vf["configMapKeyRef"].get("key", "")
                    config_refs.append(f"ConfigMap:{cm_name}")
                    found.append((ename, f"${{configMap:{cm_name}.{cm_key}}}", eline, f"{platform} {kind} {res_name} from ConfigMap {cm_name}", profile or prefix_ns))
                elif isinstance(vf.get("secretKeyRef"), dict):
                    s_name = vf["secretKeyRef"].get("name", "")
                    s_key = vf["secretKeyRef"].get("key", "")
                    config_refs.append(f"Secret:{s_name}")
                    found.append((ename, f"${{secret:{s_name}.{s_key}}}", eline, f"{platform} {kind} {res_name} from Secret {s_name}", profile or prefix_ns))
                elif isinstance(vf.get("fieldRef"), dict):
                    found.append((ename, f"${{fieldRef:{vf['fieldRef'].get('fieldPath', '')}}}", eline, f"{platform} {kind} {res_name} fieldRef", profile or prefix_ns))

    manifests.append({
        "kind": kind, "name": res_name, "platform": platform, "path": path, "line": line_start,
        "api_version": api_version, "namespace": namespace,
        "details": {"replicas": replicas, "strategy": strat_name, "images": images, "env": env_names[:40], "config_refs": sorted(set(config_refs))},
    })
    return found


def _parse_dockerfile(text: str) -> list[tuple[str, str, int, str, str]]:
    found: list[tuple[str, str, int, str, str]] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m_env = re.match(r"^(?:ENV|ARG)\s+([A-Za-z_][A-Za-z0-9_]*)(?:=(.*)|\s+(.*))?$", line, re.I)
        if m_env:
            key = m_env.group(1)
            val = (m_env.group(2) if m_env.group(2) is not None else (m_env.group(3) or "")).strip().strip('"\'')
            found.append((key, val, idx, "Dockerfile ENV/ARG", "docker"))
            continue
        m_exp = re.match(r"^EXPOSE\s+(.+)$", line, re.I)
        if m_exp:
            found.append(("docker.expose.ports", m_exp.group(1).strip(), idx, "Dockerfile EXPOSE", "docker"))
    return found


def collect(ctx: Context) -> list[dict]:
    """Every configuration entry: {key, value, raw, line, path, profile, source, comment}."""
    entries: list[dict] = []
    ocp_manifests: list[dict] = []
    for file in ctx.files:
        if not file.text or (file.is_test and not _is_spring_config(file.path)):
            continue
        name = file.name
        path = file.path
        source = ""
        found: list[tuple[str, str, int, str, str]] = []
        if _is_spring_config(path):
            source = "Spring Boot"
            if name.endswith(".properties"):
                found = [(k, v, line, c, "") for k, v, line, c in parse_properties(file.text)]
            else:
                found = parse_yaml(file.text)
        elif name == ".env" or name.startswith(".env.") or name.endswith(".env"):
            source = "Environment file"
            found = [(k, v.strip('"\''), line, c, "") for k, v, line, c in parse_properties(file.text) if re.match(r"^[A-Za-z_][\w.]*$", k)]
        elif name == "gradle.properties":
            source = "Gradle properties"
            found = [(k, v, line, c, "") for k, v, line, c in parse_properties(file.text) if not k.startswith(("org.gradle", "android.", "kotlin."))]
        elif name.endswith(".xcconfig"):
            source = "Xcode config"
            found = [(m.group(1), m.group(2).strip(), file.text.count("\n", 0, m.start()) + 1, "", "") for m in re.finditer(r"^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", file.text, re.M)]
        elif name == "Info.plist":
            source = "iOS Info.plist"
            for m in re.finditer(r"<key>([^<]+)</key>\s*<(string|true|false|integer)\s*/?>([^<]*)", file.text):
                value = m.group(3) if m.group(2) in ("string", "integer") else m.group(2)
                found.append((m.group(1), value, file.text.count("\n", 0, m.start()) + 1, "", ""))
        elif name in ("build.gradle", "build.gradle.kts"):
            source = "Android BuildConfig"
            for m in re.finditer(r"buildConfigField\s*\(?\s*\"(\w+)\"\s*,\s*\"(\w+)\"\s*,\s*\"((?:\\.|[^\"\\])*)\"", file.text):
                found.append((f"BuildConfig.{m.group(2)}", m.group(3).replace('\\"', '"').strip('"'), file.text.count("\n", 0, m.start()) + 1, f"{m.group(1)} field", _flavor_at(file.text, m.start())))
            for m in re.finditer(r"resValue\s*\(?\s*\"(\w+)\"\s*,\s*\"(\w+)\"\s*,\s*\"([^\"]*)\"", file.text):
                found.append((f"R.{m.group(1)}.{m.group(2)}", m.group(3), file.text.count("\n", 0, m.start()) + 1, "resValue", _flavor_at(file.text, m.start())))
            for m in re.finditer(r"manifestPlaceholders\s*(?:\[\s*\"(\w+)\"\s*\]\s*=\s*\"([^\"]*)\"|\+?=\s*\[\s*(\w+)\s*:\s*\"([^\"]*)\")", file.text):
                found.append((f"manifest.{m.group(1) or m.group(3)}", m.group(2) or m.group(4), file.text.count("\n", 0, m.start()) + 1, "manifest placeholder", ""))
        elif name == "Dockerfile" or name.startswith("Dockerfile.") or name == "Containerfile":
            source = "Dockerfile"
            found = _parse_dockerfile(file.text)
        elif name.endswith((".yml", ".yaml")) and len(file.text) < 400_000:
            lines = file.text.splitlines()
            if re.match(r"^(docker-)?compose([-.][\w.-]+)?\.ya?ml$", name, re.I):
                source = "Docker Compose"
                try:
                    doc = yaml.safe_load(file.text)
                except yaml.YAMLError:
                    doc = None
                if isinstance(doc, dict) and isinstance(doc.get("services"), dict):
                    for svc_name, svc in doc["services"].items():
                        if not isinstance(svc, dict):
                            continue
                        if svc.get("image"):
                            found.append((f"compose.{svc_name}.image", str(svc["image"]), _line_of_key(lines, str(svc["image"])), f"Compose service {svc_name}", "compose"))
                        env_block = svc.get("environment")
                        if isinstance(env_block, dict):
                            for ek, ev in env_block.items():
                                found.append((str(ek), "" if ev is None else str(ev), _line_of_key(lines, str(ek)), f"Compose {svc_name} env", "compose"))
                        elif isinstance(env_block, list):
                            for item in env_block:
                                if isinstance(item, str) and "=" in item:
                                    ek, _, ev = item.partition("=")
                                    found.append((ek.strip(), ev.strip(), _line_of_key(lines, ek.strip()), f"Compose {svc_name} env", "compose"))
            elif name.lower() in ("cloudbuild.yaml", "cloudbuild.yml"):
                source = "Cloud Build"
                try:
                    doc = yaml.safe_load(file.text)
                except yaml.YAMLError:
                    doc = None
                if isinstance(doc, dict) and isinstance(doc.get("substitutions"), dict):
                    for sk, sv in doc["substitutions"].items():
                        found.append((str(sk), "" if sv is None else str(sv), _line_of_key(lines, str(sk)), "Cloud Build substitution", "cloudbuild"))
            elif "apiVersion:" in file.text and "kind:" in file.text:
                try:
                    docs = list(yaml.safe_load_all(file.text))
                except yaml.YAMLError:
                    docs = []
                prof = _profile(path)
                for doc in docs:
                    if isinstance(doc, dict) and doc.get("kind"):
                        doc_found = _parse_ocp_k8s_doc(doc, lines, path, prof, ocp_manifests)
                        if doc_found:
                            is_ocp_doc = "openshift.io" in str(doc.get("apiVersion") or "") or doc.get("kind") in ("DeploymentConfig", "Route", "BuildConfig", "ImageStream", "Template")
                            source = source or ("OpenShift (OCP)" if is_ocp_doc else "Kubernetes")
                            found.extend(doc_found)
            elif re.match(r"^values([-.][\w.-]+)?\.ya?ml$", name, re.I) or (_OCP_DIR_RE.search(f"/{path}") and not path.startswith(".github/")):
                source = "OpenShift / Helm values" if ("ocp" in path.lower() or "openshift" in path.lower()) else "Helm / Deployment values"
                found = [(k, v, ln, c, p or _profile(path)) for k, v, ln, c, p in parse_yaml(file.text) if k and not k.startswith("apiVersion")]
        elif name.endswith(".json") and name.lower() not in SKIP_JSON and file.category == "config" and (
            re.match(r"^appsettings(\.[\w-]+)?\.json$", name, re.I) or "/config/" in f"/{path}" or re.search(r"(config|settings|env)[\w.-]*\.json$", name, re.I)
        ) and len(file.text) < 300_000:
            source = "JSON settings"
            try:
                data = json.loads(file.text)
            except ValueError:
                data = None
            if isinstance(data, dict):
                lines = file.text.splitlines()
                for key, value in _flatten_json(data):
                    leaf = key.split(".")[-1].split("[")[0]
                    line = next((i + 1 for i, text in enumerate(lines) if f'"{leaf}"' in text), 0)
                    found.append((key, value, line, "", ""))
        if not found:
            continue
        profile_from_name = _profile(path)
        for key, value, line, comment, profile in found:
            if not key:
                continue
            entries.append({"key": key, "raw": value, "line": line, "path": path, "profile": profile or profile_from_name, "source": source, "comment": comment})
    ctx.ocp_manifests = ocp_manifests  # type: ignore[attr-defined]
    return entries


def _flavor_at(text: str, index: int) -> str:
    before = text[:index]
    m = list(re.finditer(r"\b(debug|release|staging|production|prod|dev|qa|uat)\s*\{", before))
    return m[-1].group(1) if m else ""


_USAGES = [
    (re.compile(r"@Value\s*\(\s*\"\\?\$\{([^}:\"]+)(?::[^}\"]*)?\}"), "Spring @Value"),
    (re.compile(r"(?:environment|env|Environment)\s*\.\s*(?:getProperty|getRequiredProperty)\s*\(\s*\"([\w.\-\[\]]+)\""), "Environment.getProperty"),
    (re.compile(r"System\s*\.\s*getenv\s*\(\s*\"(\w+)\""), "System.getenv"),
    (re.compile(r"process\.env\.([A-Za-z_]\w*)|process\.env\[\s*['\"]([A-Za-z_]\w*)['\"]\s*\]"), "process.env"),
    (re.compile(r"import\.meta\.env\.([A-Za-z_]\w*)"), "import.meta.env"),
    (re.compile(r"\bBuildConfig\.([A-Z_][A-Z0-9_]*)"), "BuildConfig"),
    (re.compile(r"forInfoDictionaryKey:\s*\"(\w+)\"|infoDictionary\?\s*\[\s*\"(\w+)\"\s*\]"), "Info.plist"),
    (re.compile(r"os\.environ(?:\.get)?\s*[\[(]\s*['\"](\w+)['\"]|os\.getenv\s*\(\s*['\"](\w+)['\"]"), "os.environ"),
]


def prime(ctx: Context) -> list[dict]:
    """Collects configuration and makes default-profile values available for ${placeholder} resolution."""
    entries = collect(ctx)
    ctx.config_entries = entries  # type: ignore[attr-defined]
    for entry in entries:
        if entry["profile"] in ("default", "") or entry["key"] not in ctx.config_values:
            ctx.config_values[entry["key"]] = entry["raw"]
    return entries


def _enclosing_item_key(cat: Catalog, path: str, line: int) -> str:
    best_key = ""
    best_span = 10**9
    for item in cat.items.values():
        if item.path == path and item.kind not in ("config", "manifest") and item.line_start <= line <= max(item.line_end, item.line_start):
            span = max(item.line_end, item.line_start) - item.line_start
            if span < best_span:
                best_span = span
                best_key = item.key
    return best_key


def extract(ctx: Context, cat: Catalog, entries: list[dict]) -> list[dict]:
    usages: dict[str, list[dict]] = {}
    for file in ctx.files:
        if not file.text or file.category not in ("code", "markup", "script") or file.is_test:
            continue
        for pattern, kind in _USAGES:
            for m in pattern.finditer(file.text):
                key = next((g for g in m.groups() if g), "")
                if not key:
                    continue
                if kind == "BuildConfig":
                    key = f"BuildConfig.{key}"
                line_no = file.text.count("\n", 0, m.start()) + 1
                item_key = _enclosing_item_key(cat, file.path, line_no)
                use_entry = {"path": file.path, "line": line_no, "via": kind}
                if item_key:
                    use_entry["item"] = item_key
                usages.setdefault(key, []).append(use_entry)
    for key, path, line, item_key in cat.pending_config_usage:
        usages.setdefault(key, []).append({"path": path, "line": line, "via": "@ConfigurationProperties", "item": item_key})
    grouped: dict[str, dict] = {}
    for entry in entries:
        value, secret = mask(entry["key"], entry["raw"])
        group = grouped.setdefault(entry["key"], {"key": entry["key"], "values": [], "secret": False, "source": entry["source"], "comment": "", "env": [], "path": entry["path"], "line": entry["line"]})
        group["values"].append({"profile": entry["profile"], "value": value, "path": entry["path"], "line": entry["line"]})
        group["secret"] = group["secret"] or secret or entry["raw"] == "••••••" or "Secret " in entry.get("comment", "")
        group["comment"] = group["comment"] or entry["comment"]
        for env in re.findall(r"\$\{([A-Za-z_][\w.]*)", entry["raw"]):
            if env not in group["env"]:
                group["env"].append(env)
    for key, entries_used in usages.items():
        if key not in grouped and (re.match(r"^[A-Z][A-Z0-9_]+$", key) or key.startswith("BuildConfig.")):
            first = entries_used[0]
            grouped[key] = {"key": key, "values": [], "secret": bool(SECRET.search(key)), "source": "Environment variable (read in code)", "comment": "", "env": [key],
                            "path": first["path"], "line": first["line"]}
    ordered = sorted(grouped.values(), key=lambda g: (0 if usages.get(g["key"]) else 1, g["key"]))[:MAX_KEYS]
    for group in ordered:
        used = usages.get(group["key"], [])
        for env in group["env"]:
            used += usages.get(env, [])
        item = cat.add(Item("shared", "config", f"config:{group['key']}", group["key"], group["path"], group["line"], group["line"], "", group["source"],
                            ", ".join(f"{v['profile']}={v['value']}" for v in group["values"][:3])[:300],
                            {"key": group["key"], "values": group["values"][:12], "secret": group["secret"], "env_vars": group["env"], "category": category(group["key"]),
                             "description": group["comment"], "used_by": used[:30]}))
        for use in used[:30]:
            if use.get("item"):
                cat.link(use["item"], item.key, "reads config")

    # Emit OpenShift (OCP) / Kubernetes manifests into Shared -> App manifests and link workloads to ConfigMaps/Secrets
    manifest_items: dict[str, str] = {}
    for mf in getattr(ctx, "ocp_manifests", []) or []:
        m_kind = mf["kind"]
        m_name = mf["name"]
        m_key = f"manifest:{m_kind}:{m_name}"
        src_file = ctx.by_path.get(mf["path"])
        code_snip = "\n".join((src_file.text.splitlines() if src_file and src_file.text else [])[max(0, mf["line"] - 1):mf["line"] + 60])
        sig_parts = [f"{m_kind}/{m_name}"]
        if mf.get("namespace"):
            sig_parts.append(f"ns:{mf['namespace']}")
        det = mf.get("details") or {}
        if det.get("host"):
            sig_parts.append(f"host:{det['host']}")
        if det.get("images"):
            sig_parts.append(f"image:{det['images'][0]}")
        m_item = cat.add(Item("shared", "manifest", m_key, f"{m_kind}: {m_name}", mf["path"], mf["line"], mf["line"] + 40,
                              "YAML", mf["platform"], " · ".join(sig_parts)[:300],
                              {"kind": m_kind, "name": m_name, "api_version": mf.get("api_version", ""), "namespace": mf.get("namespace", ""), **det},
                              code=code_snip))
        manifest_items[f"{m_kind}:{m_name}"] = m_item.key
        for env_key in det.get("env", []):
            if f"config:{env_key}" in cat.items:
                cat.link(m_item.key, f"config:{env_key}", "reads config")
    for mf in getattr(ctx, "ocp_manifests", []) or []:
        src_key = manifest_items.get(f"{mf['kind']}:{mf['name']}")
        det = mf.get("details") or {}
        for ref in det.get("config_refs", []):
            if ref in manifest_items and src_key:
                cat.link(src_key, manifest_items[ref], "uses")
        if mf["kind"] == "Route" and det.get("target_service"):
            svc_key = manifest_items.get(f"Service:{det['target_service']}")
            if svc_key and src_key:
                cat.link(src_key, svc_key, "routes to")
    cat.extra["config_entries"] = entries
    return entries
