"""Python extractors: FastAPI / Flask / Starlette / Litestar endpoints (with APIRouter / Blueprint prefix and
include_router mount resolution), service classes and service modules, Celery / APScheduler jobs, SQLAlchemy /
SQLModel / Django ORM models, embedded SQL DDL and queries, repositories / DAOs, Pydantic / dataclass DTOs, enums,
custom exceptions and outbound HTTP integrations.
"""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from app.services.analyzer.catalog import data as datamod
from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, join_paths, snippet

HTTP_VERBS = ("get", "post", "put", "delete", "patch", "options", "head")
ROUTE_METHODS = HTTP_VERBS + ("route", "api_route", "websocket")
AUTH_NAMES = re.compile(r"(auth|current_user|require|permission|admin|token|jwt|security|login_required|verify)", re.I)
PRIMITIVES = {"str", "int", "float", "bool", "bytes", "dict", "list", "set", "tuple", "Any", "None", "object"}
FRAMEWORK_PARAM_TYPES = {
    "Request", "Response", "BackgroundTasks", "WebSocket", "Session", "AsyncSession",
    "Connection", "Cursor", "HTTPConnection", "SecurityScopes",
}
ORM_BASES = {"Base", "DeclarativeBase", "SQLModel", "Model", "Document"}
DTO_BASES = {"BaseModel", "GenericModel", "SQLModel", "Schema", "TypedDict"}
ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
EXC_BASES = {"Exception", "RuntimeError", "ValueError", "HTTPException", "AppError", "BaseException"}
DDL_RE = re.compile(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\b", re.I)
DML_RE = re.compile(r"\b(?:SELECT\s+.+?\bFROM|INSERT\s+INTO|UPDATE\s+[\w.\"`]+\s+SET|DELETE\s+FROM)\b", re.I | re.S)


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for val in node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
            elif isinstance(val, ast.FormattedValue):
                parts.append("{" + _unparse(val.value) + "}")
        return "".join(parts)
    return None


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _kw_str(call: ast.Call, name: str, pos: int | None = None) -> str:
    if pos is not None and len(call.args) > pos:
        val = _const_str(call.args[pos])
        if val is not None:
            return val
    node = _kw(call, name)
    return _const_str(node) or ""


def _base_names(cls: ast.ClassDef) -> list[str]:
    out = []
    for base in cls.bases:
        raw = _unparse(base)
        simple = raw.split("[")[0].split(".")[-1].strip()
        if simple:
            out.append(simple)
    return out


def _module_key(path: str) -> str:
    stem = PurePosixPath(path).with_suffix("").as_posix()
    return stem.replace("/", ".")


def _resolve_py_import(by_path: dict[str, object], from_path: str, module: str, name: str = "", level: int = 0) -> str | None:
    if level > 0:
        parent = PurePosixPath(from_path).parent
        for _ in range(level - 1):
            parent = parent.parent
        base = (parent / module.replace(".", "/")).as_posix() if module else parent.as_posix()
    else:
        base = module.replace(".", "/")
    candidates = []
    if name:
        candidates += [f"{base}/{name}.py", f"{base}/{name}/__init__.py"]
    candidates += [f"{base}.py", f"{base}/__init__.py"]
    # Also try matching suffixes when a container directory (e.g. backend/app/...) prefixes the path
    for cand in candidates:
        cand_clean = cand.lstrip("/")
        if cand_clean in by_path:
            return cand_clean
        for existing in by_path:
            if existing.endswith("/" + cand_clean):
                return existing
    return None


def _collect_router_prefixes(files: list) -> dict[tuple[str, str], str]:
    """Resolve (file_path, router_var_name) -> full URL prefix across APIRouter / Blueprint & include_router mounts."""
    by_path = {f.path: f for f in files}
    trees: dict[str, ast.Module] = {}
    local_routers: dict[tuple[str, str], str] = {}
    imports: dict[str, dict[str, tuple[str, str]]] = {}  # file_path -> local_alias -> (target_file, target_attr)
    includes: list[tuple[str, str, str, str]] = []  # (file_path, parent_var, child_expr, mount_prefix)

    for file in files:
        try:
            tree = ast.parse(file.text)
        except SyntaxError:
            continue
        trees[file.path] = tree
        imp_map: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    local = alias.asname or alias.name
                    target_file = _resolve_py_import(by_path, file.path, mod, alias.name, node.level or 0)
                    if target_file:
                        if target_file.endswith(f"/{alias.name}.py") or target_file == f"{alias.name}.py":
                            imp_map[local] = (target_file, "router")
                        else:
                            imp_map[local] = (target_file, alias.name)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call):
                var = node.targets[0].id
                fn = _unparse(node.value.func).split(".")[-1]
                if fn in ("APIRouter", "Blueprint"):
                    prefix = _kw_str(node.value, "prefix") or _kw_str(node.value, "url_prefix")
                    local_routers[(file.path, var)] = prefix
                elif fn in ("FastAPI", "Flask", "Starlette", "Quart", "Sanic", "Litestar"):
                    local_routers[(file.path, var)] = ""
        imports[file.path] = imp_map

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("include_router", "register_blueprint") and node.args:
                parent_var = _unparse(node.func.value)
                child_expr = _unparse(node.args[0])
                mount_prefix = _kw_str(node, "prefix") or _kw_str(node, "url_prefix")
                includes.append((file.path, parent_var, child_expr, mount_prefix))

    # Propagate prefixes from parent routers/apps down to included routers (up to 4 hops)
    full_prefixes = dict(local_routers)
    for _ in range(4):
        for file_path, parent_var, child_expr, mount_prefix in includes:
            parent_prefix = full_prefixes.get((file_path, parent_var), "")
            combined_mount = join_paths(parent_prefix, mount_prefix) if (parent_prefix or mount_prefix) else ""
            imp_map = imports.get(file_path, {})
            target_key: tuple[str, str] | None = None
            if "." in child_expr:
                mod_alias, attr = child_expr.split(".", 1)
                if mod_alias in imp_map:
                    target_key = (imp_map[mod_alias][0], attr)
            elif child_expr in imp_map:
                target_key = imp_map[child_expr]
            elif (file_path, child_expr) in local_routers:
                target_key = (file_path, child_expr)
            if target_key and target_key in local_routers:
                own = local_routers[target_key]
                full_prefixes[target_key] = join_paths(combined_mount, own) if (combined_mount or own) else ""
    return full_prefixes


