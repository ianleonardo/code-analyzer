"""Legacy-platform source understanding: COBOL (fixed / free, CICS, DB2, IMS), RPG (III, IV fixed, ILE free), CL, JCL,
DDS (physical / logical / display / printer files), CICS BMS maps and CICS CSD resource definitions.

Everything here is deterministic and dependency-free. Each parse_* function returns plain dicts so the file parser
(parsers.py: symbols, imports, calls) and the layer catalogue (catalog/legacy.py: programs, transactions, jobs, files,
tables, screens) share one reading of the source.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

# --- shared helpers -------------------------------------------------------------------------------------------------

_LIT = re.compile(r"'(?:[^'\n]|'')*'|\"(?:[^\"\n]|\"\")*\"")

# IBM-supplied / runtime programs: calls to them are platform services, never links inside the repository.
SYSTEM_EXACT = {
    "SORT", "DFSORT", "SYNCSORT", "ICETOOL", "ICEMAN", "IDCAMS", "IEFBR14", "IEBGENER", "IEBCOPY", "IEBUPDTE", "IEBCOMPR", "IEHLIST",
    "IEHPROGM", "IKJEFT01", "IKJEFT1A", "IKJEFT1B", "ADRDSSU", "IRXJCL", "DFHCSDUP", "DSNUTILB", "DSNTIAR", "DSNTIAC", "DSNTIAUL",
    "DSNHLI", "DSNALI", "DSNRLI", "DSNELI", "CBLTDLI", "AIBTDLI", "PLITDLI", "DLITCBL", "DFSRRC00", "QCMDEXC", "QCMD", "QCLSCAN",
    "QSYGETPH", "QSYRLSPH", "CEE3ABD", "CEELOCT", "CEEDATE", "CEEDAYS", "CEESECS", "CEEMOUT", "CEEGTST", "CEEFRST", "ILBOABN0",
    "IGZEBST", "DFHEI1", "DFHELII", "MQCONN", "MQCONNX", "MQOPEN", "MQPUT", "MQPUT1", "MQGET", "MQCLOSE", "MQDISC", "MQINQ",
    "MQSET", "MQCMIT", "MQBACK", "MQBEGIN",
}
SYSTEM_PREFIXES = ("CEE", "DFH", "DSN", "IGZ", "IGY", "ILBO", "IKJ", "IEF", "IEB", "IEH", "DFS", "QUS", "QDB", "QSN", "QMH", "QWC",
                   "QTQ", "QLG", "QSQ", "QP0", "QC2", "QSY", "QWT", "QGY", "QZ", "MQ")


def is_system_program(name: str) -> bool:
    upper = (name or "").upper().strip()
    return upper in SYSTEM_EXACT or upper.startswith(SYSTEM_PREFIXES)


def unquote(literal: str) -> str:
    literal = (literal or "").strip()
    if len(literal) >= 2 and literal[0] == literal[-1] and literal[0] in "'\"":
        return literal[1:-1].replace(literal[0] * 2, literal[0])
    return literal


def is_literal(token: str) -> bool:
    token = (token or "").strip()
    return len(token) >= 2 and token[0] == token[-1] and token[0] in "'\""


def mask_literals(text: str) -> str:
    """Same length as `text`; literal contents blanked so keywords inside strings never match."""
    return _LIT.sub(lambda m: m.group(0)[0] + " " * (len(m.group(0)) - 2) + m.group(0)[-1], text)


class LineIndex:
    def __init__(self, text: str) -> None:
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


def member_name(reference: str) -> str:
    """COPY / include reference -> member: 'LIB/QRPGLESRC,CUSTPR' -> CUSTPR, 'copy/custpr.rpgleinc' -> CUSTPR."""
    ref = unquote(reference.strip().rstrip("."))
    ref = ref.split(",")[-1]
    ref = re.split(r"[/\\]", ref)[-1]
    ref = re.sub(r"\.(rpgleinc|rpginc|rpgle|sqlrpgle|cpy|cbl|cob|copy|inc|mbr|txt)$", "", ref, flags=re.I)
    return ref.strip().upper()


_OPTION = re.compile(r"([A-Za-z*][\w#@$-]*)\s*\(")


def _paren_options(text: str) -> list[tuple[str, str]]:
    """KEY(value) pairs with nested parentheses and quoted values kept intact: PGM(LIB/X) PARM('a' &B)."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        m = _OPTION.search(text, i)
        if not m:
            break
        depth, j, quote = 0, m.end() - 1, ""
        while j < n:
            ch = text[j]
            if quote:
                if ch == quote:
                    quote = ""
            elif ch in "'\"":
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append((m.group(1).upper(), text[m.end() : j].strip()))
        i = j + 1
    return out


def _complexity(text: str, pattern: re.Pattern) -> int:
    return 1 + len(pattern.findall(text))


# --- COBOL ------------------------------------------------------------------------------------------------------------


@dataclass
class CobolSource:
    lines: list[str]
    area_a: list[bool]
    fixed: bool


def _looks_fixed(raw_lines: list[str]) -> bool:
    sample = [line for line in raw_lines[:400] if line.strip()]
    if not sample:
        return True
    if any(re.match(r"\s*>>\s*SOURCE\s+(?:FORMAT\s+)?(?:IS\s+)?FREE", line, re.I) for line in sample[:20]):
        return False
    fixed_like = sum(1 for line in sample if len(line) > 6 and re.match(r"^[\d ]{6}[ *\-/Dd$]", line) or len(line) <= 6 and not line[:6].strip("0123456789 "))
    long_lines = sum(1 for line in sample if len(line.rstrip()) > 80)
    return fixed_like >= 0.6 * len(sample) and long_lines < 0.2 * len(sample)


def cobol_source(text: str) -> CobolSource:
    """Code text per original line: sequence area, indicator column, identification area and comments removed."""
    raw_lines = text.splitlines()
    fixed = _looks_fixed(raw_lines)
    lines: list[str] = []
    area: list[bool] = []
    for raw in raw_lines:
        if re.match(r"\s*>>", raw) or re.match(r"\s*\*>", raw):
            lines.append("")
            area.append(False)
            continue
        if fixed:
            indicator = raw[6:7]
            if indicator in ("*", "/", "D", "d") or len(raw) <= 7:
                lines.append("")
                area.append(False)
                continue
            content = raw[7:72]
            is_a = indicator != "-" and content[:4].strip() != ""
        else:
            if raw.lstrip().startswith("*") and not raw.lstrip().startswith("*>"):
                content = ""
            else:
                content = raw
            is_a = bool(content) and not content[:1].isspace()
        content = re.sub(r"\*>.*$", "", content)
        if re.match(r"^\s*(EJECT|SKIP[123])\s*\.?\s*$", content, re.I):
            content = ""
        lines.append(content.rstrip())
        area.append(is_a)
    return CobolSource(lines, area, fixed)


COBOL_VERBS = {
    "ACCEPT", "ADD", "ALTER", "CALL", "CANCEL", "CLOSE", "COMPUTE", "CONTINUE", "DELETE", "DISPLAY", "DIVIDE", "ELSE", "END-IF",
    "END-EVALUATE", "END-PERFORM", "END-READ", "END-CALL", "EVALUATE", "EXEC", "EXIT", "GO", "GOBACK", "IF", "INITIALIZE", "INSPECT",
    "MERGE", "MOVE", "MULTIPLY", "OPEN", "PERFORM", "READ", "RELEASE", "RETURN", "REWRITE", "SEARCH", "SET", "SORT", "START", "STOP",
    "STRING", "SUBTRACT", "UNSTRING", "WHEN", "WRITE", "NOT", "AT", "INVALID", "ON", "END", "THEN", "OTHERWISE",
}
_RESERVED_PARA = {"EXIT", "GOBACK", "CONTINUE", "DECLARATIVES", "END", "ELSE", "STOP", "NEXT"}
_COBOL_BRANCH = re.compile(r"(?<![\w-])(?:IF|WHEN|UNTIL|VARYING|AND|OR)(?![\w-])", re.I)
_EXEC = re.compile(r"\bEXEC\s+(SQL|CICS|DLI|SQLIMS)\b([\s\S]*?)\bEND-EXEC\b", re.I)
_LEVEL = re.compile(r"^(\d{1,2})(?:\s+([A-Za-z0-9][\w-]*))?(.*)$", re.S)
_DATA_KEYWORDS = {"PIC", "PICTURE", "VALUE", "VALUES", "REDEFINES", "OCCURS", "USAGE", "COMP", "COMP-3", "COMP-1", "COMP-2", "COMP-4",
                  "COMP-5", "BINARY", "PACKED-DECIMAL", "DISPLAY", "INDEX", "POINTER", "SIGN", "JUSTIFIED", "JUST", "SYNC", "SYNCHRONIZED",
                  "BLANK", "EXTERNAL", "GLOBAL", "RENAMES"}
_SQL_SKIP_INCLUDES = {"SQLCA", "SQLDA"}
_FILE_OPS = {"OPEN INPUT": "R", "OPEN OUTPUT": "C", "OPEN I-O": "RU", "OPEN EXTEND": "C", "READ": "R", "START": "R", "WRITE": "C",
             "REWRITE": "U", "DELETE": "D", "SORT": "R", "MERGE": "R"}


def _ops(*codes: str) -> list[str]:
    found = set("".join(codes))
    return [op for op in "CRUD" if op in found]


def _cics_options(body: str) -> tuple[str, dict[str, str]]:
    words = body.split()
    if not words:
        return "", {}
    verb = words[0].upper().split("(")[0]
    if verb in ("SEND", "RECEIVE", "WRITEQ", "READQ", "DELETEQ", "HANDLE", "IGNORE", "PUSH", "POP", "GET", "PUT") and len(words) > 1:
        verb = f"{verb} {words[1].upper().split('(')[0]}"
    options = {k: v for k, v in _paren_options(body)}
    for word in words[1:]:
        if re.match(r"^[A-Za-z][\w-]*$", word):
            options.setdefault(word.upper(), "")
    return verb, options


