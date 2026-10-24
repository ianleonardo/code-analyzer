"""SQL understanding for the data catalogue: Oracle and MariaDB/MySQL DDL (tables, columns, keys, indexes, comments,
views, sequences, PL/SQL procedures / packages / triggers), DML table + operation extraction, JPQL entity extraction
and Spring Data derived-query names.
"""

from __future__ import annotations

import re

_SQL_STRING = r"'(?:''|[^'])*'"
_SQL_LEX = re.compile(_SQL_STRING + r"|--[^\n]*|/\*[\s\S]*?\*/|#[^\n]*")
_PLSQL_START = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?:PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE\s+BODY)\b",
    re.I,
)
RESERVED = {
    "select", "where", "set", "values", "on", "using", "as", "join", "left", "right", "inner", "outer", "full", "cross", "lateral",
    "dual", "table", "only", "into", "from", "group", "order", "having", "union", "limit", "offset", "fetch", "for", "with", "natural",
    "straight_join", "ignore", "low_priority", "delayed", "high_priority", "quick", "unnest", "and", "or", "not", "null",
}


def strip_sql_comments(text: str) -> str:
    def repl(match: re.Match) -> str:
        value = match.group(0)
        if value.startswith("'"):
            return value
        if value.startswith("#") and re.match(r"#\s*\{", value):
            return value  # MyBatis #{param}
        return re.sub(r"[^\n]", " ", value)

    return _SQL_LEX.sub(repl, text)


def unquote(name: str) -> str:
    return re.sub(r'["`\[\]]', "", name or "").strip()


def table_key(name: str) -> str:
    """Lower-case table name without schema or quotes."""
    return unquote(name).split(".")[-1].lower()


def _split_semicolons(line: str) -> list[str]:
    parts, i = [], 0
    for m in re.finditer(_SQL_STRING + r"|;", line):
        if m.group(0) == ";":
            parts.append(line[i : m.start()])
            i = m.end()
    parts.append(line[i:])
    return parts


def split_statements(text: str) -> list[tuple[int, str]]:
    """(1-based start line, statement) pairs. Handles MariaDB DELIMITER blocks and Oracle '/' terminated PL/SQL."""
    clean = strip_sql_comments(text)
    out: list[tuple[int, str]] = []
    cur: list[tuple[int, str]] = []
    delim = ";"
    plsql = False

    def flush() -> None:
        nonlocal cur, plsql
        statement = "\n".join(t for _, t in cur).strip()
        if statement:
            first = next((n for n, t in cur if t.strip()), cur[0][0])
            out.append((first, statement))
        cur = []
        plsql = False

    for lineno, line in enumerate(clean.split("\n"), 1):
        dm = re.match(r"\s*DELIMITER\s+(\S+)\s*$", line, re.I)
        if dm:
            flush()
            delim = dm.group(1)
            continue
        if plsql:
            if re.match(r"\s*/\s*$", line):
                flush()
                continue
            if re.match(r"\s*CREATE\s", line, re.I) and cur and re.search(r"\bEND\b[^;]*;\s*$", "\n".join(t for _, t in cur).rstrip(), re.I):
                flush()
            else:
                cur.append((lineno, line))
                continue
        if not cur and not line.strip():
            continue
        cur.append((lineno, line))
        if delim == ";" and len(cur) <= 3 and _PLSQL_START.match(" ".join(t for _, t in cur)):
            plsql = True
            continue
        if delim != ";":
            if line.rstrip().endswith(delim):
                cur[-1] = (lineno, line.rstrip()[: -len(delim)])
                flush()
            continue
        segments = _split_semicolons(line)
        if len(segments) == 1:
            continue
        cur[-1] = (lineno, segments[0])
        flush()
        for segment in segments[1:-1]:
            if segment.strip():
                out.append((lineno, segment.strip()))
        if segments[-1].strip():
            cur = [(lineno, segments[-1])]
    flush()
    return out