def _extract_rules(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[dict], list[int]]:
    rules: list[dict] = []
    status_codes: set[int] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            cond = _unparse(node.test)[:200]
            for child in node.body[:3]:
                if isinstance(child, ast.Raise) and isinstance(child.exc, ast.Call):
                    exc_name = _unparse(child.exc.func).split(".")[-1]
                    code_val = None
                    msg_val = ""
                    if exc_name == "AppError" and child.exc.args:
                        if isinstance(child.exc.args[0], ast.Constant) and isinstance(child.exc.args[0].value, int):
                            code_val = child.exc.args[0].value
                        if len(child.exc.args) > 1:
                            msg_val = _const_str(child.exc.args[1]) or _unparse(child.exc.args[1])[:120]
                    else:
                        sc = _kw(child.exc, "status_code") or (child.exc.args[0] if child.exc.args else None)
                        if isinstance(sc, ast.Constant) and isinstance(sc.value, int):
                            code_val = sc.value
                        det = _kw(child.exc, "detail")
                        if det is not None:
                            msg_val = _const_str(det) or _unparse(det)[:120]
                    if code_val:
                        status_codes.add(code_val)
                    outcome = f"raises {exc_name}" + (f" ({code_val})" if code_val else "") + (f': "{msg_val}"' if msg_val else "")
                    rules.append({"kind": "guard", "condition": cond, "outcome": outcome, "line": getattr(node, "lineno", func.lineno)})
                elif isinstance(child, ast.Return):
                    ret_str = _unparse(child.value)[:120] if child.value else "None"
                    rules.append({"kind": "guard", "condition": cond, "outcome": f"returns {ret_str}", "line": getattr(node, "lineno", func.lineno)})
    return rules[:15], sorted(status_codes)


def _fstring_sql(node: ast.JoinedStr, table_vars: dict[str, str]) -> str:
    parts: list[str] = []
    for val in node.values:
        if isinstance(val, ast.Constant) and isinstance(val.value, str):
            parts.append(val.value)
        elif isinstance(val, ast.FormattedValue):
            expr = val.value
            resolved = ""
            if isinstance(expr, ast.Call):
                fn = _unparse(expr.func).split(".")[-1]
                if fn == "fqtn" and expr.args:
                    resolved = _const_str(expr.args[0]) or ""
                elif fn in table_vars:
                    resolved = table_vars[fn]
            elif isinstance(expr, ast.Name) and expr.id in table_vars:
                resolved = table_vars[expr.id]
            parts.append(resolved if resolved else "placeholder_val")
    return "".join(parts)