def _sql_statement(body: str, line: int) -> dict:
    from app.services.analyzer.catalog import sql as sqlmod

    sql = " ".join(body.split())
    upper = sql.upper()
    entry: dict = {"line": line, "sql": sql[:600], "operation": "", "tables": {}}
    m = re.match(r"INCLUDE\s+([\w#@$-]+)", upper)
    if m:
        entry["operation"] = "INCLUDE"
        entry["include"] = m.group(1)
        return entry
    m = re.match(r"DECLARE\s+([\w#@$.\"-]+)\s+TABLE\s*\(", upper)
    if m:
        name = sqlmod.unquote(m.group(1))
        cols_text = sql[sql.index("(") :]
        parsed = sqlmod.parse_create_table(f"CREATE TABLE {name} {cols_text}")
        entry.update(operation="DECLARE TABLE", declares=name, columns=(parsed or {}).get("columns", []))
        return entry
    m = re.match(r"DECLARE\s+([\w#@$-]+)\s+(?:(?:INSENSITIVE|SENSITIVE\s+\w+|SCROLL|NO\s+SCROLL|ASENSITIVE)\s+)*CURSOR\b[\s\S]*?\bFOR\b\s*([\s\S]*)$", sql, re.I)
    if m:
        entry.update(operation="SELECT", cursor=m.group(1).upper(), tables=sqlmod.tables_in(m.group(2)))
        return entry
    m = re.match(r"CALL\s+([\w#@$.\"]+)", upper)
    if m:
        entry.update(operation="CALL", procedure=sqlmod.unquote(m.group(1)))
        return entry
    entry["operation"] = sqlmod.operation(sql)
    if entry["operation"] in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
        entry["tables"] = sqlmod.tables_in(re.sub(r":[\w-]+(?:\.[\w-]+)?", "?", sql))
    return entry


def _data_items(region_masked: str, region_orig: str, base_line: int, index: LineIndex, base_offset: int) -> tuple[list[dict], dict[str, list[str]], dict[str, str], list[tuple[str, str, int]]]:
    """Level entries, VALUE literals by name, record -> FD map and (FD/SD, name, line) from a DATA DIVISION region."""
    items: list[dict] = []
    values: dict[str, list[str]] = {}
    record_fd: dict[str, str] = {}
    fds: list[tuple[str, str, int]] = []
    section = ""
    current_fd = ""
    position = 0
    for m in re.finditer(r"\.(?=\s|$)", region_masked + " "):
        stmt_masked = region_masked[position : m.start()]
        stmt = region_orig[position : m.start()]
        start = position
        position = m.end()
        lead = len(stmt_masked) - len(stmt_masked.lstrip())
        stmt_masked, stmt = stmt_masked.strip(), stmt[lead:].strip()
        if not stmt_masked:
            continue
        line = index.line(base_offset + start + lead)
        sm = re.match(r"^([\w-]+)\s+SECTION$", stmt_masked, re.I)
        if sm:
            section = sm.group(1).upper()
            current_fd = ""
            continue
        fm = re.match(r"^(FD|SD|RD)\s+([\w-]+)", stmt_masked, re.I)
        if fm:
            current_fd = fm.group(2).upper()
            fds.append((fm.group(1).upper(), current_fd, line))
            continue
        if re.match(r"^COPY\b", stmt_masked, re.I):
            continue
        lm = _LEVEL.match(stmt_masked)
        if not lm:
            continue
        level = int(lm.group(1))
        if not (1 <= level <= 49 or level in (66, 77, 88)):
            continue
        name = (lm.group(2) or "FILLER").upper()
        if name in _DATA_KEYWORDS:
            name = "FILLER"
        flat = " ".join(stmt.split())
        item = {"level": level, "name": name, "line": line, "section": section}
        if current_fd and section == "FILE":
            item["fd"] = current_fd
            if level == 1:
                record_fd[name] = current_fd
        pic = re.search(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)", flat, re.I)
        if pic:
            item["pic"] = pic.group(1).upper()
        usage = re.search(r"\b(COMP(?:UTATIONAL)?(?:-[1-5X])?|BINARY|PACKED-DECIMAL|INDEX|POINTER)\b", flat, re.I)
        if usage:
            item["usage"] = usage.group(1).upper().replace("COMPUTATIONAL", "COMP")
        occurs = re.search(r"\bOCCURS\s+(\d+)(?:\s+TO\s+(\d+))?", flat, re.I)
        if occurs:
            item["occurs"] = int(occurs.group(2) or occurs.group(1))
        redefines = re.search(r"\bREDEFINES\s+([\w-]+)", flat, re.I)
        if redefines:
            item["redefines"] = redefines.group(1).upper()
        value = re.search(r"\bVALUES?\s+(?:IS\s+|ARE\s+)?(.+)$", flat, re.I)
        if value:
            raw_value = value.group(1).strip()
            item["value"] = raw_value[:120]
            literals = [unquote(x).strip() for x in _LIT.findall(raw_value)]
            if level != 88 and literals:
                values.setdefault(name, []).extend(literals[:1])
        items.append(item)
    return items, values, record_fd, fds


def _owner(paragraphs: list[dict], line_no: int) -> dict | None:
    found = None
    for para in paragraphs:
        if para["line"] <= line_no <= para["end"] and (found is None or para["kind"] == "paragraph"):
            found = para
    return found


