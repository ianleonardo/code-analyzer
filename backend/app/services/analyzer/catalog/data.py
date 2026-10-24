"""Data layer: SQL schema / migration / PL-SQL files (Oracle, MariaDB, MySQL, ...), Hibernate XML mappings,
persistence.xml, hibernate.cfg.xml, Liquibase change logs, MyBatis mapper XML, Prisma and TypeORM models, database
engine detection (drivers, JDBC URLs, dialects, compose images) and the merged table catalogue (DDL + ORM).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

import yaml

from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, snippet

ENGINES = [
    # (pattern over dependency names / URLs / dialects / images, engine, category)
    (r"ojdbc|com\.oracle\.database|jdbc:oracle|oracle\w*dialect|gvenzl/oracle|container-registry\.oracle\.com|oracle/database|\boracledb\b|cx_oracle", "Oracle", "relational"),
    (r"mariadb|jdbc:mariadb|mariadbdialect", "MariaDB", "relational"),
    (r"mysql-connector|com\.mysql|jdbc:mysql|mysql\w*dialect|\bmysql2?\b|\bpymysql\b|\baiomysql\b|image:\s*mysql", "MySQL", "relational"),
    (r"org\.postgresql|postgresql|jdbc:postgresql|postgres\w*dialect|\bpg\b|\bpsycopg2?\b|\basyncpg\b|r2dbc-postgres|image:\s*postgres", "PostgreSQL", "relational"),
    (r"mssql-jdbc|jdbc:sqlserver|sqlserverdialect|mcr\.microsoft\.com/mssql|\bpyodbc\b", "SQL Server", "relational"),
    (r"jt400|jdbc:as400|db2400|db2idialect", "IBM Db2 for i (AS/400)", "relational"),
    (r"db2jcc|jcc-|jdbc:db2|db2dialect", "IBM Db2", "relational"),
    (r"com\.h2database|jdbc:h2|h2dialect|\bh2\b", "H2 (embedded)", "embedded"),
    (r"hsqldb|jdbc:hsqldb", "HSQLDB (embedded)", "embedded"),
    (r"derby", "Apache Derby (embedded)", "embedded"),
    (r"androidx\.room|room[-.](?:runtime|ktx|compiler|common)", "SQLite (Android Room)", "embedded"),
    (r"sqlite-jdbc|jdbc:sqlite|\bsqlite3\b|\baiosqlite\b|better-sqlite3|sqlite\.swift|\bgrdb\b", "SQLite", "embedded"),
    (r"\bduckdb\b", "DuckDB", "embedded"),
    (r"data-mongodb|mongodb-driver|\bmongoose\b|\bmongodb\b|\bpymongo\b|\bmotor\b|image:\s*mongo", "MongoDB", "document"),
    (r"data-redis|jedis|lettuce|\bioredis\b|\bredis\b|image:\s*redis", "Redis", "key-value"),
    (r"data-cassandra|cassandra-driver", "Cassandra", "wide-column"),
    (r"data-elasticsearch|elasticsearch", "Elasticsearch", "search"),
    (r"realm|realmswift", "Realm (mobile)", "embedded"),
    (r"google-cloud-bigquery|dbt-bigquery|\bbigquery\b", "Google BigQuery", "warehouse"),
    (r"snowflake-connector|dbt-snowflake|\bsnowflake\b", "Snowflake", "warehouse"),
]
ORMS = [
    (r"hibernate-core|spring-boot-starter-data-jpa|jakarta\.persistence|javax\.persistence|hibernate", "Hibernate / JPA"),
    (r"mybatis", "MyBatis"), (r"spring-boot-starter-data-jdbc|spring-data-jdbc", "Spring Data JDBC"), (r"spring-boot-starter-jdbc|spring-jdbc", "Spring JDBC (JdbcTemplate)"),
    (r"\bjooq\b", "jOOQ"), (r"androidx\.room", "Room"), (r"@prisma/client|\bprisma\b", "Prisma"), (r"\btypeorm\b", "TypeORM"), (r"\bsequelize\b", "Sequelize"),
    (r"\bknex\b", "Knex"), (r"\bdrizzle-orm\b", "Drizzle"), (r"\bmikro-orm\b", "MikroORM"), (r"\bexposed\b", "Exposed"), (r"\bflyway\b", "Flyway (migrations)"),
    (r"\bliquibase\b", "Liquibase (migrations)"), (r"\bsqlalchemy\b", "SQLAlchemy"), (r"\bsqlmodel\b", "SQLModel"), (r"\balembic\b", "Alembic (migrations)"),
    (r"\btortoise-orm\b", "Tortoise ORM"), (r"\bpeewee\b", "Peewee"), (r"\bdjango\b", "Django ORM"), (r"\bdbt-(?:core|bigquery|postgres|snowflake|duckdb)\b", "dbt"),
]


def _safe_xml(text: str):
    text = re.sub(r"<!DOCTYPE[^>\[]*(\[[\s\S]*?\])?\s*>", "", text or "")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for element in root.iter():
        if isinstance(element.tag, str) and "}" in element.tag:
            element.tag = element.tag.split("}", 1)[1]
        for key in list(element.attrib):
            if "}" in key:
                element.attrib[key.split("}", 1)[1]] = element.attrib.pop(key)
    return root


def _line(text: str, *needles: str) -> int:
    for needle in needles:
        if needle:
            index = text.find(needle)
            if index >= 0:
                return text.count("\n", 0, index) + 1
    return 1


def _register(cat: Catalog, name: str, path: str, line: int, source: str) -> dict:
    key = sqlmod.table_key(name)
    entry = cat.tables_registry.setdefault(key, {"name": sqlmod.unquote(name).split(".")[-1], "columns": [], "sources": [], "foreign_keys": []})
    entry.setdefault("path", path)
    entry.setdefault("line", line)
    if source not in entry["sources"]:
        entry["sources"].append(source)
    return entry


def _merge_columns(entry: dict, columns: list[dict]) -> None:
    by_name = {c["name"].lower(): c for c in entry["columns"]}
    for column in columns:
        existing = by_name.get(column["name"].lower())
        if existing is None:
            entry["columns"].append(dict(column))
            by_name[column["name"].lower()] = entry["columns"][-1]
        else:
            for k, v in column.items():
                if v not in (None, "", [], False) and not existing.get(k):
                    existing[k] = v


def extract_mappings(ctx: Context, cat: Catalog) -> dict:
    """Schema sources that must be known before code is linked (DDL, XML / Prisma / TypeORM mappings)."""
    state = {"schema_files": [], "migrations": [], "sequences": []}
    for file in ctx.files:
        if not file.text or (file.is_test and not re.search(r"(schema|data|init|migration)", file.path, re.I)):
            continue
        name = file.name
        try:
            if file.language in ("SQL", "PL/SQL"):
                _sql_file(ctx, cat, file, state["schema_files"], state["migrations"], state["sequences"])
            elif name.endswith(".hbm.xml"):
                _hbm(cat, file)
            elif name in ("persistence.xml", "hibernate.cfg.xml") or name.endswith(".cfg.xml"):
                _persistence(cat, file)
            elif file.language in ("XML", "YAML", "JSON") and re.search(r"databaseChangeLog", file.text[:3000]):
                _liquibase(cat, file, state["migrations"])
            elif file.language == "YAML" and re.search(r"^\s*(models|sources)\s*:", file.text, re.M):
                _dbt_yaml(cat, file)
            elif name == "schema.prisma":
                _prisma(cat, file)
            elif file.language == "TypeScript" and re.search(r"@Entity\s*\(", file.text) and "typeorm" in file.text[:2000]:
                _typeorm(cat, file)
        except (ValueError, IndexError, KeyError, RecursionError, AttributeError, TypeError):
            continue
    return state


def extract_late(ctx: Context, cat: Catalog, config_entries: list[dict], state: dict) -> None:
    """MyBatis mappers (need mapper interfaces) and database engines (need config + ORM evidence)."""
    for file in ctx.files:
        if file.text and not file.is_test and file.language == "XML" and "<mapper" in file.text[:4000] and "namespace=" in file.text[:4000]:
            try:
                _mybatis(ctx, cat, file)
            except (ValueError, IndexError, KeyError, AttributeError, TypeError):
                continue
    _databases(ctx, cat, config_entries, state["schema_files"], state["migrations"], state["sequences"])


def _sql_file(ctx: Context, cat: Catalog, file, schema_files: list[str], migrations: list[dict], sequences: list[dict]) -> None:
    path = file.path
    # dbt model SQL files (e.g. models/marts/sp500_insider_transactions.sql)
    if re.search(r"\{\{\s*(?:ref|source|config)\s*\(", file.text) or re.search(r"(^|/)models/.+\.sql$", path):
        if "/macros/" not in path:
            model_name = file.stem
            entry = _register(cat, model_name, path, 1, f"dbt model {file.name}")
            entry["ddl_code"] = entry.get("ddl_code") or snippet(file.text, 1, 60, 60)
            refs = set(re.findall(r"\{\{\s*ref\(\s*['\"]([\w_]+)['\"]\s*\)\s*\}\}", file.text))
            sources = set(re.findall(r"\{\{\s*source\(\s*['\"][\w_]+['\"]\s*,\s*['\"]([\w_]+)['\"]\s*\)\s*\}\}", file.text))
            upstream = sorted(refs | sources)
            tables_map = {sqlmod.table_key(t): ["R"] for t in upstream}
            tables_map[sqlmod.table_key(model_name)] = ["C"]
            q_item = cat.add(
                Item(
                    "data", "query", f"query:{file.name}", f"dbt {model_name}", path, 1, file.text.count("\n") + 1,
                    file.language, "dbt SQL", file.text.strip()[:200],
                    {"sql": file.text[:4000], "dialect": "dbt SQL", "operation": "SELECT", "tables": tables_map, "script": True},
                    code=snippet(file.text, 1, 60, 60),
                )
            )
            for up in upstream:
                cat.pending_tables.append((q_item.key, sqlmod.table_key(up), "R", "dbt ref/source"))
            cat.pending_tables.append((q_item.key, sqlmod.table_key(model_name), "C", "dbt materialization"))
            schema_files.append(path)
            return

    parsed = sqlmod.parse_sql_file(file.text)
    mig = re.match(r"^[VUR](\d+(?:[._]\d+)*)__(.+)\.sql$", file.name)
    if mig or re.search(r"(^|/)(db/migration|migrations?|flyway|changelog|changes)/", path, re.I):
        migrations.append({"path": path, "version": mig.group(1).replace("_", ".") if mig else "", "description": mig.group(2).replace("_", " ") if mig else file.stem, "tool": "Flyway" if mig else ""})
    if parsed["tables"] or parsed["views"] or parsed["routines"]:
        schema_files.append(path)
    dialect = "Oracle" if re.search(r"VARCHAR2|NUMBER\s*\(|\bNVL\b|CREATE\s+OR\s+REPLACE\s+PACKAGE|SYSDATE|\bBEGIN\b[\s\S]*\bEND\s*;\s*\n\s*/", file.text, re.I) else (
        "MariaDB / MySQL" if re.search(r"AUTO_INCREMENT|ENGINE\s*=|`\w+`|DELIMITER", file.text, re.I) else "")
    for table in parsed["tables"]:
        entry = _register(cat, table["name"], path, table["line"], f"DDL {PurePosixPath(path).name}")
        entry.setdefault("ddl", []).append({"path": path, "line": table["line"], "end_line": table.get("end_line", table["line"]), "dialect": dialect})
        entry["ddl_code"] = entry.get("ddl_code") or snippet(file.text, table["line"], table.get("end_line", table["line"]), 60)
        if dialect and dialect not in entry.get("dialects", []):
            entry.setdefault("dialects", []).append(dialect)
        for key in ("schema", "comment", "engine", "tablespace"):
            if table.get(key):
                entry[key] = table[key]
        _merge_columns(entry, [dict(c, source="ddl") for c in table["columns"]])
        if table["primary_key"]:
            entry["primary_key"] = table["primary_key"]
        entry["foreign_keys"] += table["foreign_keys"]
        entry.setdefault("indexes", []).extend(table["indexes"])
        entry.setdefault("unique", []).extend(table["unique"])
        entry.setdefault("checks", []).extend(table["checks"])
    for alter in parsed["alters"]:
        key = sqlmod.table_key(alter["table"])
        action = alter["action"]
        entry = cat.tables_registry.get(key)
        if entry is None:
            continue
        fk = re.search(r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+([\w.\"`]+)\s*(?:\(([^)]*)\))?", action, re.I)
        if fk:
            entry["foreign_keys"].append({"columns": [sqlmod.unquote(c.strip()) for c in fk.group(1).split(",")], "ref_table": sqlmod.unquote(fk.group(2)).split(".")[-1],
                                          "ref_columns": [sqlmod.unquote(c.strip()) for c in (fk.group(3) or "").split(",") if c.strip()],
                                          "name": (re.search(r"CONSTRAINT\s+([\w\"`]+)", action, re.I) or [None, ""])[1]})
        pk = re.search(r"PRIMARY\s+KEY\s*\(([^)]*)\)", action, re.I)
        if pk and not fk:
            entry["primary_key"] = [sqlmod.unquote(c.strip()) for c in pk.group(1).split(",")]
        add = re.match(r"ADD\s+(?:COLUMN\s+)?\(?\s*(?!CONSTRAINT|PRIMARY|FOREIGN|UNIQUE|INDEX|KEY)([\s\S]+?)\)?\s*$", action, re.I)
        if add and not fk and not pk:
            for part in sqlmod._split_cols(add.group(1)):
                column = sqlmod._column(part)
                if column:
                    _merge_columns(entry, [dict(column, source="ddl")])
        entry.setdefault("alters", []).append({"path": path, "line": alter["line"], "action": action[:300]})
    for index in parsed["indexes"]:
        entry = cat.tables_registry.get(sqlmod.table_key(index["table"]))
        if entry is not None:
            entry.setdefault("indexes", []).append({"name": index["name"], "columns": index["columns"], "unique": index["unique"]})
    for comment in parsed["comments"]:
        target = comment["target"].split(".")
        if comment["kind"] == "TABLE":
            entry = cat.tables_registry.get(sqlmod.table_key(target[-1]))
            if entry is not None:
                entry["comment"] = comment["text"]
        elif len(target) >= 2:
            entry = cat.tables_registry.get(sqlmod.table_key(target[-2]))
            if entry is not None:
                for column in entry["columns"]:
                    if column["name"].lower() == target[-1].lower():
                        column["comment"] = comment["text"]
    for table, rows in parsed["seeds"].items():
        entry = cat.tables_registry.get(sqlmod.table_key(table))
        if entry is not None:
            entry["seed_rows"] = entry.get("seed_rows", 0) + rows
            entry.setdefault("seed_files", []).append(path)
    for sequence in parsed["sequences"]:
        sequences.append({**sequence, "path": path})
    for view in parsed["views"]:
        item = cat.add(Item("data", "view", f"view:{view['name'].lower()}", view["name"], path, view["line"], view["end_line"], file.language, dialect or "SQL",
                            f"VIEW {view['name']}", {"sql": view["sql"], "tables": {sqlmod.table_key(t): ops for t, ops in view["tables"].items()}},
                            code=snippet(file.text, view["line"], view["end_line"], 60)))
        for table, ops in view["tables"].items():
            cat.pending_tables.append((item.key, sqlmod.table_key(table), "/".join(ops), "view"))
    for routine in parsed["routines"]:
        kind = "procedure" if routine["kind"] in ("procedure", "function", "package body", "package") else "routine"
        item = cat.add(Item("data", kind, f"routine:{routine['name'].lower()}", routine["name"], path, routine["line"], routine["end_line"], file.language, dialect or "SQL",
                            f"{routine['kind'].upper()} {routine['name']}({', '.join(p['direction'] + ' ' + p['name'] + ' ' + p['type'] for p in routine['params'])})" + (f" RETURN {routine['returns']}" if routine.get("returns") else ""),
                            {"routine_type": routine["kind"], "params": routine["params"], "returns": routine.get("returns", ""), "members": routine.get("members", []),
                             "tables": {sqlmod.table_key(t): ops for t, ops in routine["tables"].items()}},
                            code=snippet(file.text, routine["line"], routine["end_line"], 120)))
        cat.type_keys.setdefault(f"routine:{routine['name'].lower()}", item.key)
        for table, ops in routine["tables"].items():
            cat.pending_tables.append((item.key, sqlmod.table_key(table), "/".join(ops), ""))
    for trigger in parsed["triggers"]:
        item = cat.add(Item("data", "trigger", f"trigger:{trigger['name'].lower()}", trigger["name"], path, trigger["line"], trigger["end_line"], file.language, dialect or "SQL",
                            f"{trigger['timing']} {trigger['events']} ON {trigger['table']}", {"timing": trigger["timing"], "events": trigger["events"], "table": trigger["table"],
                                                                                              "tables": {sqlmod.table_key(t): ops for t, ops in trigger["tables"].items()}},
                            code=snippet(file.text, trigger["line"], trigger["end_line"], 60)))
        cat.pending_tables.append((item.key, sqlmod.table_key(trigger["table"]), "trigger", trigger["events"]))
    for statement in parsed["statements"][:40]:
        if not statement["tables"]:
            continue
        name = f"{PurePosixPath(path).name}:{statement['line']}"
        item = cat.add(Item("data", "query", f"query:{name}", name, path, statement["line"], statement["line"] + statement["sql"].count("\n"), file.language, dialect or "SQL",
                            statement["sql"][:200], {"sql": statement["sql"], "dialect": "SQL", "operation": statement["operation"],
                                                     "tables": {sqlmod.table_key(t): ops for t, ops in statement["tables"].items()}, "script": True}))
        for table, ops in statement["tables"].items():
            cat.pending_tables.append((item.key, sqlmod.table_key(table), "/".join(ops), ""))


def _dbt_yaml(cat: Catalog, file) -> None:
    try:
        doc = yaml.safe_load(file.text)
    except Exception:  # noqa: BLE001
        return
    if not isinstance(doc, dict):
        return
    models = list(doc.get("models") or [])
    for src in doc.get("sources") or []:
        if isinstance(src, dict):
            models.extend(src.get("tables") or [])
    for m in models:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        tname = str(m["name"])
        line = _line(file.text, f"name: {tname}", tname)
        entry = _register(cat, tname, file.path, line, f"dbt schema {file.name}")
        if m.get("description") and not entry.get("comment"):
            entry["comment"] = str(m["description"]).strip()[:400]
        cols = []
        for col in m.get("columns") or []:
            if not isinstance(col, dict) or not col.get("name"):
                continue
            cname = str(col["name"])
            tests = [str(t) for t in (col.get("tests") or []) if isinstance(t, str)]
            pk = "unique" in tests and "not_null" in tests
            nullable = "not_null" not in tests and not pk
            cols.append({
                "name": cname,
                "field": cname,
                "type": str(col.get("data_type") or "COLUMN"),
                "pk": pk,
                "nullable": nullable,
                "comment": str(col.get("description") or "").strip()[:240],
                "source": "dbt",
                "line": _line(file.text, f"name: {cname}"),
            })
        if cols:
            _merge_columns(entry, cols)
            pks = [c["name"] for c in cols if c.get("pk")]
            if pks and not entry.get("primary_key"):
                entry["primary_key"] = pks



def _hbm(cat: Catalog, file) -> None:
    root = _safe_xml(file.text)
    if root is None:
        return
    package = root.attrib.get("package", "")
    for cls in root.iter("class"):
        entity = cls.attrib.get("name", "").split(".")[-1]
        table = cls.attrib.get("table") or entity
        line = _line(file.text, f'name="{cls.attrib.get("name", "")}"')
        entry = _register(cat, table, file.path, line, "Hibernate XML mapping")
        entry.update({"entity": entity, "entity_path": file.path, "entity_line": line, "orm": "Hibernate (hbm.xml)"})
        if cls.attrib.get("schema"):
            entry["schema"] = cls.attrib["schema"]
        columns = []
        relations = []

        def column_of(element) -> str:
            col = element.find("column")
            return element.attrib.get("column") or (col.attrib.get("name") if col is not None else "") or element.attrib.get("name", "")

        for tag in ("id", "property", "version", "timestamp"):
            for element in cls.findall(tag):
                column = {"name": column_of(element), "field": element.attrib.get("name", ""), "java_type": element.attrib.get("type", ""), "pk": tag == "id",
                          "nullable": element.attrib.get("not-null") != "true" and tag != "id", "unique": element.attrib.get("unique") == "true", "source": "hbm",
                          "line": _line(file.text, f'name="{element.attrib.get("name", "")}"')}
                if element.attrib.get("length"):
                    column["length"] = element.attrib["length"]
                generator = element.find("generator")
                if generator is not None:
                    column["generated"] = generator.attrib.get("class", "")
                    sequence = generator.find("param")
                    if sequence is not None and sequence.attrib.get("name") in ("sequence", "sequence_name"):
                        column["sequence"] = (sequence.text or "").strip()
                columns.append(column)
        for element in cls.findall("many-to-one") + cls.findall("one-to-one"):
            col = column_of(element)
            target = element.attrib.get("class", element.attrib.get("entity-name", "")).split(".")[-1]
            columns.append({"name": col, "field": element.attrib.get("name", ""), "java_type": target, "pk": False, "nullable": element.attrib.get("not-null") != "true",
                            "source": "hbm", "relation": element.tag, "references_entity": target})
            relations.append({"field": element.attrib.get("name", ""), "kind": "ManyToOne" if element.tag == "many-to-one" else "OneToOne", "target": target, "join_column": col})
        for tag in ("set", "bag", "list", "map", "idbag"):
            for element in cls.findall(tag):
                one = element.find("one-to-many")
                many = element.find("many-to-many")
                target = (one if one is not None else many)
                relations.append({"field": element.attrib.get("name", ""), "kind": "OneToMany" if one is not None else "ManyToMany",
                                  "target": (target.attrib.get("class", "").split(".")[-1] if target is not None else ""), "join_table": element.attrib.get("table", ""),
                                  "inverse": element.attrib.get("inverse") == "true"})
        entry["entity_columns"] = columns
        entry["relations"] = relations
        cat.entity_tables[entity] = sqlmod.table_key(table)
        if package:
            entry["entity_package"] = package


def _persistence(cat: Catalog, file) -> None:
    root = _safe_xml(file.text)
    if root is None:
        return
    props = {}
    for prop in root.iter("property"):
        name = prop.attrib.get("name", "")
        value = prop.attrib.get("value", (prop.text or "").strip())
        props[name] = value
    units = [{"name": u.attrib.get("name", ""), "transaction": u.attrib.get("transaction-type", ""), "provider": (u.findtext("provider") or "").strip(),
              "classes": [c.text.strip().split(".")[-1] for c in u.findall("class") if c.text]} for u in root.iter("persistence-unit")]
    mappings = [m.attrib.get("resource") or m.attrib.get("class", "") for m in root.iter("mapping")]
    cat.extra.setdefault("persistence_configs", []).append({"path": file.path, "properties": props, "units": units, "mappings": mappings})


def _mybatis(ctx: Context, cat: Catalog, file) -> None:
    root = _safe_xml(file.text)
    if root is None or root.tag != "mapper":
        return
    namespace = root.attrib.get("namespace", "")
    owner = namespace.split(".")[-1] or file.stem
    repo_key = cat.type_keys.get(owner)
    repo = cat.items.get(repo_key) if repo_key else None
    if repo is None:
        repo = cat.add(Item("data", "repository", f"repo:{owner}", owner, file.path, 1, file.text.count("\n") + 1, "XML", "MyBatis", f"<mapper namespace=\"{namespace}\">",
                            {"entity": "", "methods": [], "mapper_xml": file.path}))
        cat.type_keys[owner] = repo.key
    else:
        repo.details["mapper_xml"] = file.path
    for element in root:
        if element.tag not in ("select", "insert", "update", "delete"):
            continue
        sql_text = re.sub(r"\s+", " ", " ".join(element.itertext())).strip()
        method = element.attrib.get("id", "")
        line = _line(file.text, f'id="{method}"')
        tables = {sqlmod.table_key(t): ops for t, ops in sqlmod.tables_in(sql_text).items()}
        name = f"{owner}.{method}"
        query = cat.add(Item("data", "query", f"query:{name}", name, file.path, line, line + sql_text.count(" ") // 12, "XML", "MyBatis", f"<{element.tag} id=\"{method}\">",
                             {"repository": owner, "method": method, "sql": sql_text[:3000], "dialect": "SQL (MyBatis dynamic)", "operation": element.tag.upper(), "tables": tables,
                              "params": [{"name": p, "type": ""} for p in sorted(set(re.findall(r"#\{(\w+)", sql_text)))],
                              "returns": element.attrib.get("resultType") or element.attrib.get("resultMap", ""), "parameter_type": element.attrib.get("parameterType", "")},
                             code=snippet(file.text, line, line + 25, 26)))
        repo.details.setdefault("methods", []).append({"name": method, "signature": method, "operation": element.tag.upper(), "dialect": "SQL", "line": line, "tables": tables, "query": query.key})
        cat.link(repo.key, query.key, "declares")
        cat.type_members.setdefault(owner, {})[method] = query.key
        for table, ops in tables.items():
            cat.pending_tables.append((query.key, table, "/".join(ops), ""))


def _liquibase(cat: Catalog, file, migrations: list[dict]) -> None:
    migrations.append({"path": file.path, "version": "", "description": file.stem, "tool": "Liquibase"})
    changes: list[tuple[str, dict]] = []
    if file.language == "XML":
        root = _safe_xml(file.text)
        if root is None:
            return
        for element in root.iter():
            if element.tag in ("createTable", "addColumn", "addForeignKeyConstraint", "createIndex", "addPrimaryKey", "addUniqueConstraint"):
                data = dict(element.attrib)
                data["columns"] = []
                for column in element.findall("column"):
                    col = dict(column.attrib)
                    constraints = column.find("constraints")
                    if constraints is not None:
                        col["constraints"] = dict(constraints.attrib)
                    data["columns"].append(col)
                changes.append((element.tag, data))
    else:
        try:
            doc = yaml.safe_load(file.text) if file.language == "YAML" else __import__("json").loads(file.text)
        except Exception:  # noqa: BLE001
            return

        def walk(node) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("createTable", "addColumn", "addForeignKeyConstraint", "createIndex", "addPrimaryKey", "addUniqueConstraint") and isinstance(value, dict):
                        data = {k: v for k, v in value.items() if not isinstance(v, (list, dict))}
                        data["columns"] = []
                        for col in value.get("columns", []) or []:
                            column = (col or {}).get("column", {}) if isinstance(col, dict) else {}
                            entry = {k: v for k, v in column.items() if not isinstance(v, dict)}
                            if isinstance(column.get("constraints"), dict):
                                entry["constraints"] = column["constraints"]
                            data["columns"].append(entry)
                        changes.append((key, data))
                    else:
                        walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(doc)
    for tag, data in changes:
        table = data.get("tableName") or data.get("baseTableName") or ""
        if not table:
            continue
        line = _line(file.text, f'tableName="{table}"', f"tableName: {table}", table)
        entry = _register(cat, table, file.path, line, "Liquibase")
        if tag in ("createTable", "addColumn"):
            columns = []
            for col in data["columns"]:
                constraints = col.get("constraints", {}) or {}
                columns.append({"name": str(col.get("name", "")), "type": str(col.get("type", "")), "nullable": str(constraints.get("nullable", "true")).lower() != "false",
                                "pk": str(constraints.get("primaryKey", "false")).lower() == "true", "unique": str(constraints.get("unique", "false")).lower() == "true",
                                "default": str(col.get("defaultValue") or col.get("defaultValueNumeric") or col.get("defaultValueComputed") or ""), "comment": str(col.get("remarks", "")),
                                "auto": str(col.get("autoIncrement", "false")).lower() == "true", "source": "liquibase"})
                if constraints.get("referencedTableName") or constraints.get("references"):
                    ref_table = constraints.get("referencedTableName") or str(constraints.get("references", "")).split("(")[0]
                    entry["foreign_keys"].append({"columns": [str(col.get("name", ""))], "ref_table": ref_table, "ref_columns": [str(constraints.get("referencedColumnNames", ""))] if constraints.get("referencedColumnNames") else []})
            _merge_columns(entry, columns)
            if data.get("remarks"):
                entry["comment"] = data["remarks"]
        elif tag == "addForeignKeyConstraint":
            entry["foreign_keys"].append({"columns": str(data.get("baseColumnNames", "")).split(","), "ref_table": data.get("referencedTableName", ""),
                                          "ref_columns": str(data.get("referencedColumnNames", "")).split(","), "name": data.get("constraintName", "")})
        elif tag == "createIndex":
            entry.setdefault("indexes", []).append({"name": data.get("indexName", ""), "columns": [str(c.get("name", "")) for c in data["columns"]], "unique": str(data.get("unique", "")).lower() == "true"})
        elif tag == "addPrimaryKey":
            entry["primary_key"] = str(data.get("columnNames", "")).split(",")


def _prisma(cat: Catalog, file) -> None:
    for m in re.finditer(r"^model\s+(\w+)\s*\{([\s\S]*?)^\}", file.text, re.M):
        entity = m.group(1)
        body = m.group(2)
        table_map = re.search(r"@@map\(\s*\"([^\"]+)\"", body)
        table = table_map.group(1) if table_map else entity
        line = file.text.count("\n", 0, m.start()) + 1
        entry = _register(cat, table, file.path, line, "Prisma model")
        entry.update({"entity": entity, "entity_path": file.path, "entity_line": line, "orm": "Prisma"})
        columns, relations = [], []
        for fm in re.finditer(r"^\s*(\w+)\s+(\w+)(\[\])?(\?)?\s*(.*)$", body, re.M):
            name, type_, many, optional, attrs = fm.groups()
            if name.startswith("@@"):
                continue
            if type_[0].isupper() and type_ not in ("String", "Int", "BigInt", "Float", "Decimal", "Boolean", "DateTime", "Json", "Bytes"):
                rel = re.search(r"@relation\([^)]*fields:\s*\[(\w+)\]", attrs)
                relations.append({"field": name, "kind": "OneToMany" if many else "ManyToOne", "target": type_, "join_column": rel.group(1) if rel else ""})
                continue
            col_map = re.search(r"@map\(\s*\"([^\"]+)\"", attrs)
            columns.append({"name": col_map.group(1) if col_map else name, "field": name, "java_type": type_ + ("[]" if many else ""), "pk": "@id" in attrs, "nullable": bool(optional),
                            "unique": "@unique" in attrs, "generated": "autoincrement" if "autoincrement()" in attrs else "", "source": "prisma"})
        entry["entity_columns"] = columns
        entry["relations"] = relations
        cat.entity_tables[entity] = sqlmod.table_key(table)
    ds = re.search(r"datasource\s+\w+\s*\{[\s\S]*?provider\s*=\s*\"(\w+)\"", file.text)
    if ds:
        cat.extra.setdefault("prisma_providers", []).append(ds.group(1))


def _typeorm(cat: Catalog, file) -> None:
    for m in re.finditer(r"@Entity\s*\(\s*(?:['\"]([^'\"]+)['\"]|\{[^}]*name:\s*['\"]([^'\"]+)['\"][^}]*\})?\s*\)\s*(?:export\s+)?class\s+(\w+)", file.text):
        entity = m.group(3)
        table = m.group(1) or m.group(2) or sqlmod.snake(entity)
        line = file.text.count("\n", 0, m.start()) + 1
        entry = _register(cat, table, file.path, line, "TypeORM @Entity")
        entry.update({"entity": entity, "entity_path": file.path, "entity_line": line, "orm": "TypeORM"})
        body_start = file.text.find("{", m.end())
        body = file.text[body_start : body_start + 20000]
        columns, relations = [], []
        for cm in re.finditer(r"@(PrimaryGeneratedColumn|PrimaryColumn|Column|CreateDateColumn|UpdateDateColumn|ManyToOne|OneToMany|OneToOne|ManyToMany|JoinColumn)\s*\(([^)]*(?:\([^)]*\)[^)]*)*)\)\s*(?:@\w+\([^)]*\)\s*)*(\w+)\s*[!?]?\s*:\s*([\w<>\[\]|. ]+)", body):
            decorator, args, name, type_ = cm.groups()
            if decorator in ("ManyToOne", "OneToMany", "OneToOne", "ManyToMany"):
                target = re.search(r"=>\s*(\w+)", args)
                relations.append({"field": name, "kind": decorator, "target": target.group(1) if target else type_.strip()})
                if decorator in ("ManyToOne", "OneToOne"):
                    columns.append({"name": f"{name}Id", "field": name, "java_type": type_.strip(), "nullable": True, "source": "typeorm", "relation": decorator})
                continue
            col_name = re.search(r"name:\s*['\"](\w+)['\"]", args)
            columns.append({"name": col_name.group(1) if col_name else name, "field": name, "java_type": type_.strip(), "pk": decorator.startswith("Primary"),
                            "nullable": "nullable: true" in args, "unique": "unique: true" in args, "generated": "auto" if decorator == "PrimaryGeneratedColumn" else "", "source": "typeorm"})
        entry["entity_columns"] = columns
        entry["relations"] = relations
        cat.entity_tables[entity] = sqlmod.table_key(table)


def _databases(ctx: Context, cat: Catalog, entries: list[dict], schema_files: list[str], migrations: list[dict], sequences: list[dict]) -> None:
    found: dict[str, dict] = {}

    def note(engine: str, category: str, source: str, detail: str, path: str = "", line: int = 0) -> dict:
        db = found.setdefault(engine, {"engine": engine, "category": category, "evidence": [], "connections": [], "dialects": [], "drivers": []})
        if len(db["evidence"]) < 25 and not any(e["detail"] == detail and e["source"] == source for e in db["evidence"]):
            db["evidence"].append({"source": source, "detail": detail[:200], "path": path, "line": line})
        return db

    def match(text: str) -> tuple[str, str] | None:
        for pattern, engine, category in ENGINES:
            if re.search(pattern, text, re.I):
                return engine, category
        return None

    for dep in ctx.dependencies:
        name = dep.get("name", "")
        hit = match(name)
        if hit and dep.get("scope") != "test" and not re.search(r"(test|mock|embedded-redis|testcontainers)", name, re.I):
            db = note(hit[0], hit[1], "dependency", f"{name} {dep.get('version', '')}".strip(), dep.get("manifest", ""))
            db["drivers"].append(name)
    from app.services.analyzer.catalog.config import mask

    for entry in entries:
        key, value = entry["key"], entry["raw"]
        lower = key.lower()
        if re.search(r"(datasource|r2dbc|jdbc|database|db)[\w.-]*\.(url|jdbc-url|uri)$|^database_url$|_db_url$|connection\.url$|jdbc\.url$|mongodb\.uri$", lower) or (re.match(r"^jdbc:", value) and "url" in lower):
            hit = match(value) or match(ctx.placeholder(value))
            if hit:
                db = note(hit[0], hit[1], "connection URL", f"{key}", entry["path"], entry["line"])
                username = next((e["raw"] for e in entries if e["path"] == entry["path"] and re.search(r"(datasource|connection)\.(username|user)$", e["key"])), "")
                masked, _ = mask(key, value)
                db["connections"].append({"profile": entry["profile"], "url": masked[:300], "username": username[:80], "path": entry["path"], "line": entry["line"]})
        elif re.search(r"(dialect|database-platform|database\.platform)$", lower):
            hit = match(value)
            if hit:
                db = note(hit[0], hit[1], "Hibernate dialect", value, entry["path"], entry["line"])
                db["dialects"].append(value.split(".")[-1])
        elif re.search(r"driver-class-name$|driver_class$|driverclassname$|connection\.driver_class$", lower):
            hit = match(value) or (("Oracle", "relational") if "oracle" in value.lower() else None)
            if hit:
                note(hit[0], hit[1], "JDBC driver", value, entry["path"], entry["line"])
        elif lower in ("spring.jpa.hibernate.ddl-auto", "hibernate.hbm2ddl.auto", "spring.jpa.generate-ddl"):
            cat.extra.setdefault("ddl_auto", []).append({"profile": entry["profile"], "value": value, "path": entry["path"]})
        elif lower in ("spring.sql.init.mode", "spring.sql.init.schema-locations", "spring.sql.init.data-locations", "spring.datasource.schema", "spring.datasource.data",
                       "spring.flyway.locations", "spring.liquibase.change-log"):
            cat.extra.setdefault("sql_init", []).append({"key": key, "profile": entry["profile"], "value": value})
        elif re.search(r"(redis|mongodb|cassandra|elasticsearch)\.(host|uri|url|nodes|uris)$", lower):
            hit = match(key)
            if hit:
                note(hit[0], hit[1], "configuration", f"{key}={mask(key, value)[0]}", entry["path"], entry["line"])
    for persistence in cat.extra.get("persistence_configs", []):
        for key, value in persistence["properties"].items():
            hit = match(value) if re.search(r"url|dialect|driver", key, re.I) else None
            if hit:
                note(hit[0], hit[1], PurePosixPath(persistence["path"]).name, f"{key}={mask(key, value)[0]}", persistence["path"])
    for file in ctx.files:
        if file.text and re.match(r"^(docker-)?compose[\w.-]*\.ya?ml$", file.name):
            for m in re.finditer(r"image:\s*['\"]?([\w./:-]+)", file.text):
                hit = match("image: " + m.group(1))
                if hit:
                    note(hit[0], hit[1], "container image", m.group(1), file.path, file.text.count("\n", 0, m.start()) + 1)
        if file.text and file.language == "Swift" and re.search(r"NSPersistentContainer|NSManagedObjectContext", file.text):
            note("SQLite (Core Data)", "embedded", "code", "Core Data stack", file.path)
        if file.text and file.language == "Swift" and re.search(r"@Model\b|ModelContainer\s*\(", file.text):
            note("SQLite (SwiftData)", "embedded", "code", "SwiftData", file.path)
        if file.language in ("SQL", "PL/SQL") and file.path in schema_files:
            if re.search(r"VARCHAR2|NUMBER\s*\(|CREATE\s+OR\s+REPLACE\s+PACKAGE", file.text or "", re.I):
                note("Oracle", "relational", "SQL dialect", "Oracle syntax (VARCHAR2 / NUMBER / PL/SQL)", file.path)
            elif re.search(r"AUTO_INCREMENT|ENGINE\s*=\s*InnoDB", file.text or "", re.I):
                engine = "MariaDB" if "mariadb" in file.path.lower() or "MariaDB" in found else "MySQL" if "mysql" in file.path.lower() or "MySQL" in found else "MariaDB"
                note(engine, "relational", "SQL dialect", "MySQL/MariaDB syntax (AUTO_INCREMENT / ENGINE=InnoDB)", file.path)
    for provider in cat.extra.get("prisma_providers", []):
        hit = match(provider) or (("PostgreSQL", "relational") if provider == "postgresql" else None)
        if hit:
            note(hit[0], hit[1], "Prisma datasource", provider)
    for room in cat.extra.get("room_databases", []):
        note("SQLite (Android Room)", "embedded", "@Database", f"{room['class']} v{room.get('version', '')} {room.get('name', '')}".strip(), room["path"], room["line"])
    orms = sorted({name for pattern, name in ORMS if re.search(pattern, ctx.dep_names, re.I)})
    tools = {m["tool"] for m in migrations if m["tool"]}
    for engine, db in found.items():
        slug = re.sub(r"[^a-z0-9]+", "-", engine.lower()).strip("-")
        relational = db["category"] in ("relational", "embedded")
        details = {
            **db, "drivers": sorted(set(db["drivers"])), "dialects": sorted(set(db["dialects"])),
            "orm": [o for o in orms if relational or "migrations" not in o] if relational else [],
            "ddl_auto": cat.extra.get("ddl_auto", []) if relational else [], "sql_init": cat.extra.get("sql_init", []) if relational else [],
            "migrations": {"tools": sorted(tools), "files": migrations[:200]} if relational else {},
            "schema_files": [p for p in schema_files if _schema_matches(p, engine, len(found))][:100] if relational else [],
            "sequences": sequences[:200] if relational and engine in ("Oracle", "PostgreSQL", "MariaDB", "IBM Db2 for i (AS/400)", "IBM Db2", "H2 (embedded)") else [],
        }
        evidence = db["evidence"][0] if db["evidence"] else {"path": ""}
        if engine.startswith("SQLite (Android"):
            details["room_databases"] = cat.extra.get("room_databases", [])
        cat.add(Item("data", "database", f"db:{slug}", engine, evidence.get("path", ""), evidence.get("line", 0), evidence.get("line", 0), "", engine,
                     f"{engine} — {db['category']}", details))


def _schema_matches(path: str, engine: str, engines: int) -> bool:
    if engines <= 1:
        return True
    lower = path.lower()
    hints = {"Oracle": "oracle", "MariaDB": "maria", "MySQL": "mysql", "PostgreSQL": "postg", "H2 (embedded)": "h2", "HSQLDB (embedded)": "hsql",
             "SQL Server": "sqlserver", "IBM Db2 for i (AS/400)": "db2"}
    hint = hints.get(engine)
    named = [h for h in hints.values() if h in lower]
    return (bool(hint) and hint in lower) if named else not hint or True


def _dedupe(indexes: list[dict]) -> list[dict]:
    out, seen = [], set()
    for index in indexes:
        key = tuple(c.lower() for c in index.get("columns", []))
        if key in seen:
            continue
        seen.add(key)
        out.append(index)
    return out


def finalize_tables(ctx: Context, cat: Catalog) -> None:
    """Turns the merged registry (DDL + ORM + Liquibase + XML) into one item per table and records FK links."""
    databases = [i for i in cat.by_kind("database") if i.details.get("category") in ("relational", "embedded")]
    for key, entry in cat.tables_registry.items():
        columns = [dict(c) for c in entry.get("columns", [])]
        by_name = {c["name"].lower(): c for c in columns}
        for column in entry.get("entity_columns", []):
            existing = by_name.get(column["name"].lower())
            if existing is None:
                columns.append(dict(column))
                by_name[column["name"].lower()] = columns[-1]
            else:
                for k, v in column.items():
                    if k == "type" and existing.get("type"):
                        continue
                    if v not in (None, "", [], False) and not existing.get(k) or k in ("field", "java_type", "validations", "relation", "references_entity"):
                        if v not in (None, "", []):
                            existing[k] = v
        foreign_keys = list(entry.get("foreign_keys", []))
        for relation in entry.get("relations", []):
            if relation.get("join_column"):
                target_key = cat.entity_tables.get(relation["target"])
                ref_name = cat.tables_registry.get(target_key or "", {}).get("name", sqlmod.snake(relation["target"]))
                if not any(fk.get("columns") == [relation["join_column"]] for fk in foreign_keys):
                    foreign_keys.append({"columns": [relation["join_column"]], "ref_table": ref_name, "ref_columns": [], "from": "entity"})
        for fk in foreign_keys:
            if fk.get("ref_entity") and not fk.get("ref_table"):
                target_key = cat.entity_tables.get(fk["ref_entity"])
                fk["ref_table"] = cat.tables_registry.get(target_key or "", {}).get("name", sqlmod.snake(fk["ref_entity"]))
            for col in fk.get("columns", []):
                column = by_name.get(str(col).lower())
                if column is not None:
                    column["references"] = {"table": fk.get("ref_table", ""), "columns": fk.get("ref_columns", [])}
        pk = entry.get("primary_key") or [c["name"] for c in columns if c.get("pk")]
        for column in columns:
            if column["name"] in pk:
                column["pk"] = True
                column["nullable"] = False
        path = entry.get("entity_path") or entry.get("path", "")
        line = entry.get("entity_line") or entry.get("line", 0)
        db_engines = [d.name for d in databases if not (d.name.startswith("SQLite (Android") and entry.get("orm") != "Room") and not (entry.get("orm") == "Room" and not d.name.startswith("SQLite (Android"))]
        details = {
            "table": entry.get("name", key), "schema": entry.get("schema", ""), "comment": entry.get("comment", ""), "entity": entry.get("entity", ""), "orm": entry.get("orm", ""),
            "columns": columns, "primary_key": pk, "foreign_keys": foreign_keys, "relations": entry.get("relations", []), "indexes": _dedupe(entry.get("indexes", [])),
            "unique": _dedupe(entry.get("unique", [])), "checks": sorted(set(entry.get("checks", []))), "sources": sorted(set(entry.get("sources", []))), "ddl": entry.get("ddl", []),
            "alters": entry.get("alters", []), "seed_rows": entry.get("seed_rows", 0), "dialect": ", ".join(entry.get("dialects", [])), "engine": entry.get("engine", ""),
            "tablespace": entry.get("tablespace", ""), "databases": db_engines, "entity_path": entry.get("entity_path", ""), "entity_line": entry.get("entity_line", 0),
        }
        item = cat.add(Item("data", "table", f"table:{key}", entry.get("name", key), path, line, entry.get("end_line", line), "", entry.get("orm") or details["dialect"] or "SQL",
                            f"{entry.get('name', key)} ({len(columns)} columns)" + (f" ↔ {entry['entity']}" if entry.get("entity") else ""), details,
                            code=(entry.get("entity_code") or "") + ("\n" + entry["ddl_code"] if entry.get("ddl_code") else "")))
        for database in databases:
            if database.name in db_engines:
                cat.link(database.key, item.key, "contains")
        if entry.get("entity"):
            cat.type_keys.setdefault(entry["entity"], item.key)
    for key, entry in cat.tables_registry.items():
        for fk in entry.get("foreign_keys", []) + [{"ref_table": cat.tables_registry.get(cat.entity_tables.get(r["target"], ""), {}).get("name", "")} for r in entry.get("relations", []) if r.get("join_column")]:
            target = sqlmod.table_key(fk.get("ref_table", ""))
            if target and f"table:{target}" in cat.items:
                cat.link(f"table:{key}", f"table:{target}", "references", ", ".join(fk.get("columns", [])))