def _sql_tables_in_ast(node: ast.AST, entity_tables: dict[str, str], table_vars: dict[str, str] | None = None) -> dict[str, set[str]]:
    """Return table_key -> set of CRUD operations ('C','R','U','D') found inside `node`."""
    ops: dict[str, set[str]] = {}
    tv = dict(table_vars or {})
    # Collect local variable table assignments inside node (e.g. sd = sp500_stock_daily(), sp = fqtn("sp500_companies"))
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
            var = sub.targets[0].id
            if isinstance(sub.value, ast.Call):
                fn = _unparse(sub.value.func).split(".")[-1]
                if fn == "fqtn" and sub.value.args:
                    tname = _const_str(sub.value.args[0])
                    if tname:
                        tv[var] = tname
                elif fn in tv:
                    tv[var] = tv[fn]
            elif isinstance(sub.value, ast.Name) and sub.value.id in tv:
                tv[var] = tv[sub.value.id]

    for sub in ast.walk(node):
        sql_cand = ""
        if isinstance(sub, ast.JoinedStr):
            sql_cand = _fstring_sql(sub, tv)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and len(sub.value) >= 10:
            sql_cand = sub.value
        if sql_cand and DML_RE.search(sql_cand):
            for table, table_ops in sqlmod.tables_in(sql_cand).items():
                if table == "placeholder_val":
                    continue
                key = sqlmod.table_key(table)
                ops.setdefault(key, set()).update(table_ops)
        elif isinstance(sub, ast.Call):
            fn = _unparse(sub.func)
            short = fn.split(".")[-1]
            # db.insert("table_name", ...) / db.insert_many("table_name", ...)
            if short in ("insert", "insert_many") and sub.args:
                tname = _const_str(sub.args[0])
                if tname and re.match(r"^[a-zA-Z_]\w*$", tname):
                    ops.setdefault(sqlmod.table_key(tname), set()).add("C")
            # session.query(Order) / select(Order) / session.get(Order, ...)
            elif short in ("query", "select", "get") and sub.args:
                arg0 = _unparse(sub.args[0]).split(".")[-1]
                if arg0 in entity_tables:
                    ops.setdefault(entity_tables[arg0], set()).add("R")
            elif short in ("add", "add_all", "merge", "save") and sub.args:
                for cls_name, tkey in entity_tables.items():
                    if cls_name.lower() in _unparse(sub.args[0]).lower():
                        ops.setdefault(tkey, set()).add("C")
            elif short == "delete" and sub.args:
                arg0 = _unparse(sub.args[0]).split(".")[-1]
                if arg0 in entity_tables:
                    ops.setdefault(entity_tables[arg0], set()).add("D")
    return ops