def _cobol_unit(name: str | None, joined: str, masked: str, index: LineIndex, src: CobolSource, start: int, end: int) -> dict:
    seg_masked = masked[start:end]
    seg_orig = joined[start:end]
    unit: dict = {"name": name, "line": index.line(start), "end": index.line(max(start, end - 1)), "using": [], "linkage": [], "paragraphs": [],
                  "calls": [], "files": [], "sql": [], "cics": [], "dli": [], "screens": [], "transids": [], "queues": [], "copybooks": [],
                  "data_items": [], "ends": [], "complexity": 1, "sorts": []}
    # EXEC blocks first, then blank them so verbs inside CICS / SQL never read as COBOL statements
    for m in _EXEC.finditer(seg_masked):
        kind, body = m.group(1).upper(), seg_orig[m.start(2) : m.end(2)]
        line = index.line(start + m.start())
        if kind in ("SQL", "SQLIMS"):
            unit["sql"].append(_sql_statement(body, line))
        elif kind == "CICS":
            verb, options = _cics_options(body)
            unit["cics"].append({"command": verb, "options": options, "line": line})
        else:
            words = body.split()
            unit["dli"].append({"command": words[0].upper() if words else "", "options": {k: v for k, v in _paren_options(body)}, "line": line})
    clean = _EXEC.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), seg_masked)
    # COPY statements (anywhere in the unit)
    for m in re.finditer(r"(?<![\w-])COPY\s+([\w#@$-]+|'[^'\n]*'|\"[^\"\n]*\")(?:\s+(?:OF|IN)\s+([\w#@$-]+))?(\s+REPLACING\s+[\s\S]*?)?\s*\.", clean, re.I):
        literal = seg_orig[m.start(1) : m.end(1)]
        replacing = " ".join(seg_orig[m.start(3) : m.end(3)].split())[:200] if m.group(3) else ""
        unit["copybooks"].append({"name": member_name(literal), "library": (m.group(2) or "").upper(), "replacing": replacing.replace("REPLACING ", "", 1), "line": index.line(start + m.start()), "via": "COPY"})
    for sql in unit["sql"]:
        if sql.get("include") and sql["include"].upper() not in _SQL_SKIP_INCLUDES:
            unit["copybooks"].append({"name": member_name(sql["include"]), "library": "", "replacing": "", "line": sql["line"], "via": "EXEC SQL INCLUDE"})
    # divisions
    data_m = re.search(r"\bDATA\s+DIVISION\b", clean, re.I)
    proc_m = re.search(r"\bPROCEDURE\s+DIVISION\b([^.]*)\.", clean, re.I)
    if proc_m:
        using = re.search(r"\bUSING\s+([\s\S]*?)(?:\bRETURNING\b|$)", proc_m.group(1), re.I)
        if using:
            unit["using"] = [t.upper() for t in re.findall(r"[A-Za-z][\w-]*", using.group(1)) if t.upper() not in ("BY", "REFERENCE", "VALUE", "CONTENT")]
    data_start = data_m.end() if data_m else (0 if not proc_m else None)
    data_end = proc_m.start() if proc_m else len(clean)
    has_divisions = bool(data_m or proc_m or re.search(r"\b(IDENTIFICATION|ID|ENVIRONMENT)\s+DIVISION\b", clean, re.I))
    if not has_divisions:
        # copybook / fragment: data entries when it starts with a level number, procedure text otherwise
        first = next((line.strip() for line in clean.splitlines() if re.search(r"\w", line) and not re.match(r"^COPY\b", line.strip(), re.I)), "")
        if re.match(r"^\d{1,2}\s", first):
            data_start, data_end = 0, len(clean)
            proc_region = None
        else:
            data_start = None
            proc_region = (0, len(clean))
    else:
        proc_region = (proc_m.end(), len(clean)) if proc_m else None
    values: dict[str, list[str]] = {}
    record_fd: dict[str, str] = {}
    fds: list[tuple[str, str, int]] = []
    if data_start is not None:
        items, values, record_fd, fds = _data_items(clean[data_start:data_end], seg_orig[data_start:data_end], 0, index, start + data_start)
        unit["data_items"] = items
        unit["linkage"] = [i["name"] for i in items if i["section"] == "LINKAGE" and i["level"] in (1, 77)]
    # FILE-CONTROL
    env_end = data_m.start() if data_m else (proc_m.start() if proc_m else 0)
    files: dict[str, dict] = {}
    for m in re.finditer(r"\bSELECT\s+(?:OPTIONAL\s+)?([\w-]+)\s+ASSIGN\s+(?:TO\s+|USING\s+)?([\w-]+|'[^'\n]*'|\"[^\"\n]*\")([\s\S]*?)(?=\bSELECT\b|\bI-O-CONTROL\b|\bDATA\s+DIVISION\b|$)", clean[:env_end], re.I):
        name = m.group(1).upper()
        assign_raw = seg_orig[m.start(2) : m.end(2)]
        assign = unquote(assign_raw).upper() if is_literal(assign_raw) else assign_raw.upper()
        rest = " ".join(m.group(3).split())
        device = "file"
        external = assign
        pm = re.match(r"^(DATABASE|WORKSTATION|PRINTER|FORMATFILE|DISK|TAPE|DISKETTE|SPECIAL-NAMES)-(.+)$", assign)
        if pm:
            device = {"DATABASE": "database", "WORKSTATION": "workstation", "PRINTER": "printer", "FORMATFILE": "printer"}.get(pm.group(1), "file")
            external = pm.group(2)
        else:
            sm = re.match(r"^(?:UT|DA|AS|UR)?-?(?:S|D|R|I|W|V)?-(.+)$", assign)
            if sm and "-" in assign[:6]:
                external = sm.group(1)
            if re.search(r"\bINDEXED\b", rest, re.I):
                device = "vsam"
        organization = re.search(r"\bORGANIZATION\s+(?:IS\s+)?(LINE\s+SEQUENTIAL|SEQUENTIAL|INDEXED|RELATIVE)", rest, re.I)
        access = re.search(r"\bACCESS\s+(?:MODE\s+)?(?:IS\s+)?(SEQUENTIAL|RANDOM|DYNAMIC)", rest, re.I)
        key = re.search(r"\bRECORD\s+KEY\s+(?:IS\s+)?([\w-]+)", rest, re.I)
        status = re.search(r"\bFILE\s+STATUS\s+(?:IS\s+)?([\w-]+)", rest, re.I)
        files[name] = {"name": name, "assign": assign, "external": external, "device": device,
                       "organization": (organization.group(1).upper() if organization else ("INDEXED" if device == "vsam" else "SEQUENTIAL")),
                       "access": access.group(1).upper() if access else "", "record_key": key.group(1).upper() if key else "",
                       "status": status.group(1).upper() if status else "", "ops": set(), "records": [], "line": index.line(start + m.start())}
    for kind, fd_name, _line in fds:
        if fd_name in files:
            files[fd_name]["fd"] = kind
    for record, fd_name in record_fd.items():
        if fd_name in files:
            files[fd_name]["records"].append(record)
    if proc_region is not None:
        p0, p1 = proc_region
        proc = clean[p0:p1]
        proc_orig = seg_orig[p0:p1]
        proc_base = start + p0
        first_line = index.line(proc_base)
        proc_lines = proc.split("\n")
        # paragraphs and sections
        paragraphs: list[dict] = []
        current_section = ""
        for offset, text_line in enumerate(proc_lines):
            line_no = first_line + offset
            if not text_line.strip():
                continue
            src_index = line_no - 1
            if src.fixed and not (0 <= src_index < len(src.area_a) and src.area_a[src_index]):
                continue
            if src.fixed:
                m = re.match(r"^\s*([A-Za-z0-9][\w-]*)(\s+SECTION(?:\s+\d+)?)?\s*\.(?:\s|$)", text_line, re.I)
            else:
                m = re.match(r"^\s*([A-Za-z0-9][\w-]*)(\s+SECTION(?:\s+\d+)?)?\s*\.\s*$", text_line, re.I)
            if not m or m.group(1).upper() in _RESERVED_PARA or (offset == 0 and line_no == index.line(proc_base) and "DIVISION" in text_line.upper()):
                continue
            if re.match(r"^\d+$", m.group(1)) and not m.group(2):
                continue
            kind = "section" if m.group(2) else "paragraph"
            name_ = m.group(1).upper()
            if kind == "section":
                current_section = name_
            paragraphs.append({"name": name_, "kind": kind, "section": current_section if kind == "paragraph" else "", "line": line_no, "end": line_no,
                               "performs": [], "calls": [], "complexity": 1, "statements": 0})
        last_line = index.line(start + p1 - 1) if p1 > 0 else first_line
        for i, para in enumerate(paragraphs):
            if para["kind"] == "section":
                nxt = next((p for p in paragraphs[i + 1 :] if p["kind"] == "section"), None)
            else:
                nxt = paragraphs[i + 1] if i + 1 < len(paragraphs) else None
            para["end"] = (nxt["line"] - 1) if nxt else last_line
        para_names = {p["name"] for p in paragraphs}

        def owner(line_no: int) -> dict | None:
            return _owner(paragraphs, line_no)

        for para in paragraphs:
            body = "\n".join(proc_lines[para["line"] - first_line : para["end"] - first_line + 1])
            para["complexity"] = _complexity(body, _COBOL_BRANCH) - (1 if re.search(r"\bWHEN\s+OTHER\b", body, re.I) else 0)
            para["statements"] = len(re.findall(r"(?<![\w-])(?:MOVE|COMPUTE|ADD|SUBTRACT|MULTIPLY|DIVIDE|IF|EVALUATE|PERFORM|CALL|READ|WRITE|REWRITE|DISPLAY|STRING|SET|INITIALIZE)(?![\w-])", body, re.I))
        # MOVE literal TO identifiers (feeds dynamic CALL / CICS PROGRAM resolution)
        for m in re.finditer(r"\bMOVE\s+('(?:[^'\n]|'')*'|\"[^\"\n]*\")\s+TO\s+(?=([\s\S]{0,200}))", proc, re.I):
            literal = unquote(proc_orig[m.start(1) : m.end(1)]).strip()
            if not literal:
                continue
            for token in re.findall(r"[A-Za-z][\w-]*|\S", m.group(2)):
                if token.upper() in COBOL_VERBS or not re.match(r"^[A-Za-z][\w-]*$", token):
                    break
                values.setdefault(token.upper(), [])
                if literal not in values[token.upper()]:
                    values[token.upper()].append(literal)
        unit["values"] = {k: v for k, v in values.items() if v}

        def resolve(token: str) -> tuple[list[str], bool]:
            if is_literal(token):
                return [unquote(token).upper()], False
            return [v.upper() for v in values.get(token.upper(), [])], True

        for m in re.finditer(r"\bPERFORM\s+([A-Za-z0-9][\w-]*)(?:\s+(?:THRU|THROUGH)\s+([A-Za-z0-9][\w-]*))?", proc, re.I):
            line_no = first_line + proc.count("\n", 0, m.start())
            para = owner(line_no)
            for target in (m.group(1), m.group(2)):
                if target and target.upper() in para_names and para is not None and target.upper() not in para["performs"]:
                    para["performs"].append(target.upper())
        for m in re.finditer(r"\bGO\s+TO\s+([A-Za-z0-9][\w-]*(?:[ \t\n,]+[A-Za-z0-9][\w-]*){0,40})", proc, re.I):
            line_no = first_line + proc.count("\n", 0, m.start())
            para = owner(line_no)
            for target in re.findall(r"[A-Za-z0-9][\w-]*", m.group(1)):
                if target.upper() in COBOL_VERBS or target.upper() == "DEPENDING":
                    break
                if target.upper() in para_names and para is not None:
                    para.setdefault("go_to", [])
                    if target.upper() not in para["go_to"]:
                        para["go_to"].append(target.upper())
        for m in re.finditer(r"(?<![\w-])CALL\s+('(?:[^'\n]|'')*'|\"[^\"\n]*\"|[A-Za-z][\w-]*)", proc, re.I):
            token = proc_orig[m.start(1) : m.end(1)]
            if token.upper() in ("PROCEDURE",):
                continue
            line_no = first_line + proc.count("\n", 0, m.start())
            targets, dynamic = resolve(token)
            para = owner(line_no)
            if not targets:
                unit["calls"].append({"target": "", "variable": token.upper(), "dynamic": True, "via": "CALL (dynamic)", "line": line_no, "paragraph": para["name"] if para else ""})
            for target in targets:
                unit["calls"].append({"target": target, "variable": token.upper() if dynamic else "", "dynamic": dynamic, "via": "CALL (dynamic)" if dynamic else "CALL",
                                      "line": line_no, "paragraph": para["name"] if para else "", "system": is_system_program(target)})
                if para is not None and target not in para["calls"]:
                    para["calls"].append(target)
        # file I/O verbs
        for m in re.finditer(r"\bOPEN((?:\s+(?:INPUT|OUTPUT|I-O|EXTEND)(?:\s+(?!INPUT\b|OUTPUT\b|I-O\b|EXTEND\b)[A-Za-z][\w-]*)+)+)", proc, re.I):
            mode = ""
            for token in m.group(1).split():
                upper = token.upper()
                if upper in ("INPUT", "OUTPUT", "I-O", "EXTEND"):
                    mode = upper
                elif upper in files:
                    files[upper]["ops"].update(_FILE_OPS[f"OPEN {mode}"])
        for m in re.finditer(r"(?<![\w-])(READ|START|WRITE|REWRITE|DELETE|SORT|MERGE)\s+([A-Za-z][\w-]*)", proc, re.I):
            verb, target = m.group(1).upper(), m.group(2).upper()
            fd_name = target if target in files else record_fd.get(target)
            if fd_name and fd_name in files:
                files[fd_name]["ops"].update(_FILE_OPS[verb])
            if verb in ("SORT", "MERGE"):
                tail = proc[m.end() : m.end() + 400]
                using = re.findall(r"\b(USING|GIVING)\s+((?:[A-Za-z][\w-]*\s*)+)", tail, re.I)
                for clause, names in using:
                    for token in names.split():
                        if token.upper() in files:
                            files[token.upper()]["ops"].update("R" if clause.upper() == "USING" else "C")
                unit["sorts"].append({"file": target, "line": first_line + proc.count("\n", 0, m.start())})
        for m in re.finditer(r"(?<![\w-])(GOBACK|STOP\s+RUN|EXIT\s+PROGRAM)(?![\w-])", proc, re.I):
            kind = " ".join(m.group(1).upper().split())
            if kind not in unit["ends"]:
                unit["ends"].append(kind)
        unit["paragraphs"] = paragraphs
        unit["complexity"] = _complexity(proc, _COBOL_BRANCH)
    # CICS -> programs, maps, files, transactions, queues
    for command in unit["cics"]:
        verb, options, line = command["command"], command["options"], command["line"]

        def opt(key: str) -> tuple[list[str], bool, str]:
            raw = options.get(key, "")
            if not raw:
                return [], False, ""
            if is_literal(raw):
                return [unquote(raw).strip().upper()], False, raw
            return [v.upper() for v in values.get(raw.upper(), [])], True, raw.upper()

        if verb in ("LINK", "XCTL", "LOAD"):
            targets, dynamic, raw = opt("PROGRAM")
            for target in targets or [""]:
                unit["calls"].append({"target": target, "variable": raw if dynamic else "", "dynamic": dynamic, "via": f"EXEC CICS {verb}", "line": line,
                                      "paragraph": "", "system": bool(target) and is_system_program(target)})
        elif verb in ("SEND MAP", "RECEIVE MAP"):
            maps, _, map_raw = opt("MAP")
            mapsets, _, _ = opt("MAPSET")
            for map_name in maps or ([map_raw] if map_raw else []):
                unit["screens"].append({"map": map_name, "mapset": (mapsets[0] if mapsets else map_name), "op": "send" if verb == "SEND MAP" else "receive", "line": line})
        elif verb in ("READ", "WRITE", "REWRITE", "DELETE", "STARTBR", "READNEXT", "READPREV", "RESETBR", "ENDBR", "UNLOCK"):
            names, _, raw = opt("FILE") if "FILE" in options else opt("DATASET")
            for file_name in names or ([raw] if raw else []):
                key = f"CICS:{file_name}"
                entry = files.setdefault(key, {"name": file_name, "assign": file_name, "external": file_name, "device": "cics", "organization": "VSAM (CICS file)",
                                               "access": "", "record_key": "", "status": "", "ops": set(), "records": [], "line": line})
                entry["ops"].update({"READ": "R", "STARTBR": "R", "READNEXT": "R", "READPREV": "R", "RESETBR": "R", "WRITE": "C", "REWRITE": "U", "DELETE": "D"}.get(verb, ""))
        elif verb in ("RETURN", "START"):
            ids, _, raw = opt("TRANSID")
            for transid in ids or ([raw] if raw else []):
                unit["transids"].append({"id": transid, "via": verb, "line": line})
        elif verb.startswith(("WRITEQ", "READQ", "DELETEQ")):
            names, _, raw = opt("QUEUE") if "QUEUE" in options else opt("QNAME")
            for queue in names or ([raw] if raw else []):
                unit["queues"].append({"name": queue, "kind": "TD" if verb.endswith("TD") else "TS", "op": "write" if verb.startswith("WRITEQ") else "read", "line": line})
    for command in unit["dli"]:
        segment = command["options"].get("SEGMENT", "")
        if segment:
            name_ = unquote(segment).upper()
            key = f"IMS:{name_}"
            entry = files.setdefault(key, {"name": name_, "assign": name_, "external": name_, "device": "ims", "organization": "IMS segment", "access": "", "record_key": "",
                                           "status": "", "ops": set(), "records": [], "line": command["line"]})
            entry["ops"].update({"GU": "R", "GHU": "R", "GN": "R", "GHN": "R", "GNP": "R", "GHNP": "R", "ISRT": "C", "REPL": "U", "DLET": "D"}.get(command["command"], "R"))
    for call in unit["calls"]:
        if not call.get("paragraph"):
            para = _owner(unit["paragraphs"], call["line"])
            call["paragraph"] = para["name"] if para else ""
            if para is not None and call["target"] and call["target"] not in para["calls"]:
                para["calls"].append(call["target"])
    unit["files"] = [dict(f, ops=_ops(*f["ops"])) for f in files.values()]
    unit["has_cics"] = bool(unit["cics"])
    unit["has_sql"] = any(s["operation"] not in ("INCLUDE", "") for s in unit["sql"])
    unit["has_dli"] = bool(unit["dli"]) or any(c["target"] in ("CBLTDLI", "AIBTDLI") for c in unit["calls"])
    unit["loc"] = sum(1 for line in seg_orig.splitlines() if line.strip())
    return unit