_NAME = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$#]+)(?:\s*\.\s*(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$#]+))?'
_CREATE_TABLE = re.compile(rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+TEMPORARY\s+|TEMPORARY\s+|TEMP\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})\s*\(", re.I)
_CREATE_VIEW = re.compile(rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+|NOFORCE\s+)?(?:ALGORITHM\s*=\s*\w+\s+)?(?:DEFINER\s*=\s*\S+\s+)?(?:SQL\s+SECURITY\s+\w+\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})[\s\S]*?\bAS\b\s*([\s\S]*)$", re.I)
_CREATE_INDEX = re.compile(rf"^\s*CREATE\s+(UNIQUE\s+|BITMAP\s+|FULLTEXT\s+|SPATIAL\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})\s+ON\s+({_NAME})\s*\(([^)]*)\)", re.I)
_CREATE_SEQ = re.compile(rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})([\s\S]*)$", re.I)
_CREATE_PROC = re.compile(rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(?:DEFINER\s*=\s*\S+\s+)?(PROCEDURE|FUNCTION|PACKAGE\s+BODY|PACKAGE|TYPE\s+BODY|TYPE)\s+(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})\s*(\([^;]*?\))?", re.I)
_CREATE_TRIGGER = re.compile(rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?(?:DEFINER\s*=\s*\S+\s+)?TRIGGER\s+(?:IF\s+NOT\s+EXISTS\s+)?({_NAME})\s+(BEFORE|AFTER|INSTEAD\s+OF)\s+([\w\s,]+?)\s+ON\s+({_NAME})", re.I)
_ALTER_TABLE = re.compile(rf"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({_NAME})\s+([\s\S]*)$", re.I)
_COMMENT_ON = re.compile(rf"^\s*COMMENT\s+ON\s+(TABLE|COLUMN)\s+({_NAME}(?:\s*\.\s*[\w$#\"]+)?)\s+IS\s+'((?:''|[^'])*)'", re.I)
_INSERT = re.compile(rf"^\s*INSERT\s+(?:IGNORE\s+)?INTO\s+({_NAME})", re.I)


def _split_cols(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            m = re.compile(_SQL_STRING).match(text, i)
            if m:
                i = m.end()
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _close_paren(text: str, index: int) -> int:
    depth = 0
    i = index
    while i < len(text):
        ch = text[i]
        if ch == "'":
            m = re.compile(_SQL_STRING).match(text, i)
            if m:
                i = m.end()
                continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text) - 1


def _col_list(text: str) -> list[str]:
    return [unquote(c.split()[0]) for c in _split_cols(text) if c.split()]


_TYPE = re.compile(r"^((?:[\w$]+)(?:\s+(?:VARYING|PRECISION|UNSIGNED|ZEROFILL|WITH(?:OUT)?\s+(?:LOCAL\s+)?TIME\s+ZONE|CHARACTER\s+SET\s+\w+))*\s*(?:\([^)]*\))?(?:\s+(?:UNSIGNED|ZEROFILL|BYTE|CHAR))*)", re.I)


def _column(definition: str) -> dict | None:
    m = re.match(r'^\s*("[^"]+"|`[^`]+`|\[[^\]]+\]|[\w$#]+)\s+([\s\S]*)$', definition)
    if not m:
        return None
    name, rest = unquote(m.group(1)), m.group(2).strip()
    tm = _TYPE.match(rest)
    col_type = re.sub(r"\s+", " ", tm.group(1)).strip() if tm else rest.split()[0] if rest.split() else ""
    tail = rest[tm.end() :] if tm else rest
    upper = tail.upper()
    column = {
        "name": name,
        "type": col_type,
        "nullable": "NOT NULL" not in upper and "PRIMARY KEY" not in upper,
        "pk": "PRIMARY KEY" in upper,
        "unique": bool(re.search(r"\bUNIQUE\b", upper)),
        "auto": bool(re.search(r"AUTO_INCREMENT|AS\s+IDENTITY|\bIDENTITY\b|\bSERIAL\b", upper)) or col_type.upper() in ("SERIAL", "BIGSERIAL"),
    }
    dm = re.search(r"(?<!BY )\bDEFAULT\s+(?!AS\b)('(?:''|[^'])*'|\([^)]*\)|[\w.$]+(?:\(\))?)", tail, re.I)
    if dm:
        column["default"] = dm.group(1)
    cm = re.search(r"\bCOMMENT\s+'((?:''|[^'])*)'", tail, re.I)
    if cm:
        column["comment"] = cm.group(1).replace("''", "'")
    rm = re.search(rf"\bREFERENCES\s+({_NAME})\s*(?:\(([^)]*)\))?", tail, re.I)
    if rm:
        column["references"] = {"table": unquote(rm.group(1)), "columns": _col_list(rm.group(2) or "")}
    ck = re.search(r"\bCHECK\s*\(", tail, re.I)
    if ck:
        column["check"] = tail[ck.end() - 1 : _close_paren(tail, ck.end() - 1) + 1]
    return column


def parse_create_table(statement: str) -> dict | None:
    m = _CREATE_TABLE.match(statement)
    if not m:
        return None
    open_index = m.end() - 1
    close = _close_paren(statement, open_index)
    body = statement[open_index + 1 : close]
    options = statement[close + 1 :]
    table = {"name": unquote(m.group(1)).split(".")[-1], "schema": unquote(m.group(1)).split(".")[0] if "." in unquote(m.group(1)) else "",
             "columns": [], "primary_key": [], "foreign_keys": [], "indexes": [], "checks": [], "unique": []}
    for element in _split_cols(body):
        upper = element.upper().lstrip()
        cname = ""
        cm = re.match(r'^CONSTRAINT\s+("[^"]+"|`[^`]+`|[\w$#]+)\s+([\s\S]*)$', element.strip(), re.I)
        if cm:
            cname, element = unquote(cm.group(1)), cm.group(2)
            upper = element.upper().lstrip()
        if upper.startswith("PRIMARY KEY"):
            pm = re.search(r"\(([^)]*)\)", element)
            table["primary_key"] = _col_list(pm.group(1)) if pm else []
        elif upper.startswith("FOREIGN KEY"):
            fm = re.match(rf"\s*FOREIGN\s+KEY\s*(?:[\w`\"]+\s*)?\(([^)]*)\)\s*REFERENCES\s+({_NAME})\s*(?:\(([^)]*)\))?([\s\S]*)", element, re.I)
            if fm:
                table["foreign_keys"].append({"name": cname, "columns": _col_list(fm.group(1)), "ref_table": unquote(fm.group(2)).split(".")[-1],
                                              "ref_columns": _col_list(fm.group(3) or ""), "on_delete": (re.search(r"ON\s+DELETE\s+(\w+(?:\s+\w+)?)", fm.group(4) or "", re.I) or [None, ""])[1]})
        elif re.match(r"(UNIQUE|UNIQUE\s+KEY|UNIQUE\s+INDEX)\b", upper):
            um = re.search(r"\(([^)]*)\)", element)
            table["unique"].append({"name": cname, "columns": _col_list(um.group(1)) if um else []})
        elif re.match(r"(KEY|INDEX|FULLTEXT|SPATIAL)\b", upper):
            im = re.match(r"\s*\w+(?:\s+(?:KEY|INDEX))?\s*([\w`\"]+)?\s*\(([^)]*)\)", element, re.I)
            if im:
                table["indexes"].append({"name": unquote(im.group(1) or ""), "columns": _col_list(im.group(2)), "unique": False})
        elif upper.startswith("CHECK"):
            table["checks"].append(element.strip()[:300])
        elif upper.startswith(("PERIOD", "LIKE", "EXCLUDE")):
            continue
        else:
            column = _column(element)
            if column:
                table["columns"].append(column)
                if column.get("references"):
                    table["foreign_keys"].append({"name": cname, "columns": [column["name"]], "ref_table": column["references"]["table"].split(".")[-1], "ref_columns": column["references"]["columns"]})
    for column in table["columns"]:
        if column["name"].lower() in [c.lower() for c in table["primary_key"]]:
            column["pk"] = True
            column["nullable"] = False
        elif column["pk"] and column["name"] not in table["primary_key"]:
            table["primary_key"].append(column["name"])
    om = re.search(r"\bCOMMENT\s*=?\s*'((?:''|[^'])*)'", options, re.I)
    if om:
        table["comment"] = om.group(1).replace("''", "'")
    em = re.search(r"\bENGINE\s*=\s*(\w+)", options, re.I)
    if em:
        table["engine"] = em.group(1)
    if re.search(r"\bTABLESPACE\s+(\w+)", options, re.I):
        table["tablespace"] = re.search(r"\bTABLESPACE\s+(\w+)", options, re.I).group(1)
    return table


def parse_sql_file(text: str) -> dict:
    """Everything a schema / migration / package file declares."""
    result: dict = {"tables": [], "views": [], "indexes": [], "sequences": [], "routines": [], "triggers": [], "alters": [], "comments": [], "seeds": {}, "statements": []}
    for line, statement in split_statements(text):
        head = statement[:400]
        table = parse_create_table(statement)
        if table:
            table["line"] = line
            table["end_line"] = line + statement.count("\n")
            result["tables"].append(table)
            continue
        vm = _CREATE_VIEW.match(statement)
        if vm and re.match(r"\s*CREATE\b[^(]*\bVIEW\b", head, re.I):
            result["views"].append({"name": unquote(vm.group(1)).split(".")[-1], "line": line, "end_line": line + statement.count("\n"),
                                    "sql": vm.group(2).strip()[:4000], "tables": tables_in(vm.group(2))})
            continue
        im = _CREATE_INDEX.match(statement)
        if im:
            result["indexes"].append({"name": unquote(im.group(2)), "table": unquote(im.group(3)).split(".")[-1], "columns": _col_list(im.group(4)),
                                      "unique": bool(im.group(1) and "UNIQUE" in im.group(1).upper()), "line": line})
            continue
        sm = _CREATE_SEQ.match(statement)
        if sm:
            result["sequences"].append({"name": unquote(sm.group(1)).split(".")[-1], "line": line, "options": re.sub(r"\s+", " ", sm.group(2)).strip()[:200]})
            continue
        tm = _CREATE_TRIGGER.match(statement)
        if tm:
            result["triggers"].append({"name": unquote(tm.group(1)).split(".")[-1], "timing": tm.group(2).upper(), "events": re.sub(r"\s+", " ", tm.group(3)).upper(),
                                       "table": unquote(tm.group(4)).split(".")[-1], "line": line, "end_line": line + statement.count("\n"),
                                       "tables": tables_in(statement[tm.end():])})
            continue
        pm = _CREATE_PROC.match(statement)
        if pm:
            kind = re.sub(r"\s+", " ", pm.group(1)).lower()
            params_text = ""
            if pm.group(3):
                open_at = pm.start(3)
                params_text = statement[open_at : _close_paren(statement, open_at) + 1]
            routine = {"name": unquote(pm.group(2)).split(".")[-1], "kind": kind, "line": line, "end_line": line + statement.count("\n"),
                       "params": _routine_params(params_text), "tables": tables_in(statement[pm.end():]), "source": statement[:6000]}
            rm = re.search(r"\bRETURN(?:S)?\s+([\w(),]+)", statement[pm.end(): pm.end() + 400], re.I)
            if rm and kind == "function":
                routine["returns"] = rm.group(1)
            if kind in ("package", "package body"):
                routine["members"] = sorted(set(re.findall(r"^\s*(?:PROCEDURE|FUNCTION)\s+([\w$#]+)", statement, re.I | re.M)))
            result["routines"].append(routine)
            continue
        am = _ALTER_TABLE.match(statement)
        if am:
            result["alters"].append({"table": unquote(am.group(1)).split(".")[-1], "action": re.sub(r"\s+", " ", am.group(2)).strip()[:1000], "line": line})
            continue
        cm = _COMMENT_ON.match(statement)
        if cm:
            result["comments"].append({"kind": cm.group(1).upper(), "target": unquote(cm.group(2)), "text": cm.group(3).replace("''", "'"), "line": line})
            continue
        ins = _INSERT.match(statement)
        if ins:
            key = unquote(ins.group(1)).split(".")[-1]
            rows = max(1, len(re.findall(r"\)\s*,\s*\(", statement)) + 1) if re.search(r"\bVALUES\b", statement, re.I) else 1
            result["seeds"][key] = result["seeds"].get(key, 0) + rows
            continue
        if re.match(r"\s*(SELECT|UPDATE|DELETE|MERGE|WITH)\b", statement, re.I):
            result["statements"].append({"line": line, "sql": statement[:4000], "operation": operation(statement), "tables": tables_in(statement)})
    return result


def _routine_params(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("("):
        text = text[1 : _close_paren(text, 0)]
    out = []
    for part in _split_cols(text):
        m = re.match(r"^\s*(?:(IN\s+OUT|INOUT|IN|OUT)\s+)?([\w$#]+)\s+(?:(IN\s+OUT|IN|OUT|NOCOPY)\s+)?(?:NOCOPY\s+)?([\s\S]+?)(?:\s+(?:DEFAULT|:=)\s+[\s\S]*)?$", part, re.I)
        if m:
            direction = (m.group(1) or m.group(3) or "IN").upper().replace("INOUT", "IN OUT")
            out.append({"name": m.group(2), "direction": direction, "type": m.group(4).strip()})
    return out


def operation(sql: str) -> str:
    m = re.match(r"\s*(?:WITH\b[\s\S]*?\)\s*)?(SELECT|INSERT|UPDATE|DELETE|MERGE|CALL|EXEC|EXECUTE|UPSERT|REPLACE)\b", sql, re.I)
    if m:
        return m.group(1).upper()
    m = re.match(r"\s*(\w+)", sql)
    return m.group(1).upper() if m else ""


_FROM_LIST_END = re.compile(r"\b(WHERE|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|GROUP|ORDER|HAVING|UNION|LIMIT|OFFSET|FETCH|FOR|CONNECT|START|WINDOW|MINUS|INTERSECT|EXCEPT|ON|USING|NATURAL|STRAIGHT_JOIN)\b|\)|;", re.I)


def tables_in(sql: str) -> dict[str, list[str]]:
    """{table: sorted ops} with ops C (insert), R (read), U (update), D (delete)."""
    found: dict[str, set[str]] = {}
    text = re.sub(_SQL_STRING, "''", sql or "")
    text = re.sub(r"[\"`\[\]]", "", text)

    def add(name: str, op: str) -> None:
        name = name.strip().rstrip(",;")
        if not name or name.startswith((":", "?", "#", "$", "(")) or name.lower() in RESERVED:
            return
        if not re.match(r"^[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?$", name):
            return
        found.setdefault(name.split(".")[-1], set()).add(op)

    for m in re.finditer(r"\bFROM\s+(?!\()([\s\S]*?)(?=" + _FROM_LIST_END.pattern + r"|$)", text, re.I):
        segment = m.group(1)
        preceding = text[max(0, m.start() - 30) : m.start()].upper()
        op = "D" if re.search(r"DELETE\s*$", preceding) else "R"
        for part in segment.split(","):
            words = part.strip().split()
            if words:
                add(words[0], op)
    for m in re.finditer(r"\bJOIN\s+(?:LATERAL\s+)?([\w$#.]+)", text, re.I):
        add(m.group(1), "R")
    for m in re.finditer(r"\b(?:INSERT|REPLACE)\s+(?:IGNORE\s+|ALL\s+)?INTO\s+([\w$#.]+)", text, re.I):
        add(m.group(1), "C")
    for m in re.finditer(r"\bINTO\s+([\w$#.]+)\s*(?:\(|VALUES|SELECT)", text, re.I):
        add(m.group(1), "C")
    for m in re.finditer(r"(?<![\w.])UPDATE\s+(?:ONLY\s+)?([\w$#.]+)\s+(?:\w+\s+)?SET\b", text, re.I):
        add(m.group(1), "U")
    for m in re.finditer(r"\bDELETE\s+(?:FROM\s+)?([\w$#.]+)", text, re.I):
        if m.group(1).upper() != "FROM":
            add(m.group(1), "D")
    for m in re.finditer(r"\bMERGE\s+INTO\s+([\w$#.]+)", text, re.I):
        add(m.group(1), "C")
        add(m.group(1), "U")
    for m in re.finditer(r"\bUSING\s+([\w$#.]+)", text, re.I):
        add(m.group(1), "R")
    for m in re.finditer(r"\bTRUNCATE\s+TABLE\s+([\w$#.]+)", text, re.I):
        add(m.group(1), "D")
    order = "CRUD"
    return {name: sorted(ops, key=order.index) for name, ops in found.items()}


def jpql_entities(jpql: str) -> dict[str, list[str]]:
    """Entity names referenced by a JPQL/HQL query (FROM Owner o, JOIN FETCH o.pets is a path, not an entity)."""
    text = re.sub(_SQL_STRING, "''", jpql or "")
    op = operation(text)
    found: dict[str, set[str]] = {}
    default = {"SELECT": "R", "UPDATE": "U", "DELETE": "D", "INSERT": "C"}.get(op, "R")
    for m in re.finditer(r"\b(?:FROM|JOIN(?:\s+FETCH)?|UPDATE|INTO)\s+([A-Z][\w$]*)(?![\w.])", text):
        found.setdefault(m.group(1), set()).add(default if re.match(r"(FROM|UPDATE|INTO)", m.group(0), re.I) else "R")
    for m in re.finditer(r"(?<![\w.])([A-Z]\w*)\s+(?:AS\s+)?[a-z]\w*\b", text):
        pass
    order = "CRUD"
    return {name: sorted(ops, key=order.index) for name, ops in found.items()}


# --- Spring Data derived queries ------------------------------------------------------------------------------------

_DERIVED = re.compile(r"^(find|read|get|query|search|stream|count|exists|delete|remove)(Distinct)?(?:(?:First|Top)(\d*))?\w*?By([A-Z]\w*)$")
_OPERATORS = [
    ("IsNotNull", "IS NOT NULL", 0), ("NotNull", "IS NOT NULL", 0), ("IsNull", "IS NULL", 0), ("Null", "IS NULL", 0),
    ("IsNotEmpty", "IS NOT EMPTY", 0), ("NotEmpty", "IS NOT EMPTY", 0), ("IsEmpty", "IS EMPTY", 0), ("Empty", "IS EMPTY", 0),
    ("StartingWith", "LIKE ?%", 1), ("StartsWith", "LIKE ?%", 1), ("EndingWith", "LIKE %?", 1), ("EndsWith", "LIKE %?", 1),
    ("NotContaining", "NOT LIKE %?%", 1), ("Containing", "LIKE %?%", 1), ("Contains", "LIKE %?%", 1), ("NotLike", "NOT LIKE ?", 1),
    ("Like", "LIKE ?", 1), ("NotIn", "NOT IN (?)", 1), ("In", "IN (?)", 1), ("Between", "BETWEEN ? AND ?", 2),
    ("LessThanEqual", "<= ?", 1), ("LessThan", "< ?", 1), ("GreaterThanEqual", ">= ?", 1), ("GreaterThan", "> ?", 1),
    ("Before", "< ?", 1), ("After", "> ?", 1), ("IsTrue", "= TRUE", 0), ("True", "= TRUE", 0), ("IsFalse", "= FALSE", 0),
    ("False", "= FALSE", 0), ("IsNot", "<> ?", 1), ("Not", "<> ?", 1), ("Is", "= ?", 1), ("Equals", "= ?", 1),
    ("Regex", "REGEXP ?", 1), ("Exists", "IS NOT NULL", 0),
]


def snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])|(?<=[A-Z])([A-Z])(?=[a-z])", r"_\1\2", name).lower()