def extract(ctx: Context, cat: Catalog) -> None:
    py_files = ctx.code_files("Python")
    if not py_files:
        return

    router_prefixes = _collect_router_prefixes(py_files)
    trees: dict[str, ast.Module] = {}
    dtos: dict[str, dict] = {}
    table_vars: dict[str, str] = {}

    # Pass 1: Extract embedded SQL DDL (CREATE TABLE), ORM models, Pydantic/dataclass DTOs, Enums, Exceptions
    for file in py_files:
        try:
            tree = ast.parse(file.text)
        except SyntaxError:
            continue
        trees[file.path] = tree

        # Scan for fqtn("table_name") table references and helper functions (e.g. core/bq.py)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _unparse(node.func).split(".")[-1] == "fqtn" and node.args:
                tname = _const_str(node.args[0])
                if tname and re.match(r"^[A-Za-z_]\w*$", tname):
                    datamod._register(cat, tname, file.path, getattr(node, "lineno", 1), f"Table ref {file.name}")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and _unparse(sub.func).split(".")[-1] == "fqtn" and sub.args:
                        tname = _const_str(sub.args[0])
                        if tname:
                            table_vars[node.name] = tname

        # Scan string constants for CREATE TABLE statements (e.g. portable SQL schemas in Python files)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and DDL_RE.search(node.value):
                parsed = sqlmod.parse_sql_file(node.value)
                base_line = getattr(node, "lineno", 1)
                for table in parsed["tables"]:
                    line = base_line + max(0, table["line"] - 1)
                    entry = datamod._register(cat, table["name"], file.path, line, f"DDL {file.name}")
                    entry.setdefault("ddl", []).append({"path": file.path, "line": line, "end_line": line, "dialect": "SQL"})
                    entry["ddl_code"] = entry.get("ddl_code") or snippet(file.text, line, line + 25, 30)
                    datamod._merge_columns(entry, [dict(c, source="ddl") for c in table["columns"]])
                    if table["primary_key"]:
                        entry["primary_key"] = table["primary_key"]
                    entry["foreign_keys"] += table["foreign_keys"]

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = set(_base_names(node))
            line_start = node.lineno
            line_end = getattr(node, "end_lineno", line_start + 10)
            decos = [_unparse(d).split("(")[0].split(".")[-1] for d in node.decorator_list]

            # Check for SQLAlchemy / SQLModel / Django ORM table model
            tablename = None
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == "__tablename__":
                            tablename = _const_str(stmt.value)
            is_orm = bool(tablename) or bool(bases & ORM_BASES and "BaseModel" not in bases) or ("SQLModel" in bases and any(
                isinstance(k, ast.keyword) and k.arg == "table" and _unparse(k.value) == "True" for k in getattr(node, "keywords", [])
            ))
            if is_orm and node.name not in ("Base", "DeclarativeBase"):
                tname = tablename or re.sub(r"(?<!^)(?=[A-Z])", "_", node.name).lower() + "s"
                entry = datamod._register(cat, tname, file.path, line_start, f"ORM {node.name}")
                entry.update({"entity": node.name, "entity_path": file.path, "entity_line": line_start, "orm": "SQLAlchemy / Python ORM"})
                cols = []
                for stmt in node.body:
                    col_name = ""
                    col_type = ""
                    call_node = None
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        col_name = stmt.target.id
                        col_type = _unparse(stmt.annotation)
                        call_node = stmt.value if isinstance(stmt.value, ast.Call) else None
                    elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and isinstance(stmt.value, ast.Call):
                        fn = _unparse(stmt.value.func).split(".")[-1]
                        if fn in ("Column", "mapped_column", "Field", "CharField", "IntegerField", "TextField", "BooleanField", "DateTimeField", "ForeignKey", "UUIDField"):
                            col_name = stmt.targets[0].id
                            call_node = stmt.value
                            if call_node.args:
                                col_type = _unparse(call_node.args[0])
                    if col_name and not col_name.startswith("_"):
                        pk = bool(call_node and _unparse(_kw(call_node, "primary_key")) == "True") or col_name == "id"
                        nullable = not pk and (not call_node or _unparse(_kw(call_node, "nullable")) != "False")
                        cols.append({"name": col_name, "field": col_name, "type": col_type or "VARCHAR", "pk": pk, "nullable": nullable, "source": "entity", "line": getattr(stmt, "lineno", line_start)})
                if cols:
                    entry["entity_columns"] = cols
                    datamod._merge_columns(entry, cols)
                cat.entity_tables[node.name] = sqlmod.table_key(tname)
                continue

            # Enum
            if bases & ENUM_BASES:
                values = [
                    stmt.targets[0].id
                    for stmt in node.body
                    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name) and not stmt.targets[0].id.startswith("_")
                ]
                item = cat.add(
                    Item("shared", "enum", f"enum:{node.name}", node.name, file.path, line_start, line_end, "Python", "Python",
                         f"class {node.name}({', '.join(bases)})", {"values": values[:100]}, code=snippet(file.text, line_start, line_end, 40))
                )
                cat.type_keys.setdefault(node.name, item.key)
                continue

            # Exception
            if bases & EXC_BASES or node.name.endswith(("Error", "Exception")):
                item = cat.add(
                    Item("shared", "exception", f"exc:{node.name}", node.name, file.path, line_start, line_end, "Python", "Python",
                         f"class {node.name}({', '.join(bases)})", {"bases": sorted(bases)}, code=snippet(file.text, line_start, line_end, 30))
                )
                cat.type_keys.setdefault(node.name, item.key)
                continue

            # DTO (Pydantic BaseModel / dataclass / TypedDict)
            if bases & DTO_BASES or "dataclass" in decos:
                fields = []
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and not stmt.target.id.startswith("_"):
                        fname = stmt.target.id
                        ftype = _unparse(stmt.annotation)
                        required = stmt.value is None and "Optional" not in ftype and "| None" not in ftype
                        validations: list[str] = []
                        if isinstance(stmt.value, ast.Call) and _unparse(stmt.value.func).split(".")[-1] == "Field":
                            if stmt.value.args and _unparse(stmt.value.args[0]) == "...":
                                required = True
                            for kw in stmt.value.keywords:
                                if kw.arg in ("min_length", "max_length", "ge", "le", "gt", "lt", "pattern", "regex"):
                                    validations.append(f"{kw.arg}={_unparse(kw.value)}")
                        fields.append({"name": fname, "type": ftype[:120], "required": required, "validations": validations})
                info = {"path": file.path, "line": line_start, "end_line": line_end, "fields": fields[:80], "kind": "dataclass" if "dataclass" in decos else "BaseModel"}
                dtos[node.name] = info
                item = cat.add(
                    Item("shared", "dto", f"dto:{node.name}", node.name, file.path, line_start, line_end, "Python", info["kind"],
                         f"class {node.name}", {"fields": fields[:80], "declared_as": info["kind"]}, code=snippet(file.text, line_start, line_end, 50))
                )
                cat.type_keys.setdefault(node.name, item.key)

    # Resolve module-level aliases to table helpers (e.g. MART = sp500_mart)
    for tree in trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                var = node.targets[0].id
                if isinstance(node.value, ast.Name) and node.value.id in table_vars:
                    table_vars[var] = table_vars[node.value.id]
                elif isinstance(node.value, ast.Call):
                    fn = _unparse(node.value.func).split(".")[-1]
                    if fn == "fqtn" and node.value.args and _const_str(node.value.args[0]):
                        table_vars[var] = _const_str(node.value.args[0]) or ""
                    elif fn in table_vars:
                        table_vars[var] = table_vars[fn]

    # Pass 2: Extract Service classes, Repository/DAO classes, Service/Data modules, and Endpoints
    for file in py_files:
        tree = trees.get(file.path)
        if tree is None:
            continue

        # Map imported module/class/function names in this file for call resolution
        local_imports: dict[str, str] = {}
        imported_funcs: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod_stem = (node.module or "").split(".")[-1]
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    local_imports[local_name] = alias.name
                    if mod_stem and alias.name[:1].islower():
                        imported_funcs[local_name] = (mod_stem, alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local_imports[alias.asname or alias.name.split(".")[-1]] = alias.name.split(".")[-1]

        file_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {
            node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        has_route_funcs = False
        module_funcs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_endpoint = False
                is_job_or_asset = False
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr in ROUTE_METHODS:
                        is_endpoint = True
                        has_route_funcs = True
                        _add_endpoint(file, node, deco, router_prefixes, dtos, local_imports, imported_funcs, file_funcs, table_vars, cat)
                    else:
                        deco_name = _unparse(deco.func if isinstance(deco, ast.Call) else deco).split(".")[-1]
                        if deco_name in ("task", "shared_task", "scheduled_job", "repeat_every", "job", "schedule"):
                            is_job_or_asset = True
                            _add_job(file, node, deco if isinstance(deco, ast.Call) else None, cat, table_vars)
                        elif deco_name in ("asset", "dbt_assets", "multi_asset", "op"):
                            module_funcs.append(node)
                if not is_endpoint and not is_job_or_asset and not node.name.startswith("_"):
                    if node not in module_funcs:
                        module_funcs.append(node)

            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Call):
                fn_name = _unparse(node.value.func).split(".")[-1]
                if fn_name in ("define_asset_job", "ScheduleDefinition"):
                    job_name = _kw_str(node.value, "name") or node.targets[0].id
                    cron = _kw_str(node.value, "cron_schedule") or fn_name
                    desc = _kw_str(node.value, "description")
                    line_s = node.lineno
                    line_e = getattr(node, "end_lineno", line_s + 10)
                    j_item = cat.add(
                        Item(
                            "service", "job", f"job:{job_name}", job_name, file.path, line_s, line_e, "Python", "Dagster",
                            f"{fn_name}({job_name})", {"human": cron, "schedule": {"cron": cron}},
                            summary=desc[:240], summary_source="docstring" if desc else "",
                            code=snippet(file.text, line_s, line_e, 30),
                        )
                    )
                    for table_key, ops in _sql_tables_in_ast(tree, cat.entity_tables, table_vars).items():
                        cat.pending_tables.append((j_item.key, table_key, "/".join(sorted(ops)), ""))

            elif isinstance(node, ast.ClassDef):
                bases = set(_base_names(node))
                if node.name in cat.type_keys or bases & (ORM_BASES | DTO_BASES | ENUM_BASES | EXC_BASES):
                    continue
                is_repo = file.layer == "data" or node.name.endswith(("Repository", "DAO", "Dao", "Store"))
                is_service = file.layer == "service" or node.name.endswith(("Service", "Manager", "UseCase", "Handler", "Processor", "Pipeline", "Engine"))
                if is_repo or is_service:
                    _add_class_component(file, node, "data" if is_repo else "service", "repository" if is_repo else "service", cat, table_vars)

        # Register module-level service or data modules (e.g. app/services/chat.py, core/bigquery.py, dagster assets) when they expose public functions
        is_dagster_asset_mod = "/assets/" in file.path and bool(module_funcs)
        if module_funcs and not has_route_funcs and (file.layer in ("service", "data") or is_dagster_asset_mod) and file.stem != "__init__":
            layer = "service" if is_dagster_asset_mod else file.layer
            kind = "repository" if layer == "data" else "service"
            mod_name = file.stem
            key = f"{kind}:{_module_key(file.path)}"
            methods = []
            all_rules = []
            for fn in module_funcs[:40]:
                args = [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]
                ret = _unparse(fn.returns)
                sig = f"def {fn.name}({', '.join(args)})" + (f" -> {ret}" if ret else "")
                methods.append({"name": fn.name, "signature": sig, "line": fn.lineno})
                rules, _ = _extract_rules(fn)
                all_rules.extend(rules)
            doc = ast.get_docstring(tree) or ""
            item = cat.add(
                Item(
                    layer, kind, key, mod_name, file.path, 1, file.text.count("\n") + 1, "Python", "Dagster" if is_dagster_asset_mod else "Python",
                    f"module {_module_key(file.path)} ({len(methods)} functions)",
                    {"class": mod_name, "methods": methods, "rules": all_rules[:20]},
                    summary=doc.splitlines()[0][:240] if doc else "",
                    summary_source="docstring" if doc else "",
                    code=snippet(file.text, 1, 90, 90),
                )
            )
            cat.type_keys.setdefault(mod_name, item.key)
            for m in methods:
                cat.type_members.setdefault(mod_name, {})[m["name"]] = item.key
            for table_key, ops in _sql_tables_in_ast(tree, cat.entity_tables, table_vars).items():
                cat.pending_tables.append((item.key, table_key, "/".join(sorted(ops)), ""))
            for fn in module_funcs:
                _record_py_calls(item.key, fn, local_imports, imported_funcs, cat)


def _add_class_component(file, node: ast.ClassDef, layer: str, kind: str, cat: Catalog, table_vars: dict[str, str] | None = None) -> None:
    line_start = node.lineno
    line_end = getattr(node, "end_lineno", line_start + 20)
    methods = []
    all_rules = []
    field_types: dict[str, str] = {}

    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if stmt.name == "__init__":
                for arg in stmt.args.args:
                    if arg.arg != "self" and arg.annotation:
                        field_types[arg.arg] = _unparse(arg.annotation).split("[")[0].split(".")[-1]
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Attribute):
                        if _unparse(sub.targets[0].value) == "self" and isinstance(sub.value, ast.Name) and sub.value.id in field_types:
                            field_types[sub.targets[0].attr] = field_types[sub.value.id]
                continue
            if stmt.name.startswith("_"):
                continue
            args = [a.arg for a in stmt.args.args if a.arg not in ("self", "cls")]
            ret = _unparse(stmt.returns)
            sig = f"{stmt.name}({', '.join(args)})" + (f" -> {ret}" if ret else "")
            methods.append({"name": stmt.name, "signature": sig, "line": stmt.lineno})
            rules, _ = _extract_rules(stmt)
            all_rules.extend(rules)

    doc = ast.get_docstring(node) or ""
    key = f"{kind}:{node.name}"
    item = cat.add(
        Item(
            layer, kind, key, node.name, file.path, line_start, line_end, "Python", "Python",
            f"class {node.name}",
            {"class": node.name, "methods": methods, "rules": all_rules[:20]},
            summary=doc.splitlines()[0][:240] if doc else "",
            summary_source="docstring" if doc else "",
            code=snippet(file.text, line_start, line_end, 80),
        )
    )
    cat.type_keys.setdefault(node.name, item.key)
    for m in methods:
        cat.type_members.setdefault(node.name, {})[m["name"]] = item.key
    for table_key, ops in _sql_tables_in_ast(node, cat.entity_tables, table_vars).items():
        cat.pending_tables.append((item.key, table_key, "/".join(sorted(ops)), ""))
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _record_py_calls(item.key, stmt, field_types, {}, cat)