def parse_cobol(text: str) -> dict:
    src = cobol_source(text)
    joined = "\n".join(src.lines)
    masked = mask_literals(joined)
    index = LineIndex(joined)
    starts = []
    for m in re.finditer(r"\bPROGRAM-ID\s*\.?\s*([\w#@$-]+|'[^'\n]*'|\"[^\"\n]*\")", masked, re.I):
        name = unquote(joined[m.start(1) : m.end(1)]).upper()
        begin = m.start()
        head = re.search(r"\b(?:IDENTIFICATION|ID)\s+DIVISION\s*\.\s*$", masked[max(0, begin - 200) : begin], re.I)
        if head:
            begin = max(0, begin - 200) + head.start()
        starts.append((begin, name))
    units = []
    if not starts:
        units.append(_cobol_unit(None, joined, masked, index, src, 0, len(joined)))
    else:
        bounds = [s for s, _ in starts] + [len(joined)]
        for i, (begin, name) in enumerate(starts):
            units.append(_cobol_unit(name, joined, masked, index, src, 0 if i == 0 else begin, bounds[i + 1]))
    includes = []
    for i, raw in enumerate(text.splitlines()):
        m = re.match(r"^\s*(?:\+\+INCLUDE|-INC)\s+([\w#@$-]+)", raw, re.I)
        if m:
            includes.append({"name": m.group(1).upper(), "library": "", "replacing": "", "line": i + 1, "via": "++INCLUDE" if "+" in raw[:6] else "-INC"})
    if includes:
        units[0]["copybooks"] += includes
    dclgen = []
    for unit in units:
        for sql in unit["sql"]:
            if sql.get("declares"):
                dclgen.append({"table": sql["declares"], "columns": sql.get("columns", []), "line": sql["line"]})
    is_copybook = not starts and not re.search(r"\bPROCEDURE\s+DIVISION\b", masked, re.I)
    return {"format": "fixed" if src.fixed else "free", "units": units, "copybook": is_copybook, "dclgen": dclgen, "source": src}


# --- RPG ------------------------------------------------------------------------------------------------------------

_RPG_OPCODE = re.compile(
    r"^(CALLB?|CALLP|PARM|PLIST|BEGSR|ENDSR|EXSR|CAS(?:EQ|NE|GT|LT|GE|LE)?|CHAIN|READ[EPC]?|READPE|SETLL|SETGT|WRITE|UPDATE?|UPDAT|DELETE?|DELET|"
    r"EXFMT|EXCPT|OPEN|CLOSE|FEOD|UNLOCK|IF(?:EQ|NE|GT|LT|GE|LE)?|ELSE(?:IF)?|DO[UW]?(?:EQ|NE|GT|LT|GE|LE)?|FOR|SELECT|WH(?:EN|EQ|NE|GT|LT|GE|LE)|OTHER|"
    r"END\w*|EVALR?|EVAL-CORR|MOVEL?|MOVEA|Z-ADD|Z-SUB|ADD|SUB|MULT|DIV|MVR|SQRT|XFOOT|CAT|SCAN|SUBST|CHECKR?|XLATE|COMP|TESTN?|TESTB|TESTZ|TIME|"
    r"CLEAR|RESET|LOOKUP|SORTA|DSPLY|RETURN|LEAVE(?:SR)?|ITER|GOTO|TAG|SETON|SETOFF|COMMIT|ROLBK|IN|OUT|DEFINE|KFLD|KLIST|EXTRCT|ADDDUR|SUBDUR|"
    r"BITON|BITOFF|MONITOR|ON-ERROR|ENDMON|NEXT|ACQ|REL|POST|SHTDN|DUMP|DEALLOC|ALLOC|REALLOC|OCCUR|CAB(?:EQ|NE|GT|LT|GE|LE)|AND(?:EQ|NE|GT|LT|GE|LE)|"
    r"OR(?:EQ|NE|GT|LT|GE|LE)|ELSEIF|MHHZO|MHLZO|MLHZO|MLLZO|DOWEQ)(\([A-Z]*\))?$",
    re.I,
)
_RPG_BRANCH_FREE = re.compile(r"(?<![\w-])(?:if|elseif|when|dow|dou|for|on-error|and|or)(?![\w-])", re.I)
_RPG_BRANCH_FIXED = re.compile(r"^(?:IF\w*|ELSEIF|DO[UW]\w*|WH\w+|CAS\w*|FOR|AND\w+|OR\w+|CAB\w+|ON-ERROR)$", re.I)
_RPG_FILE_OPS = {"CHAIN": "R", "READ": "R", "READE": "R", "READP": "R", "READPE": "R", "READC": "R", "SETLL": "R", "SETGT": "R", "WRITE": "C",
                 "UPDATE": "U", "UPDAT": "U", "DELETE": "D", "DELET": "D", "EXFMT": "", "OPEN": "", "CLOSE": "", "UNLOCK": "", "FEOD": ""}
_USAGE_OPS = {"*INPUT": "R", "*OUTPUT": "C", "*UPDATE": "RU", "*DELETE": "D"}


def _rpg_file_usage(type_letter: str, device: str, designation: str = "") -> set[str]:
    letter = (type_letter or "").upper()
    if device == "WORKSTN":
        return set()
    return set({"I": "R", "O": "C", "U": "RUD", "C": "RC"}.get(letter, "R"))