def derived_query(method: str, table: str = "", columns: dict[str, str] | None = None) -> dict | None:
    """Spring Data method name -> readable SQL. columns maps entity property -> column name when known."""
    m = _DERIVED.match(method)
    if not m:
        return None
    verb, distinct, limit, criteria = m.groups()
    order_by = ""
    if "OrderBy" in criteria:
        criteria, order_by = criteria.split("OrderBy", 1)
    columns = columns or {}
    ignore_all = criteria.endswith("AllIgnoreCase")
    if ignore_all:
        criteria = criteria[: -len("AllIgnoreCase")]
    tokens = re.split(r"(?<=[a-z0-9])(And|Or)(?=[A-Z])", criteria)
    clauses, fields = [], []
    joiner = ""
    for token in tokens:
        if token in ("And", "Or"):
            joiner = token.upper()
            continue
        ignore = ignore_all or token.endswith("IgnoreCase") or token.endswith("IgnoringCase")
        token = re.sub(r"(IgnoreCase|IgnoringCase)$", "", token)
        op_sql = "= ?"
        prop = token
        for suffix, sql_op, _arity in _OPERATORS:
            if token.endswith(suffix) and len(token) > len(suffix):
                prop = token[: -len(suffix)]
                op_sql = sql_op
                break
        prop_name = prop[0].lower() + prop[1:] if prop else prop
        column = columns.get(prop_name) or snake(prop_name).replace("_", "_")
        expr = f"UPPER({column})" if ignore else column
        clause = f"{expr} {op_sql}".replace("?", ":" + prop_name, 1).replace("?", ":" + prop_name + "2")
        clauses.append((joiner + " " if joiner and clauses else "") + clause)
        fields.append(prop_name)
    where = " ".join(clauses)
    target = table or "<entity table>"
    if verb in ("count",):
        sql = f"SELECT COUNT(*) FROM {target} WHERE {where}"
        operation_name = "SELECT"
    elif verb == "exists":
        sql = f"SELECT 1 FROM {target} WHERE {where} LIMIT 1"
        operation_name = "SELECT"
    elif verb in ("delete", "remove"):
        sql = f"DELETE FROM {target} WHERE {where}"
        operation_name = "DELETE"
    else:
        sql = f"SELECT {'DISTINCT ' if distinct else ''}* FROM {target} WHERE {where}"
        operation_name = "SELECT"
    if order_by:
        pieces = re.split(r"(Asc|Desc)(?=[A-Z]|$)", order_by)
        parts = []
        for i in range(0, len(pieces), 2):
            prop = pieces[i]
            if prop:
                direction = pieces[i + 1] if i + 1 < len(pieces) else "Asc"
                parts.append((prop, direction))
        sql += " ORDER BY " + ", ".join(f"{columns.get(p[0][0].lower() + p[0][1:]) or snake(p[0])} {p[1].upper()}" for p in parts)
    if limit is not None and verb not in ("count", "exists", "delete", "remove"):
        sql += f" FETCH FIRST {limit or 1} ROWS ONLY"
    return {"sql": sql, "operation": operation_name, "fields": fields, "derived": True}


CRUD_METHODS = {
    "C": re.compile(r"^(save|saveAll|saveAndFlush|persist|insert|create|add|store|register|put)", re.I),
    "U": re.compile(r"^(save|saveAll|saveAndFlush|merge|update|modify|set|patch|upsert|edit|change)", re.I),
    "D": re.compile(r"^(delete|remove|purge|erase|clear|deleteAll|deleteById|deleteInBatch)", re.I),
    "R": re.compile(r"^(find|get|read|query|search|stream|count|exists|load|fetch|list|select|lookup|all)", re.I),
}


def crud_of_method(name: str) -> list[str]:
    ops = [op for op, pattern in CRUD_METHODS.items() if pattern.match(name)]
    order = "CRUD"
    return sorted(set(ops), key=order.index)