def _add_job(file, node: ast.FunctionDef | ast.AsyncFunctionDef, deco: ast.Call | None, cat: Catalog, table_vars: dict[str, str] | None = None) -> None:
    line_start = node.lineno
    line_end = getattr(node, "end_lineno", line_start + 15)
    sched = (_kw_str(deco, "cron") or _kw_str(deco, "trigger") or _unparse(deco)) if deco else node.name
    item = cat.add(
        Item(
            "service", "job", f"job:{file.stem}.{node.name}", f"{file.stem}.{node.name}",
            file.path, line_start, line_end, "Python", "Celery / Scheduler",
            f"def {node.name}(...)", {"human": sched[:160], "schedule": {"expression": sched[:160]}},
            code=snippet(file.text, line_start, line_end, 40),
        )
    )
    for table_key, ops in _sql_tables_in_ast(node, cat.entity_tables, table_vars).items():
        cat.pending_tables.append((item.key, table_key, "/".join(sorted(ops)), ""))


def _add_endpoint(
    file,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    deco: ast.Call,
    router_prefixes: dict[tuple[str, str], str],
    dtos: dict[str, dict],
    local_imports: dict[str, str],
    imported_funcs: dict[str, tuple[str, str]],
    file_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    table_vars: dict[str, str],
    cat: Catalog,
) -> None:
    assert isinstance(deco.func, ast.Attribute)
    router_var = _unparse(deco.func.value)
    attr = deco.func.attr
    prefix = router_prefixes.get((file.path, router_var), "")
    raw_path = _kw_str(deco, "path", pos=0) or "/"
    # Convert Flask <int:id> to {id}
    norm_route = re.sub(r"<(?:[\w]+:)?(\w+)>", r"{\1}", raw_path)
    full_path = join_paths(prefix, norm_route) if prefix else (norm_route if norm_route.startswith("/") else "/" + norm_route)

    verbs: list[str] = []
    if attr in HTTP_VERBS:
        verbs = [attr.upper()]
    elif attr == "websocket":
        verbs = ["WS"]
    else:
        methods_node = _kw(deco, "methods")
        if isinstance(methods_node, (ast.List, ast.Tuple, ast.Set)):
            verbs = [(_const_str(elt) or "GET").upper() for elt in methods_node.elts]
        if not verbs:
            verbs = ["GET"]

    framework = "Flask" if any("flask" in _unparse(n).lower() for n in file.text.splitlines()[:30] if "import" in n) else "FastAPI"
    line_start = node.lineno
    line_end = getattr(node, "end_lineno", line_start + 25)
    path_param_names = set(re.findall(r"\{(\w+)\}", full_path))

    params: list[dict] = []
    security_exprs: list[str] = []
    request_info: dict | None = None
    dep_var_types: dict[str, str] = dict(local_imports)

    # Decorator-level dependencies=[Depends(...)]
    deco_deps = _kw(deco, "dependencies")
    if isinstance(deco_deps, (ast.List, ast.Tuple)):
        for elt in deco_deps.elts:
            dep_str = _unparse(elt)
            if AUTH_NAMES.search(dep_str):
                security_exprs.append(dep_str)

    # Function parameters
    args_list = list(node.args.args)
    defaults_offset = len(args_list) - len(node.args.defaults)
    for idx, arg in enumerate(args_list):
        if arg.arg in ("self", "cls"):
            continue
        ann_str = _unparse(arg.annotation) if arg.annotation else ""
        base_type = ann_str.split("[")[0].split("|")[0].split(".")[-1].strip()
        default_node = node.args.defaults[idx - defaults_offset] if idx >= defaults_offset else None
        default_str = _unparse(default_node) if default_node is not None else ""

        # Check Depends(...) / Security(...)
        if isinstance(default_node, ast.Call) and _unparse(default_node.func).split(".")[-1] in ("Depends", "Security"):
            dep_target = _unparse(default_node.args[0]) if default_node.args else base_type
            dep_simple = dep_target.split(".")[-1]
            if AUTH_NAMES.search(dep_simple) or AUTH_NAMES.search(arg.arg):
                security_exprs.append(f"Depends({dep_simple})")
            elif dep_simple and dep_simple not in PRIMITIVES and dep_simple not in FRAMEWORK_PARAM_TYPES:
                dep_var_types[arg.arg] = dep_simple
            continue

        if base_type in FRAMEWORK_PARAM_TYPES:
            continue

        # Extract Query / Path / Body / Header constraints (ge, le, min_length, max_length, etc.)
        constraints: list[str] = []
        is_call_default = isinstance(default_node, ast.Call)
        call_fn = _unparse(default_node.func).split(".")[-1] if is_call_default else ""
        if is_call_default and call_fn in ("Query", "Path", "Body", "Header", "Cookie", "Form"):
            for kw in default_node.keywords:
                if kw.arg in ("ge", "le", "gt", "lt", "min_length", "max_length", "pattern", "regex"):
                    constraints.append(f"{kw.arg}={_unparse(kw.value)}")

        if arg.arg in path_param_names or call_fn == "Path":
            params.append({"in": "path", "name": arg.arg, "type": ann_str or "str", "required": True, "validations": constraints})
        elif base_type in dtos or arg.arg in ("body", "payload", "data") or call_fn in ("Body", "Form", "File"):
            dto = dtos.get(base_type)
            request_info = {
                "type": base_type or ann_str or "dict",
                "name": arg.arg,
                "fields": dto["fields"] if dto else [],
            }
            params.append({"in": "body", "name": arg.arg, "type": ann_str or "dict", "required": default_node is None or default_str.startswith(("Body(...)", "Form(...)", "File(...)")), "validations": constraints})
        elif call_fn == "Header":
            params.append({"in": "header", "name": arg.arg, "type": ann_str or "str", "required": "..." in default_str, "validations": constraints})
        else:
            req = (default_node is None or (is_call_default and bool(default_node.args) and _unparse(default_node.args[0]) == "...")) and "Optional" not in ann_str and "| None" not in ann_str
            params.append({"in": "query", "name": arg.arg, "type": ann_str or "str", "required": req, "validations": constraints})

    # Ensure path placeholders in URL that weren't in args are still listed
    listed_paths = {p["name"] for p in params if p["in"] == "path"}
    for pname in re.findall(r"\{(\w+)\}", full_path):
        if pname not in listed_paths:
            params.insert(0, {"in": "path", "name": pname, "type": "str", "required": True})

    # Follow same-file helper functions called by the endpoint (up to 2 hops)
    helper_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = [node]
    seen_helpers = {node.name}
    for _ in range(2):
        for current_fn in list(helper_nodes):
            for sub in ast.walk(current_fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in file_funcs and sub.func.id not in seen_helpers:
                    seen_helpers.add(sub.func.id)
                    helper_nodes.append(file_funcs[sub.func.id])

    # Response model / return annotation
    resp_model = _unparse(_kw(deco, "response_model")) or _unparse(node.returns)
    resp_simple = resp_model.split("[")[-1].rstrip("]").split(".")[-1].strip() if resp_model else ""
    response_info: dict = {"kind": "body", "type": resp_model or "JSON"}
    if resp_simple in dtos:
        response_info["fields"] = dtos[resp_simple]["fields"]

    # Status codes & guard rules (across endpoint + its same-file helpers)
    rules: list[dict] = []
    guard_codes: set[int] = set()
    for fn_node in helper_nodes:
        r_list, c_list = _extract_rules(fn_node)
        rules.extend(r_list)
        guard_codes.update(c_list)
    rules = rules[:15]
    explicit_sc = _kw(deco, "status_code")
    primary_sc = int(explicit_sc.value) if isinstance(explicit_sc, ast.Constant) and isinstance(explicit_sc.value, int) else 200
    status_codes = sorted({primary_sc, *guard_codes})

    # Also detect inline permission checks in function body (e.g. require(user, ...), _require_chat(user))
    for fn_node in helper_nodes:
        for sub in ast.walk(fn_node):
            if isinstance(sub, ast.Call):
                fn_name = _unparse(sub.func).split(".")[-1]
                if AUTH_NAMES.search(fn_name):
                    security_exprs.append(_unparse(sub)[:100])

    doc = ast.get_docstring(node) or ""
    table_ops: dict[str, set[str]] = {}
    for fn_node in helper_nodes:
        for tkey, ops in _sql_tables_in_ast(fn_node, cat.entity_tables, table_vars).items():
            table_ops.setdefault(tkey, set()).update(ops)

    for verb in verbs:
        ep_name = f"{verb} {full_path}"
        sig_args = ", ".join(f"{p['name']}: {p['type']}" for p in params[:6])
        sig = f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}def {node.name}({sig_args})" + (f" -> {resp_model}" if resp_model else "")
        item = cat.add(
            Item(
                "api", "endpoint", f"api:{ep_name}", ep_name, file.path, line_start, line_end, "Python", framework,
                sig,
                {
                    "method": verb,
                    "path": full_path,
                    "style": "WebSocket" if verb == "WS" else "REST",
                    "handler": {"function": node.name, "path": file.path, "line": line_start},
                    "params": params,
                    "request": request_info,
                    "response": response_info,
                    "status_codes": status_codes,
                    "security": {"expressions": sorted(set(security_exprs))} if security_exprs else {},
                    "rules": rules,
                },
                summary=doc.splitlines()[0][:240] if doc else "",
                summary_source="docstring" if doc else "",
                code=snippet(file.text, max(1, line_start - 1), line_end, 60),
            )
        )
        if request_info and request_info.get("type") in dtos:
            cat.pending_types.append((item.key, request_info["type"], "request"))
        if resp_simple in dtos:
            cat.pending_types.append((item.key, resp_simple, "response"))
        for dep_type in set(dep_var_types.values()):
            if dep_type not in local_imports.values():
                cat.pending_types.append((item.key, dep_type, "uses"))
        for table_key, ops in table_ops.items():
            cat.pending_tables.append((item.key, table_key, "/".join(sorted(ops)), ""))
        for fn_node in helper_nodes:
            _record_py_calls(item.key, fn_node, dep_var_types, imported_funcs, cat)


def _record_py_calls(
    source_key: str,
    node: ast.AST,
    var_types: dict[str, str],
    imported_funcs: dict[str, tuple[str, str]],
    cat: Catalog,
) -> None:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Attribute):
                receiver = _unparse(sub.func.value)
                if receiver.startswith("self."):
                    receiver = receiver[5:]
                target_type = var_types.get(receiver) or receiver.split(".")[-1]
                method = sub.func.attr
                if target_type and re.match(r"^[A-Za-z_]\w*$", target_type):
                    cat.pending_calls.append((source_key, target_type, method, getattr(sub, "lineno", 1)))
            elif isinstance(sub.func, ast.Name) and sub.func.id in imported_funcs:
                mod_stem, orig_fn = imported_funcs[sub.func.id]
                cat.pending_calls.append((source_key, mod_stem, orig_fn, getattr(sub, "lineno", 1)))