def parse_rpg(text: str, path: str = "") -> dict:
    raw_lines = text.splitlines()
    first = next((line.strip().upper() for line in raw_lines if line.strip()), "")
    fully_free = first.startswith("**FREE")
    out: dict = {"dialect": "", "files": [], "prototypes": {}, "entry": [], "procedures": [], "subroutines": [], "calls": [], "copybooks": [], "sql": [],
                 "fileops": [], "candidate_calls": [], "complexity": 1, "loc": 0, "plists": {}}
    files: dict[str, dict] = {}
    free_statements: list[tuple[int, str]] = []
    fixed_seen = free_seen = False
    rpg3 = PurePosixPath(path).suffix.lower() in (".rpg", ".rpg38", ".rpg36")
    in_free_block = False
    sql_buffer: list[str] = []
    sql_line = 0
    pending = ""
    pending_line = 0
    current_d: dict | None = None
    in_pi = False
    entry_plist = False
    fixed_complexity = 0
    for i, raw in enumerate(raw_lines):
        line_no = i + 1
        if raw.strip():
            out["loc"] += 1
        if not fully_free and raw.startswith("**") and i > 0 and not raw.upper().startswith("**FREE"):
            break  # compile-time data
        if fully_free and raw.strip().upper().startswith("**") and i > 0 and not raw.strip().upper().startswith("**FREE"):
            break
        directive = re.match(r"^.{0,6}?\s*/(COPY|INCLUDE)\s+(\S+)", raw if not fully_free else raw.strip(), re.I)
        if directive and not (len(raw) > 6 and raw[6:7] == "*" and not fully_free):
            out["copybooks"].append({"name": member_name(directive.group(2)), "reference": directive.group(2), "line": line_no, "via": f"/{directive.group(1).upper()}"})
            continue
        if fully_free:
            content = raw
        else:
            spec = raw[5:6].upper() if len(raw) > 5 else ""
            if len(raw) > 6 and raw[6] == "*":
                continue
            marker = raw[6:].lstrip().upper()[:9] if len(raw) > 6 else ""
            if marker.startswith("/FREE"):
                in_free_block = True
                continue
            if marker.startswith("/END-FREE"):
                in_free_block = False
                continue
            if spec == "C" and raw[6:8] in ("/E", "/e") and re.match(r"/EXEC\s+SQL", raw[6:].upper()):
                sql_buffer, sql_line = [raw[6:].split(None, 2)[2] if len(raw[6:].split(None, 2)) > 2 else ""], line_no
                continue
            if spec == "C" and raw[6:8] == "+ " or (spec == "C" and raw[6:7] == "+"):
                sql_buffer.append(raw[7:80])
                continue
            if spec == "C" and re.match(r"/END-EXEC", raw[6:].upper()):
                out["sql"].append(_sql_statement(" ".join(sql_buffer), sql_line))
                sql_buffer = []
                continue
            if in_free_block or (spec == "" or spec == " ") and raw[6:7] in ("", " ") and raw[7:].strip():
                content = raw[7:80] if not in_free_block else raw[7:]
                free_seen = True
            elif spec in ("H", "F", "D", "I", "C", "O", "P"):
                fixed_seen = True
                content = None
                body = raw[:80].ljust(80)
                if spec == "F":
                    current_d = None
                    name = body[6:16].strip().upper()
                    if rpg3:
                        name = body[6:14].strip().upper()
                    m = re.search(r"\b(DISK|WORKSTN|PRINTER|SEQ|SPECIAL)\b", body, re.I)
                    if name:
                        type_letter = (body[14] if rpg3 else body[16]).upper()
                        designation = (body[15] if rpg3 else body[17]).upper()
                        if type_letter not in "IOUC":
                            fm = re.match(r"^(\w+)\s*([IOUC])([PSFTRE ])", body[6:], re.I)
                            type_letter = fm.group(2).upper() if fm else "I"
                        device = m.group(1).upper() if m else "DISK"
                        files[name] = {"name": name, "type": type_letter, "device": device, "ops": _rpg_file_usage(type_letter, device, designation),
                                       "keyed": "K" in body[30:36].upper() or bool(re.search(r"\bK\s+(DISK)", body, re.I)), "line": line_no, "sfile": [], "rename": {},
                                       "external": name, "usropn": False}
                        last_file = name
                    else:
                        last_file = next(reversed(files), "") if files else ""
                    target = files.get(name or last_file)
                    if target is not None:
                        keywords = body[43:80] if not rpg3 else body[52:80]
                        for km in re.finditer(r"K?SFILE\s*\(?\s*(\w+)", body[40:], re.I):
                            target["sfile"].append(km.group(1).upper())
                        for km in re.finditer(r"RENAME\s*\(\s*(\w+)\s*:\s*(\w+)\s*\)", keywords, re.I):
                            target["rename"][km.group(1).upper()] = km.group(2).upper()
                        km = re.search(r"EXT(?:FILE|DESC)\s*\(\s*'([^']+)'\s*\)", keywords, re.I)
                        if km:
                            target["external"] = km.group(1).split("/")[-1].upper()
                        if re.search(r"\bUSROPN\b", keywords, re.I):
                            target["usropn"] = True
                elif spec == "D":
                    name = body[6:21].strip().rstrip(".").upper()
                    decl = body[23:25].strip().upper()
                    keywords = body[43:80]
                    if decl in ("PR", "PI", "DS", "S", "C"):
                        in_pi = decl == "PI"
                        current_d = {"name": name, "decl": decl, "line": line_no}
                        if decl == "PR":
                            out["prototypes"][name] = {"name": name, "line": line_no, "extpgm": "", "extproc": ""}
                    elif in_pi and name:
                        out["entry"].append(name)
                    if current_d is not None and current_d["decl"] == "PR":
                        km = re.search(r"EXTPGM\s*(?:\(\s*('[^']*'|\w+)\s*\))?", keywords, re.I)
                        if km:
                            out["prototypes"][current_d["name"]]["extpgm"] = unquote(km.group(1)).upper() if km.group(1) else current_d["name"]
                        km = re.search(r"EXTPROC\s*\(\s*('[^']*'|[\w*]+)", keywords, re.I)
                        if km:
                            out["prototypes"][current_d["name"]]["extproc"] = unquote(km.group(1)).upper()
                elif spec == "P":
                    name = body[6:21].strip().upper()
                    be = body[23:24].upper()
                    if be == "B" and name:
                        out["procedures"].append({"name": name, "line": line_no, "end": line_no, "exported": bool(re.search(r"\bEXPORT\b", body[43:], re.I)), "complexity": 1})
                    elif be == "E" and out["procedures"]:
                        out["procedures"][-1]["end"] = line_no
                elif spec == "C":
                    op_iv = body[25:35].strip().upper()
                    op_iii = body[27:32].strip().upper()
                    if _RPG_OPCODE.match(op_iv) and not rpg3:
                        f1, op, f2, result, ext = body[11:25].strip(), op_iv, body[35:49].strip(), body[49:63].strip(), body[35:80].strip()
                    elif _RPG_OPCODE.match(op_iii):
                        f1, op, f2, result, ext = body[17:27].strip(), op_iii, body[32:42].strip(), body[42:48].strip(), body[32:80].strip()
                    else:
                        continue
                    base = re.sub(r"\(\w*\)$", "", op)
                    if _RPG_BRANCH_FIXED.match(base) and not base.startswith("END"):
                        fixed_complexity += 1
                    if base in ("CALL", "CALLB"):
                        out["calls"].append({"target": unquote(f2).upper() if is_literal(f2) else "", "variable": "" if is_literal(f2) else f2.upper(),
                                             "dynamic": not is_literal(f2), "via": base, "line": line_no, "system": is_literal(f2) and is_system_program(unquote(f2))})
                    elif base == "CALLP":
                        cm = re.match(r"([\w#@$]+)", ext)
                        if cm:
                            out["candidate_calls"].append((cm.group(1).upper(), line_no, "CALLP"))
                    elif base == "PLIST":
                        entry_plist = f1.upper() == "*ENTRY"
                    elif base == "PARM" and entry_plist and result:
                        out["entry"].append(result.upper())
                    elif base == "BEGSR":
                        entry_plist = False
                        out["subroutines"].append({"name": f1.upper(), "line": line_no, "end": line_no, "performs": [], "complexity": 1})
                    elif base == "ENDSR" and out["subroutines"]:
                        out["subroutines"][-1]["end"] = line_no
                    elif base == "EXSR" or base.startswith("CAS"):
                        target = (f2 if base == "EXSR" else result).upper()
                        out.setdefault("exsr", []).append((target, line_no))
                    elif base in _RPG_FILE_OPS:
                        out["fileops"].append({"op": base, "tokens": [t.upper() for t in (f2, result) if t], "line": line_no})
                    else:
                        entry_plist = entry_plist and base == "PARM"
                    if base in ("EVAL", "IF", "DOW", "DOU", "WHEN", "ELSEIF", "RETURN", "FOR", "EVALR"):
                        for cm in re.finditer(r"([A-Za-z#@$][\w#@$]*)\s*\(", mask_literals(ext)):
                            out["candidate_calls"].append((cm.group(1).upper(), line_no, "expression"))
                continue
            else:
                continue
        # free-form text: strip // comments and gather ';'-terminated statements
        code = mask_literals(content)
        cut = code.find("//")
        if cut >= 0:
            content = content[:cut]
        if fully_free and content.strip().upper().startswith("**FREE"):
            continue
        if not content.strip():
            continue
        if not pending:
            pending_line = line_no
        pending += " " + content.strip()
        masked = mask_literals(pending)
        while ";" in masked:
            idx = masked.index(";")
            statement = pending[:idx].strip()
            if statement:
                free_statements.append((pending_line, statement))
            pending = pending[idx + 1 :]
            masked = masked[idx + 1 :]
            pending_line = line_no
    if fully_free or free_seen and not fixed_seen:
        out["dialect"] = "ILE RPG (free)"
    elif fixed_seen and free_seen:
        out["dialect"] = "RPG IV (mixed)"
    elif rpg3:
        out["dialect"] = "RPG III"
    else:
        out["dialect"] = "RPG IV (fixed)"
    # free-form statements
    block = ""
    proc_open = False
    free_complexity = 0
    for line_no, statement in free_statements:
        masked = mask_literals(statement)
        lower = masked.strip().lower()
        free_complexity += len(_RPG_BRANCH_FREE.findall(re.sub(r"\b(?:end-?\w+|or-|and-)\b", " ", lower)))
        word = lower.split()[0] if lower.split() else ""
        if word == "exec" and lower.startswith("exec sql"):
            out["sql"].append(_sql_statement(statement.strip()[8:].strip(), line_no))
            continue
        if word == "dcl-f":
            m = re.match(r"dcl-f\s+([\w#@$]+)(.*)$", statement.strip(), re.I | re.S)
            if m:
                name, rest = m.group(1).upper(), m.group(2)
                device = next((d.upper() for d in ("workstn", "printer", "seq", "special", "disk") if re.search(rf"\b{d}\b", rest, re.I)), "DISK")
                usage = re.search(r"usage\s*\(([^)]*)\)", rest, re.I)
                if usage:
                    ops: set[str] = set()
                    for token in re.findall(r"\*\w+", usage.group(1)):
                        ops.update(_USAGE_OPS.get(token.upper(), ""))
                else:
                    ops = set() if device == "WORKSTN" else ({"C"} if device == "PRINTER" else {"R"})
                if device == "WORKSTN":
                    ops = set()
                ext = re.search(r"ext(?:file|desc)\s*\(\s*'([^']+)'", rest, re.I)
                files[name] = {"name": name, "type": "", "device": device, "ops": ops, "keyed": bool(re.search(r"\bkeyed\b", rest, re.I)), "line": line_no,
                               "sfile": [s.upper() for s in re.findall(r"sfile\s*\(\s*(\w+)", rest, re.I)],
                               "rename": {a.upper(): b.upper() for a, b in re.findall(r"rename\s*\(\s*(\w+)\s*:\s*(\w+)", rest, re.I)},
                               "external": ext.group(1).split("/")[-1].upper() if ext else name, "usropn": bool(re.search(r"\busropn\b", rest, re.I))}
            continue
        if word == "dcl-pr":
            m = re.match(r"dcl-pr\s+([\w#@$]+)(.*)$", statement.strip(), re.I | re.S)
            if m:
                name, rest = m.group(1).upper(), m.group(2)
                proto = {"name": name, "line": line_no, "extpgm": "", "extproc": ""}
                km = re.search(r"extpgm\s*(?:\(\s*('[^']*'|[\w#@$]+)\s*\))?", rest, re.I)
                if km:
                    proto["extpgm"] = unquote(km.group(1)).upper() if km.group(1) else name
                km = re.search(r"extproc\s*\(\s*('[^']*'|[\w*#@$]+)", rest, re.I)
                if km:
                    proto["extproc"] = unquote(km.group(1)).upper()
                out["prototypes"][name] = proto
                block = "" if re.search(r"\bend-pr\b", rest, re.I) else "pr"
            continue
        if word == "dcl-pi":
            block = "" if re.search(r"\bend-pi\b", lower) else "pi"
            continue
        if word in ("end-pr", "end-pi", "end-ds"):
            block = ""
            continue
        if word == "dcl-ds":
            block = "" if re.search(r"\b(likeds|likerec|end-ds)\b", lower) else "ds"
            continue
        if block == "pi":
            m = re.match(r"(?:dcl-parm\s+)?([\w#@$]+)", statement.strip(), re.I)
            if m:
                if proc_open and out["procedures"]:
                    out["procedures"][-1].setdefault("params", []).append(m.group(1).upper())
                else:
                    out["entry"].append(m.group(1).upper())
            continue
        if block in ("pr", "ds"):
            continue
        if word == "dcl-proc":
            m = re.match(r"dcl-proc\s+([\w#@$]+)(.*)$", statement.strip(), re.I)
            if m:
                out["procedures"].append({"name": m.group(1).upper(), "line": line_no, "end": line_no, "exported": "export" in m.group(2).lower(), "complexity": 1})
                proc_open = True
            continue
        if word == "end-proc":
            proc_open = False
            if out["procedures"]:
                out["procedures"][-1]["end"] = line_no
            continue
        if word == "begsr":
            parts = statement.split()
            if len(parts) > 1:
                out["subroutines"].append({"name": parts[1].upper(), "line": line_no, "end": line_no, "performs": [], "complexity": 1})
            continue
        if word == "endsr":
            if out["subroutines"]:
                out["subroutines"][-1]["end"] = line_no
            continue
        if word == "exsr":
            parts = statement.split()
            if len(parts) > 1:
                out.setdefault("exsr", []).append((parts[1].upper(), line_no))
            continue
        base = re.sub(r"\(\w*\)$", "", word).upper()
        if base == "CALLP":
            m = re.match(r"callp(?:\(\w*\))?\s+([\w#@$]+)", statement.strip(), re.I)
            if m:
                out["candidate_calls"].append((m.group(1).upper(), line_no, "CALLP"))
            continue
        if base in _RPG_FILE_OPS:
            tokens = re.findall(r"[A-Za-z#@$][\w#@$]*", re.sub(r"\([^)]*\)", " ", masked.split(None, 1)[1] if len(masked.split(None, 1)) > 1 else ""))
            out["fileops"].append({"op": base, "tokens": [t.upper() for t in tokens], "line": line_no})
            continue
        for m in re.finditer(r"([A-Za-z#@$][\w#@$]*)\s*\(", masked):
            out["candidate_calls"].append((m.group(1).upper(), line_no, "expression"))
        if re.match(r"^[A-Za-z#@$][\w#@$]*\s*$", masked.strip()):
            out["candidate_calls"].append((masked.strip().upper(), line_no, "CALLP"))
    out["complexity"] = 1 + free_complexity + fixed_complexity
    # prototype calls -> programs / bound procedures
    local_procs = {p["name"] for p in out["procedures"]}
    seen: set[tuple[str, int]] = set()
    for name, line_no, via in out["candidate_calls"]:
        proto = out["prototypes"].get(name)
        if proto is None or (name, line_no) in seen:
            continue
        seen.add((name, line_no))
        if proto["extpgm"]:
            out["calls"].append({"target": proto["extpgm"], "variable": "", "dynamic": False, "via": f"CALLP {name} → EXTPGM", "line": line_no, "system": is_system_program(proto["extpgm"])})
        elif name not in local_procs:
            out.setdefault("bound_calls", []).append({"target": proto["extproc"] or name, "line": line_no})
    for name, line_no, _via in out["candidate_calls"]:
        if name in local_procs and name not in out["prototypes"]:
            out.setdefault("local_calls", []).append({"target": name, "line": line_no})
    for target, line_no in out.get("exsr", []):
        for sub in out["subroutines"] + out["procedures"]:
            if sub["line"] <= line_no <= sub["end"] and "performs" in sub and target not in sub["performs"]:
                sub["performs"].append(target)
    lines = text.splitlines()
    for sub in out["subroutines"] + out["procedures"]:
        body = "\n".join(lines[sub["line"] - 1 : sub["end"]])
        sub["complexity"] = 1 + len(_RPG_BRANCH_FREE.findall(re.sub(r"\b(?:end-?\w+)\b", " ", mask_literals(body).lower())))
    out["files"] = list(files.values())
    del out["candidate_calls"]
    return out


# --- CL -------------------------------------------------------------------------------------------------------------

_CL_BRANCH = re.compile(r"(?<![\w&])(?:IF|DOWHILE|DOUNTIL|DOFOR|WHEN|MONMSG|\*AND|\*OR)(?![\w])", re.I)


def _cl_statements(text: str) -> list[tuple[int, str]]:
    clean = re.sub(r"/\*[\s\S]*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    statements: list[tuple[int, str]] = []
    buffer, start = "", 0
    for i, raw in enumerate(clean.splitlines()):
        line = raw.rstrip()
        if not line.strip() and not buffer:
            continue
        if not buffer:
            start = i + 1
        if line.endswith("+"):
            buffer += line[:-1].strip() + " "
            continue
        if line.endswith("-"):
            buffer += line[:-1].rstrip() + " "
            continue
        buffer += line.strip()
        statements.append((start, buffer.strip()))
        buffer = ""
    if buffer.strip():
        statements.append((start, buffer.strip()))
    return statements


def _cl_object(value: str) -> str:
    return unquote(value.strip()).split("/")[-1].strip().upper()


def parse_cl(text: str) -> dict:
    out: dict = {"dialect": "CL", "entry": [], "calls": [], "files": [], "screens": [], "sql": [], "subroutines": [], "copybooks": [], "overrides": [],
                 "complexity": 1, "loc": sum(1 for line in text.splitlines() if line.strip())}
    files: dict[str, dict] = {}
    statements = _cl_statements(text)
    for line_no, statement in statements:
        m = re.match(r"^(?:([\w#@$]+)\s*:\s*)?([\w#@$]+)\b(.*)$", statement)
        if not m:
            continue
        command, rest = m.group(2).upper(), m.group(3)
        options = dict(_paren_options(rest))
        out["complexity"] += len(_CL_BRANCH.findall(command + " " + rest))
        if command == "PGM":
            out["entry"] = [p.upper() for p in re.findall(r"&[\w#@$]+", options.get("PARM", ""))]
        elif command in ("CALL", "TFRCTL"):
            target = options.get("PGM") or (rest.split()[0] if rest.split() else "")
            dynamic = target.strip().startswith("&")
            name = "" if dynamic else _cl_object(target)
            out["calls"].append({"target": name, "variable": target.upper() if dynamic else "", "dynamic": dynamic, "via": command, "line": line_no,
                                 "system": bool(name) and is_system_program(name)})
        elif command == "CALLPRC":
            out.setdefault("bound_calls", []).append({"target": _cl_object(options.get("PRC", "")), "line": line_no})
        elif command == "SBMJOB":
            inner = options.get("CMD", "")
            im = re.match(r"\s*CALL\s+(?:PGM\()?\s*([^\s)]+)", inner, re.I)
            if im:
                name = _cl_object(im.group(1))
                out["calls"].append({"target": name, "variable": "", "dynamic": False, "via": f"SBMJOB {_cl_object(options.get('JOB', ''))}".strip(), "line": line_no,
                                     "system": is_system_program(name)})
        elif command in ("DCLF",):
            name = _cl_object(options.get("FILE", ""))
            files.setdefault(name, {"name": name, "ops": set(), "line": line_no, "via": "DCLF", "device": "", "formats": set()})
        elif command in ("SNDRCVF", "SNDF", "RCVF"):
            fmt = _cl_object(options.get("RCDFMT", ""))
            target = next(iter(files.values()), None)
            dev = options.get("DEV", "")
            if target is not None:
                target["formats"].add(fmt)
                if command == "RCVF" and not dev:
                    target["ops"].add("R")
                target.setdefault("io", set()).add(command)
            out["screens"].append({"format": fmt, "file": target["name"] if target else "", "op": command, "line": line_no})
        elif command == "OVRDBF":
            out["overrides"].append({"file": _cl_object(options.get("FILE", "")), "to": _cl_object(options.get("TOFILE", "")), "line": line_no})
        elif command in ("CPYF",):
            for key, op in (("FROMFILE", "R"), ("TOFILE", "C")):
                if options.get(key):
                    name = _cl_object(options[key])
                    files.setdefault(name, {"name": name, "ops": set(), "line": line_no, "via": command, "device": "", "formats": set()})["ops"].add(op)
        elif command in ("CLRPFM", "RGZPFM"):
            name = _cl_object(options.get("FILE", rest.split()[0] if rest.split() else ""))
            files.setdefault(name, {"name": name, "ops": set(), "line": line_no, "via": command, "device": "", "formats": set()})["ops"].add("D")
        elif command == "RUNSQL":
            out["sql"].append(_sql_statement(unquote(options.get("SQL", "")), line_no))
        elif command == "RUNSQLSTM":
            out["copybooks"].append({"name": _cl_object(options.get("SRCMBR", "")), "line": line_no, "via": "RUNSQLSTM"})
        elif command == "SUBR":
            out["subroutines"].append({"name": _cl_object(options.get("SUBR", "")), "line": line_no, "end": line_no, "performs": [], "complexity": 1})
        elif command == "ENDSUBR" and out["subroutines"]:
            out["subroutines"][-1]["end"] = line_no
        elif command == "CALLSUBR":
            out.setdefault("exsr", []).append((_cl_object(options.get("SUBR", "")), line_no))
        elif command == "INCLUDE":
            out["copybooks"].append({"name": _cl_object(options.get("SRCMBR", "")), "line": line_no, "via": "INCLUDE"})
    out["files"] = [dict(f, ops=_ops(*f["ops"]), formats=sorted(f["formats"] - {""}), io=sorted(f.get("io", set()))) for f in files.values() if f["name"]]
    return out


# --- JCL ------------------------------------------------------------------------------------------------------------


def _jcl_operands(text: str) -> tuple[str, str]:
    """Operand field (up to the first blank outside quotes / parentheses) and the trailing comment."""
    depth, quote = 0, False
    for i, ch in enumerate(text):
        if ch == "'":
            quote = not quote
        elif not quote and ch == "(":
            depth += 1
        elif not quote and ch == ")":
            depth -= 1
        elif not quote and depth <= 0 and ch == " ":
            return text[:i], text[i:].strip()
    return text, ""


def _jcl_params(operands: str) -> tuple[list[str], dict[str, str]]:
    positional, keywords = [], {}
    depth, quote, current = 0, False, ""
    parts = []
    for ch in operands:
        if ch == "'":
            quote = not quote
        if not quote and ch == "(":
            depth += 1
        if not quote and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quote:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)
    for part in parts:
        if "=" in part and re.match(r"^[A-Za-z][\w.]*=", part):
            key, _, value = part.partition("=")
            keywords[key.upper()] = value
        else:
            positional.append(part)
    return positional, keywords


# DDs that point at load / source libraries, dumps and listings rather than application data.
NOISE_DDS = {"STEPLIB", "JOBLIB", "SYSLIB", "SYSLMOD", "SYSPRINT", "SYSUDUMP", "SYSABEND", "SYSMDUMP", "SYSOUT", "SYSTSPRT", "SYSDBOUT", "CEEDUMP",
             "SYSEXEC", "SYSPROC", "DFSRESLB", "IMS", "DBRMLIB", "SYSLIN", "SYSTERM", "SYSCPRT", "SYSPUNCH", "SYSADATA", "SYSXMLSD", "DFHRPL",
             "SYSDEBUG", "SYSMDECK", "SYSUT5", "SYSUT6", "SYSUT7", "SYSUT8", "SYSUT9", "SYSUT10", "SYSUT11", "SYSUT12", "SYSUT13", "SYSUT14",
             "SYSUT15", "SYSUT16", "SYSUT17", "SYSIDF", "PROCLIB", "ORDER"}
# Compilers, translators and binders: their datasets are build artefacts, not application data.
TOOL_PROGRAMS = {"IGYCRCTL", "IGYWCL", "IEWL", "IEWBLINK", "HEWL", "HEWLH096", "HEWLKED", "DFHECP1$", "DFHEAP1$", "DFHECP1", "DFHEAP1", "DSNHPC",
                 "DSNHPSM", "DSNHPRE", "ASMA90", "IEV90", "ASMA", "DFHMAPS", "IGYCRCTL", "DFHYITVL", "CCNDRVR", "EDCDRVR", "IBMZPLI", "DFSILNK0"}


def dataset_base(dsn: str) -> tuple[str, str]:
    """PROD.CUST.BKUP(+1) -> ('PROD.CUST.BKUP', '+1'); LIB.SRC(MEMBER) -> ('LIB.SRC', 'MEMBER')."""
    dsn = unquote(dsn).upper()
    m = re.match(r"^(.*?)\(([^)]*)\)$", dsn)
    return (m.group(1), m.group(2)) if m else (dsn, "")


def disp_ops(disp: str) -> list[str]:
    status = unquote(disp).strip("()").split(",")[0].strip().upper() if disp else ""
    return {"SHR": ["R"], "OLD": ["R", "U"], "MOD": ["C"], "NEW": ["C"], "": ["C"]}.get(status, ["R"])


def _substitute(value: str, symbols: dict[str, str]) -> str:
    """JCL symbolic substitution: &HLQ..CUST.MASTER with HLQ=PROD -> PROD.CUST.MASTER (unknown symbols stay)."""
    for _ in range(3):
        replaced = re.sub(r"&([A-Za-z#@$][\w#@$]*)\.?", lambda m: symbols.get(m.group(1).upper(), m.group(0)), value)
        if replaced == value:
            break
        value = replaced
    return value


def parse_jcl(text: str) -> dict:
    statements: list[dict] = []
    raw_lines = text.splitlines()
    i = 0
    while i < len(raw_lines):
        raw = raw_lines[i].rstrip()
        line_no = i + 1
        if raw.startswith("//*") or not raw.startswith("//"):
            i += 1
            continue
        if raw.strip() == "//":
            break
        m = re.match(r"^//(\S*)\s+(\S+)\s*(.*)$", raw[:72])
        if not m:
            i += 1
            continue
        name, op, rest = m.group(1).upper(), m.group(2).upper(), m.group(3)
        operands, _comment = _jcl_operands(rest)
        while operands.endswith(",") and i + 1 < len(raw_lines) and raw_lines[i + 1].startswith("//") and not raw_lines[i + 1].startswith("//*"):
            i += 1
            more, _ = _jcl_operands(raw_lines[i][2:72].strip())
            operands += more
        stmt = {"name": name, "op": op, "operands": operands, "line": line_no, "instream": []}
        statements.append(stmt)
        i += 1
        if op == "DD" and re.match(r"^(\*|DATA)(?:,|\s|$)", operands, re.I):
            delimiter = "/*"
            dm = re.search(r"DLM=([^,\s]+)", operands, re.I)
            if dm:
                delimiter = unquote(dm.group(1))
            while i < len(raw_lines) and not raw_lines[i].startswith(delimiter) and not (delimiter == "/*" and raw_lines[i].startswith("//")):
                stmt["instream"].append(raw_lines[i][:72])
                i += 1
            if i < len(raw_lines) and raw_lines[i].startswith(delimiter):
                i += 1
    members: list[dict] = []
    current: dict | None = None
    step: dict | None = None
    last_dd: dict | None = None
    for stmt in statements:
        op = stmt["op"]
        positional, keywords = _jcl_params(stmt["operands"])
        if op in ("JOB", "PROC"):
            params = {k: unquote(v) for k, v in keywords.items()} if op == "PROC" else {}
            current = {"type": op.lower(), "name": stmt["name"], "line": stmt["line"], "end": stmt["line"], "steps": [], "includes": [], "symbols": dict(params),
                       "class": keywords.get("CLASS", ""), "params": params}
            members.append(current)
            step = None
            continue
        if current is None:
            current = {"type": "proc", "name": "", "line": stmt["line"], "end": stmt["line"], "steps": [], "includes": [], "symbols": {}, "class": "", "params": {}}
            members.append(current)
        current["end"] = stmt["line"]
        if op == "EXEC":
            program = unquote(keywords.get("PGM", "")).upper()
            proc = unquote(keywords.get("PROC", "")).upper() or (positional[0].upper() if positional and not program else "")
            step = {"name": stmt["name"], "program": program, "proc": proc, "parm": unquote(keywords.get("PARM", ""))[:200], "line": stmt["line"], "dds": [],
                    "cond": keywords.get("COND", ""), "via_program": "", "tool": program in TOOL_PROGRAMS}
            current["steps"].append(step)
            last_dd = None
        elif op == "DD" and step is not None:
            raw_dsn = unquote(keywords.get("DSN") or keywords.get("DSNAME") or "")
            dsn = _substitute(raw_dsn, current["symbols"]) if raw_dsn and not raw_dsn.startswith("&&") else raw_dsn
            base, member = dataset_base(dsn) if dsn else ("", "")
            dd = {"name": stmt["name"] or (last_dd["name"] if last_dd else ""), "dsn": base, "raw_dsn": raw_dsn if raw_dsn != dsn else "", "member": member, "disp": keywords.get("DISP", ""),
                  "sysout": keywords.get("SYSOUT", ""), "dummy": bool(positional and positional[0].upper() == "DUMMY"), "instream": bool(stmt["instream"]),
                  "temporary": base.startswith("&&"), "line": stmt["line"], "concatenated": not stmt["name"]}
            dd["ops"] = disp_ops(dd["disp"]) if base and not dd["dummy"] else []
            step["dds"].append(dd)
            last_dd = dd if stmt["name"] else last_dd
            if stmt["instream"] and dd["name"] in ("SYSTSIN", "SYSIN"):
                body = "\n".join(stmt["instream"])
                for rm in re.finditer(r"\bRUN\s+PROGRAM\s*\(\s*([\w#@$]+)\s*\)(?:\s+PLAN\s*\(\s*([\w#@$]+)\s*\))?", body, re.I):
                    step["via_program"] = step["program"]
                    step["program"] = rm.group(1).upper()
                    step["plan"] = (rm.group(2) or "").upper()
                if dd["name"] == "SYSIN":
                    step["sysin"] = body[:2000]
        elif op == "INCLUDE":
            current["includes"].append({"name": unquote(keywords.get("MEMBER", "")).upper(), "line": stmt["line"]})
        elif op == "SET":
            current["symbols"].update({k: unquote(v) for k, v in keywords.items()})
    return {"members": members}


# --- DDS ------------------------------------------------------------------------------------------------------------

DDS_TYPES = {"A": "CHAR", "O": "CHAR", "J": "CHAR", "E": "CHAR", "G": "GRAPHIC", "P": "DECIMAL", "S": "NUMERIC", "B": "BINARY", "F": "FLOAT",
             "L": "DATE", "T": "TIME", "Z": "TIMESTAMP", "H": "HEX", "Y": "NUMERIC", "N": "NUMERIC"}


def _dds_text(keywords: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*\(\s*((?:'(?:[^']|'')*'\s*)+)\)", keywords, re.I)
    return " ".join(unquote(x) for x in re.findall(r"'(?:[^']|'')*'", m.group(1))).strip() if m else ""


def parse_dds(text: str, path: str = "") -> dict:
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    out: dict = {"type": {"pf": "PF", "lf": "LF", "dspf": "DSPF", "prtf": "PRTF"}.get(suffix, ""), "file_keywords": "", "formats": [], "ref": ""}
    current: dict | None = None
    last: dict | None = None
    pending_constant = ""
    for i, raw in enumerate(text.splitlines()):
        if len(raw) < 7 or raw[6:7] == "*" or not raw[5:].strip():
            continue
        if raw[5:6].upper() not in ("A", " "):
            continue
        body = raw[:80].ljust(80)
        kind = body[16].upper()
        name = body[18:28].strip().upper()
        keywords = body[44:80].rstrip()
        row, col = body[38:41].strip(), body[41:44].strip()
        if pending_constant:
            # continuation of a constant split with '-' / '+'
            if last is not None and last.get("constant") is not None:
                last["constant"] += keywords.strip().strip("'").rstrip("-+")
            pending_constant = "" if not keywords.rstrip().endswith(("-", "+")) else "1"
            continue
        if kind == "R":
            current = {"name": name, "line": i + 1, "keywords": keywords, "fields": [], "constants": [], "keys": [], "select": []}
            out["formats"].append(current)
            last = current
            continue
        if current is None and not name and kind == " ":
            out["file_keywords"] += " " + keywords
            continue
        if current is None:
            continue
        if kind == "K":
            current["keys"].append(name)
            continue
        if kind in ("S", "O"):
            current["select"].append({"type": "select" if kind == "S" else "omit", "field": name, "rule": keywords.strip()})
            continue
        if name:
            length = body[29:34].strip()
            data_type = body[34].strip().upper()
            decimals = body[35:37].strip()
            usage = body[37].strip().upper() or "B"
            item = {"name": name, "line": i + 1, "length": int(length) if length.isdigit() else None, "type": data_type,
                    "decimals": int(decimals) if decimals.isdigit() else None, "usage": usage, "ref": body[28].upper() == "R", "keywords": keywords,
                    "row": int(row) if row.isdigit() else None, "col": int(col) if col.isdigit() else None}
            current["fields"].append(item)
            last = item
            continue
        if keywords.strip().startswith("'") and (row or col):
            constant = {"constant": keywords.strip().strip("'"), "row": int(row) if row.isdigit() else None, "col": int(col) if col.isdigit() else None, "line": i + 1}
            if keywords.rstrip().endswith(("-", "+")) and not re.search(r"'\s*$", keywords.rstrip()[:-1]):
                constant["constant"] = keywords.strip().lstrip("'").rstrip("-+")
                pending_constant = "1"
            current["constants"].append(constant)
            last = constant
            continue
        if keywords.strip() and last is not None:
            last["keywords"] = (last.get("keywords", "") + " " + keywords).strip()
    file_kw = out["file_keywords"]
    all_kw = file_kw + " ".join(f["keywords"] for f in out["formats"])
    if not out["type"]:
        if re.search(r"\b(DSPSIZ|CA\d\d|CF\d\d|SFL\b|SFLCTL|OVERLAY)", all_kw, re.I) or any(f.get("row") for fmt in out["formats"] for f in fmt["fields"]):
            out["type"] = "DSPF"
        elif re.search(r"\bPFILE\s*\(|\bJFILE\s*\(", all_kw, re.I):
            out["type"] = "LF"
        elif re.search(r"\b(SKIPB|SPACEA|SKIPA|SPACEB)\b", all_kw, re.I):
            out["type"] = "PRTF"
        else:
            out["type"] = "PF"
    ref = re.search(r"\bREF\s*\(\s*(?:[\w#@$]+/)?([\w#@$]+)", file_kw, re.I)
    out["ref"] = ref.group(1).upper() if ref else ""
    out["unique"] = bool(re.search(r"\bUNIQUE\b", file_kw, re.I))
    out["text"] = _dds_text(file_kw, "TEXT")
    for fmt in out["formats"]:
        kw = fmt["keywords"]
        pf = re.search(r"\b(?:PFILE|JFILE)\s*\(([^)]*)\)", kw, re.I)
        fmt["pfile"] = [p.split("/")[-1].upper() for p in pf.group(1).split()] if pf else []
        fmt["text"] = _dds_text(kw, "TEXT")
        sflctl = re.search(r"\bSFLCTL\s*\(\s*([\w#@$]+)", kw, re.I)
        fmt["subfile_control_of"] = sflctl.group(1).upper() if sflctl else ""
        fmt["subfile"] = bool(re.search(r"\bSFL\b(?!\w)", kw, re.I))
        fmt["function_keys"] = [{"key": f"F{int(k[2:])}" if k[2:].isdigit() else k, "label": unquote(lbl) if lbl else "", "type": k[:2].upper()}
                                for k, lbl in re.findall(r"\b(C[AF]\d\d)(?:\s*\(\s*\d*\s*('(?:[^']|'')*')?\s*\))?", file_kw + " " + kw, re.I)]
        for f in fmt["fields"]:
            f["text"] = _dds_text(f["keywords"], "TEXT") or _dds_text(f["keywords"], "COLHDG")
            ref_fld = re.search(r"\bREFFLD\s*\(\s*([\w#@$]+)(?:\s+(?:[\w#@$]+/)?([\w#@$]+))?", f["keywords"], re.I)
            if ref_fld:
                f["reffld"] = {"field": ref_fld.group(1).upper(), "file": (ref_fld.group(2) or out["ref"]).upper()}
            f["check"] = re.findall(r"\bCHECK\s*\(([^)]*)\)", f["keywords"], re.I)
    return out


def dds_sql_type(field: dict) -> str:
    data_type = field.get("type") or ("P" if field.get("decimals") is not None else "A")
    base = DDS_TYPES.get(data_type, "CHAR")
    length = field.get("length")
    if base in ("DECIMAL", "NUMERIC") and length:
        return f"{base}({length},{field.get('decimals') or 0})"
    if base in ("CHAR", "GRAPHIC") and length:
        return f"{base}({length})"
    return base


# --- BMS ------------------------------------------------------------------------------------------------------------


def _asm_statements(text: str) -> list[tuple[int, str, str, str]]:
    """(line, label, op, operands) from assembler source with column-72 continuation."""
    statements: list[tuple[int, str, str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.startswith("*") or raw.startswith(".*"):
            i += 1
            continue
        start = i + 1
        continued = len(raw) > 71 and raw[71] not in (" ", "")
        text_part = raw[:71].rstrip() if continued else raw[:71]
        while continued and i + 1 < len(lines):
            i += 1
            nxt = lines[i]
            text_part += nxt[15:71].strip() if len(nxt) > 15 else nxt.strip()
            continued = len(nxt) > 71 and nxt[71] not in (" ", "")
        i += 1
        m = re.match(r"^(\S*)\s+(\S+)\s*(.*)$", text_part)
        if not m:
            continue
        operands, _ = _jcl_operands(m.group(3).strip())
        statements.append((start, m.group(1).upper(), m.group(2).upper(), operands))
    return statements


def parse_bms(text: str) -> dict:
    out: dict = {"mapset": "", "line": 0, "maps": [], "lang": "", "mode": ""}
    current: dict | None = None
    for line_no, label, op, operands in _asm_statements(text):
        _positional, keywords = _jcl_params(operands)
        if op == "DFHMSD":
            if keywords.get("TYPE", "").upper() == "FINAL":
                continue
            out.update(mapset=label, line=line_no, lang=keywords.get("LANG", ""), mode=keywords.get("MODE", ""))
        elif op == "DFHMDI":
            size = re.findall(r"\d+", keywords.get("SIZE", ""))
            current = {"name": label, "line": line_no, "size": [int(x) for x in size[:2]] or [24, 80], "fields": []}
            out["maps"].append(current)
        elif op == "DFHMDF" and current is not None:
            pos = re.findall(r"\d+", keywords.get("POS", ""))
            attrb = [a.strip().upper() for a in keywords.get("ATTRB", "").strip("()").split(",") if a.strip()]
            initial = keywords.get("INITIAL", "") or keywords.get("XINIT", "")
            field_ = {"name": label, "line": line_no, "row": int(pos[0]) if len(pos) > 1 else None, "col": int(pos[1]) if len(pos) > 1 else None,
                      "length": int(keywords["LENGTH"]) if keywords.get("LENGTH", "").isdigit() else None, "attrb": attrb, "initial": unquote(initial),
                      "picin": unquote(keywords.get("PICIN", "")), "picout": unquote(keywords.get("PICOUT", ""))}
            current["fields"].append(field_)
    return out


# --- CICS CSD -------------------------------------------------------------------------------------------------------

_CSD_DEFINE = re.compile(r"\bDEFINE\s+(TRANSACTION|PROGRAM|FILE|MAPSET|TDQUEUE|TSMODEL|URIMAP|WEBSERVICE|PIPELINE|LIBRARY)\s*\(\s*([^)\s]+)\s*\)", re.I)


def parse_csd(text: str) -> list[dict]:
    out = []
    matches = list(_CSD_DEFINE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end]
        body = re.sub(r"^\s*\*.*$", "", body, flags=re.M)
        attrs = {k: unquote(v) for k, v in _paren_options(body)}
        out.append({"type": m.group(1).upper(), "name": m.group(2).upper(), "attrs": attrs, "line": text.count("\n", 0, m.start()) + 1})
    return out


def has_csd(text: str) -> bool:
    return bool(re.search(r"\bDEFINE\s+(?:TRANSACTION|PROGRAM)\s*\(", text[:200_000], re.I))


# --- detection --------------------------------------------------------------------------------------------------------


def sniff(path: str, text: str) -> str:
    """Language for an extension-less (or .txt / .mbr) source member exported from a mainframe or IBM i library."""
    head = text[:6000]
    upper = head.upper()
    parent = PurePosixPath(path).parent.name.upper()
    folder_hint = {"QRPGLESRC": "RPG", "QRPGSRC": "RPG", "QSQLRPGLESRC": "RPG", "QCBLLESRC": "COBOL", "QCBLSRC": "COBOL", "QCLSRC": "CL", "QCLLESRC": "CL",
                   "QDDSSRC": "DDS", "QCPYSRC": "COBOL Copybook", "COPYLIB": "COBOL Copybook", "COPYBOOK": "COBOL Copybook", "COPYBOOKS": "COBOL Copybook",
                   "CPY": "COBOL Copybook", "JCL": "JCL", "JCLLIB": "JCL", "PROCLIB": "JCL", "PROCS": "JCL", "BMS": "BMS", "MAPS": "BMS", "QDSPSRC": "DDS"}
    if re.search(r"\bPROGRAM-ID\b", upper) or re.search(r"\b(IDENTIFICATION|ID)\s+DIVISION\b", upper):
        return "COBOL"
    if re.search(r"^//[\w#@$]*\s+(JOB|EXEC|PROC|DD)\b", head, re.M):
        return "JCL"
    if re.search(r"\bDFHMSD\b", upper):
        return "BMS"
    if upper.lstrip().startswith("**FREE") or re.search(r"\b(CTL-OPT|DCL-PROC|DCL-F|DCL-S|DCL-DS)\b", upper):
        return "RPG"
    if re.search(r"^\s*PGM\b", upper, re.M) and re.search(r"^\s*ENDPGM\b", upper, re.M):
        return "CL"
    if folder_hint.get(parent):
        return folder_hint[parent]
    spec_lines = [line for line in head.splitlines() if len(line) > 6 and line[6] != "*" and line.strip()]
    if spec_lines:
        rpg = sum(1 for line in spec_lines if line[5:6].upper() in "HFDICOP" and line[5:6] != " ")
        dds = sum(1 for line in spec_lines if line[5:6].upper() == "A")
        if dds >= 0.7 * len(spec_lines) and len(spec_lines) >= 2:
            return "DDS"
        if rpg >= 0.7 * len(spec_lines) and len(spec_lines) >= 2:
            return "RPG"
    if re.match(r"^\s*\d{2}\s+[\w-]+", head.lstrip("\n")) and re.search(r"\bPIC(TURE)?\b", upper):
        return "COBOL Copybook"
    return ""


@dataclass
class Parsed:
    """Per-file legacy parse cached for the catalogue."""

    path: str
    language: str
    kind: str
    data: dict = field(default_factory=dict)


def parse_any(path: str, language: str, text: str) -> Parsed | None:
    if language in ("COBOL", "COBOL Copybook"):
        return Parsed(path, language, "cobol", parse_cobol(text))
    if language == "RPG":
        return Parsed(path, language, "rpg", parse_rpg(text, path))
    if language == "CL":
        return Parsed(path, language, "cl", parse_cl(text))
    if language == "JCL":
        return Parsed(path, language, "jcl", parse_jcl(text))
    if language == "DDS":
        return Parsed(path, language, "dds", parse_dds(text, path))
    if language == "BMS":
        return Parsed(path, language, "bms", parse_bms(text))
    return None
