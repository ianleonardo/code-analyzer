"""Legacy-platform catalogue: mainframe (COBOL, CICS, DB2, IMS, JCL, BMS) and IBM i (RPG, CL, DDS) applications.

Items
  service  program      COBOL / RPG / CL program: entry parameters, paragraphs or procedures, calls, files, SQL, CICS
  service  job          JCL job or cataloged procedure: steps, programs run, datasets read and written
  api      transaction  CICS transaction (CSD definition, or inferred from RETURN / START TRANSID)
  ui       screen       CICS BMS map or 5250 display-file record format, with a 24x80 field layout
  data     table / view DB2 table (DCLGEN, embedded SQL), DDS physical file (table) and logical file (view)
  data     file         dataset (JCL DSN, VSAM / CICS file, IMS segment) or COBOL / RPG file without a database
  shared   copybook     COBOL copybook or RPG /COPY member: record layouts, DCLGEN, prototypes

Links
  program  -calls-> program            CALL, CALLP (EXTPGM), EXEC CICS LINK / XCTL, CL CALL / SBMJOB
  program  -uses-> copybook            COPY, EXEC SQL INCLUDE, /COPY, /INCLUDE
  program  -accesses-> table / file    embedded SQL, F-specs, SELECT ... ASSIGN bound to a JCL DD, CICS file control
  program  -renders-> screen           SEND MAP, EXFMT / WRITE of a display format;  screen -submits-> program on RECEIVE / EXFMT
  job      -runs-> program / job       EXEC PGM= (also IKJEFT01 RUN PROGRAM), EXEC PROC
  transaction -implemented by-> program
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.services.analyzer import legacy
from app.services.analyzer.catalog import data as datamod
from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, SourceFile, snippet

LEGACY_LANGUAGES = ("COBOL", "COBOL Copybook", "RPG", "CL", "JCL", "DDS", "BMS")


def _data_dd(step: dict, dd: dict) -> bool:
    """Application data: a named, permanent dataset that is not a library, listing or compiler work file."""
    return bool(dd["dsn"]) and not dd["dsn"].startswith(".") and not dd["temporary"] and not dd["dummy"] and not step.get("tool") and dd["name"].split(".")[-1] not in legacy.NOISE_DDS and "&" not in dd["dsn"]


def _stem(path: str) -> str:
    return PurePosixPath(path).name.split(".")[0].upper()


class _Builder:
    def __init__(self, ctx: Context, cat: Catalog) -> None:
        self.ctx = ctx
        self.cat = cat
        self.parsed: list[tuple[SourceFile, legacy.Parsed]] = []
        self.programs: dict[str, str] = {}  # program name -> item key
        self.program_files: dict[str, list[dict]] = {}  # item key -> COBOL / RPG file declarations
        self.copybooks: dict[str, str] = {}  # member -> item key
        self.dds: dict[str, tuple[SourceFile, dict]] = {}  # file name -> (source, parsed DDS)
        self.formats: dict[str, str] = {}  # record format -> DDS file
        self.screens: dict[str, str] = {}  # "FILE.FORMAT" / "MAPSET.MAP" -> screen key
        self.maps_by_name: dict[str, list[str]] = {}
        self.jobs: dict[str, str] = {}
        self.job_members: dict[str, tuple[SourceFile, dict]] = {}
        self.csd: list[tuple[SourceFile, dict]] = []
        self.csd_files: dict[str, str] = {}  # CICS file -> DSN
        self.exported_procs: dict[str, str] = {}
        self.declares: list[tuple[str, str]] = []
        self.program_by_path: dict[str, str] = {}

    # -- helpers --------------------------------------------------------------------------------------------------

    def _file_item(self, key: str, name: str, file: SourceFile, line: int, framework: str, details: dict) -> Item:
        existing = self.cat.items.get(key)
        if existing is not None:
            for k, v in details.items():
                if isinstance(v, list):
                    merged = existing.details.setdefault(k, [])
                    merged += [x for x in v if x not in merged]
                elif v and not existing.details.get(k):
                    existing.details[k] = v
            return existing
        details.setdefault("accessed_by", [])
        return self.cat.add(Item("data", "file", key, name, file.path, line, line, file.language, framework, f"{details.get('organization', 'file')} {name}", details))

    def _table(self, name: str, ops: list[str], source_key: str, file: SourceFile, line: int, origin: str, detail: str = "") -> None:
        key = sqlmod.table_key(name)
        if not key:
            return
        if key not in self.cat.tables_registry and f"view:{key}" not in self.cat.items:
            entry = datamod._register(self.cat, name, file.path, line, origin)
            entry.setdefault("dialects", [])
            if "DB2" not in entry["dialects"]:
                entry["dialects"].append("DB2")
        self.cat.pending_tables.append((source_key, key, "/".join(ops) or "R", detail))

    def _program_key(self, name: str) -> str | None:
        return self.programs.get((name or "").upper())

    # -- run ------------------------------------------------------------------------------------------------------

    def run(self) -> None:
        for file in self.ctx.files:
            if not file.text or len(file.text) > 3_000_000:
                continue
            if file.language in LEGACY_LANGUAGES:
                try:
                    parsed = legacy.parse_any(file.path, file.language, file.text)
                except (ValueError, IndexError, RecursionError):
                    parsed = None
                if parsed is not None:
                    self.parsed.append((file, parsed))
            if file.language in ("CICS CSD", "JCL", "Text", "Other", "Config") and legacy.has_csd(file.text):
                self.csd += [(file, d) for d in legacy.parse_csd(file.text)]
        if not self.parsed and not self.csd:
            return
        for file, definition in self.csd:
            if definition["type"] == "FILE" and definition["attrs"].get("DSNAME"):
                self.csd_files[definition["name"]] = legacy.dataset_base(definition["attrs"]["DSNAME"])[0]
        referenced = self._referenced_members()
        for file, parsed in self.parsed:
            if parsed.kind == "dds":
                self._dds(file, parsed.data)
            elif parsed.kind == "bms":
                self._bms(file, parsed.data)
        for file, parsed in self.parsed:
            if parsed.kind == "cobol":
                self._cobol_items(file, parsed.data, referenced)
            elif parsed.kind in ("rpg", "cl"):
                self._rpg_cl_item(file, parsed, referenced)
            elif parsed.kind == "jcl":
                for member in parsed.data["members"]:
                    if member["name"]:
                        self.job_members[member["name"]] = (file, member)
        for name, (file, member) in self.job_members.items():
            self._job_item(file, member)
        self._transactions()
        for file, parsed in self.parsed:
            if parsed.kind == "cobol":
                for unit in parsed.data["units"]:
                    key = self._program_key(unit["name"]) if unit["name"] else None
                    if key:
                        self._cobol_links(file, unit, key)
            elif parsed.kind in ("rpg", "cl"):
                key = self.program_by_path.get(file.path)
                if key:
                    self._rpg_cl_links(file, parsed, key)
        for name, (file, member) in self.job_members.items():
            self._job_links(file, member)
        self._unbound_files()
        self._finish()
        self.cat.extra["legacy_declares"] = self.declares
        self.cat.extra["legacy"] = {"programs": len(self.programs), "copybooks": len(self.copybooks), "jobs": len(self.jobs), "screens": len(self.screens)}

    def _referenced_members(self) -> set[str]:
        names: set[str] = set()
        for _file, parsed in self.parsed:
            if parsed.kind == "cobol":
                for unit in parsed.data["units"]:
                    names.update(c["name"] for c in unit["copybooks"])
            elif parsed.kind in ("rpg", "cl"):
                names.update(c["name"] for c in parsed.data["copybooks"])
        return names

    # -- DDS / BMS ------------------------------------------------------------------------------------------------

    def _dds(self, file: SourceFile, data: dict) -> None:
        name = _stem(file.path)
        self.dds[name] = (file, data)
        for fmt in data["formats"]:
            self.formats.setdefault(fmt["name"], name)
        if data["type"] == "PF":
            for fmt in data["formats"][:1]:
                entry = datamod._register(self.cat, name, file.path, fmt["line"], "DDS physical file")
                columns = [{"name": f["name"], "type": legacy.dds_sql_type(f), "nullable": "ALWNULL" in f["keywords"].upper(), "pk": False, "unique": False,
                            "auto": False, **({"comment": f["text"]} if f.get("text") else {})} for f in fmt["fields"] if not f.get("ref") or f.get("length")]
                columns += [{"name": f["name"], "type": f"REF {f.get('reffld', {}).get('file') or data['ref']}".strip(), "nullable": True, "pk": False, "unique": False, "auto": False}
                            for f in fmt["fields"] if f.get("ref") and not f.get("length")]
                datamod._merge_columns(entry, columns)
                if fmt["keys"]:
                    if data["unique"]:
                        entry["primary_key"] = list(fmt["keys"])
                    entry.setdefault("indexes", []).append({"name": f"{fmt['name']} key", "columns": list(fmt["keys"]), "unique": data["unique"]})
                entry.setdefault("dialects", [])
                if "DB2 for i (DDS)" not in entry["dialects"]:
                    entry["dialects"].append("DB2 for i (DDS)")
                entry["comment"] = entry.get("comment") or data["text"] or fmt["text"]
                entry["end_line"] = max([fmt["line"]] + [f["line"] for f in fmt["fields"]])
                entry["ddl_code"] = snippet(file.text, 1, entry["end_line"], 200)
                entry["record_format"] = fmt["name"]
        elif data["type"] == "LF":
            based = sorted({p for fmt in data["formats"] for p in fmt["pfile"]})
            fmt = data["formats"][0] if data["formats"] else {"line": 1, "keys": [], "select": [], "name": ""}
            item = self.cat.add(Item("data", "view", f"view:{name.lower()}", name, file.path, fmt["line"], len(file.text.splitlines()), file.language, "DB2 for i (DDS)",
                                     f"LOGICAL FILE {name} OVER {', '.join(based)}",
                                     {"sql": "", "dialect": "DDS logical file", "tables": {sqlmod.table_key(b): ["R"] for b in based}, "keys": fmt["keys"],
                                      "select": fmt["select"], "record_format": fmt["name"], "unique": data["unique"]},
                                     code=snippet(file.text, 1, 80, 80)))
            for b in based:
                self.cat.pending_tables.append((item.key, sqlmod.table_key(b), "R", "logical file"))
        elif data["type"] == "DSPF":
            self._dspf_screens(file, name, data)
        elif data["type"] == "PRTF":
            self._file_item(f"file:{name}", name, file, 1, "IBM i printer file", {"organization": "printer file", "kind": "printer",
                                                                                  "formats": [f["name"] for f in data["formats"]], "defined_in": file.path})

    def _dspf_screens(self, file: SourceFile, name: str, data: dict) -> None:
        by_name = {fmt["name"]: fmt for fmt in data["formats"]}
        subfiles = {fmt["subfile_control_of"] for fmt in data["formats"] if fmt["subfile_control_of"]}
        for fmt in data["formats"]:
            if fmt["name"] in subfiles:
                continue  # subfile rows render inside their control format
            fields, constants = list(fmt["fields"]), list(fmt["constants"])
            grid = by_name.get(fmt["subfile_control_of"]) if fmt["subfile_control_of"] else None
            if grid is not None:
                fields += [dict(f, subfile=True) for f in grid["fields"]]
            if not fields and not constants:
                continue
            title = next((c["constant"] for c in sorted(constants, key=lambda c: (c.get("row") or 99, c.get("col") or 0)) if (c.get("row") or 99) <= 2), "")
            details = {
                "platform": "IBM i 5250", "type": "subfile" if grid is not None else "display record format", "title": title or fmt["text"] or fmt["name"],
                "file": name, "format": fmt["name"], "size": [27, 132] if "27 132" in data["file_keywords"] and "24 80" not in data["file_keywords"] else [24, 80],
                "fields": [self._dds_field(f, constants) for f in fields if f.get("usage") != "H"],
                "constants": [{"text": c["constant"], "row": c.get("row"), "col": c.get("col"), "line": c["line"]} for c in constants],
                "actions": [{"label": f"{k['key']}={k['label']}" if k["label"] else k["key"], "widget": "function key", "kind": k["type"], "handler": ""} for k in fmt["function_keys"]],
                "subfile": grid["name"] if grid is not None else "", "routes": [],
            }
            end = max([fmt["line"]] + [f["line"] for f in fields] + [c["line"] for c in constants])
            item = self.cat.add(Item("ui", "screen", f"screen:5250:{name}.{fmt['name']}", f"{name}.{fmt['name']}", file.path, fmt["line"], end, file.language, "IBM i 5250",
                                     f"R {fmt['name']} ({name})", details, code=snippet(file.text, fmt["line"], end, 120)))
            self.screens[f"{name}.{fmt['name']}"] = item.key
            if grid is not None:
                self.screens[f"{name}.{grid['name']}"] = item.key

    @staticmethod
    def _label(row: int | None, col: int | None, constants: list[dict]) -> str:
        """Nearest constant to the left on the same row: the field's caption on a green screen."""
        if row is None or col is None:
            return ""
        same_row = [c for c in constants if c.get("row") == row and (c.get("col") or 0) < col]
        if not same_row:
            return ""
        best = max(same_row, key=lambda c: c.get("col") or 0)
        return (best.get("constant") or best.get("text") or "").strip(" .:")

    def _dds_field(self, field: dict, constants: list[dict]) -> dict:
        usage = field.get("usage") or "B"
        numeric = (field.get("type") or "") in ("Y", "S", "P", "B") or field.get("decimals") is not None
        validations = []
        for check in field.get("check", []):
            validations += [f"CHECK({c})" for c in check.split()]
        for keyword in ("VALUES", "RANGE", "COMP", "CMP"):
            m = re.search(rf"\b{keyword}\s*\(([^)]*)\)", field.get("keywords", ""), re.I)
            if m:
                validations.append(f"{keyword}({m.group(1).strip()})")
        return {"name": field["name"], "widget": {"I": "input", "O": "output", "B": "input/output", "P": "program-to-system"}.get(usage, "input/output"),
                "type": f"{'numeric' if numeric else 'char'}({field.get('length') or '?'}{',' + str(field['decimals']) if field.get('decimals') is not None else ''})",
                "label": self._label(field.get("row"), field.get("col"), constants) or field.get("text", ""), "required": any("ME" in c.upper() or "MF" in c.upper() for c in field.get("check", [])),
                "row": field.get("row"), "col": field.get("col"), "length": field.get("length"), "line": field["line"], "validations": validations,
                "subfile": bool(field.get("subfile"))}

    def _bms(self, file: SourceFile, data: dict) -> None:
        mapset = data["mapset"] or _stem(file.path)
        for bms_map in data["maps"]:
            constants = [{"constant": f["initial"], "row": f["row"], "col": f["col"], "line": f["line"]} for f in bms_map["fields"] if not f["name"] and f["initial"]]
            fields = []
            for f in bms_map["fields"]:
                if not f["name"]:
                    continue
                attrb = set(f["attrb"])
                numeric = "NUM" in attrb or bool(re.match(r"^S?9", f["picin"] or f["picout"] or ""))
                fields.append({"name": f["name"], "widget": "input" if "UNPROT" in attrb else "output", "type": f"{'numeric' if numeric else 'char'}({f['length'] or '?'})",
                               "label": self._label(f["row"], f["col"], constants), "required": False, "row": f["row"], "col": f["col"], "length": f["length"],
                               "line": f["line"], "validations": sorted(attrb & {"NUM", "IC", "FSET", "BRT", "DRK"}) + ([f"PICIN {f['picin']}"] if f["picin"] else []),
                               "initial": f["initial"]})
            title = next((c["constant"] for c in sorted(constants, key=lambda c: (c.get("row") or 99, c.get("col") or 0)) if (c.get("row") or 99) <= 2), bms_map["name"])
            end = max([bms_map["line"]] + [f["line"] for f in bms_map["fields"]])
            details = {"platform": "CICS BMS", "type": "BMS map", "title": title, "mapset": mapset, "map": bms_map["name"], "size": bms_map["size"],
                       "fields": fields, "constants": [{"text": c["constant"], "row": c["row"], "col": c["col"], "line": c["line"]} for c in constants],
                       "actions": [], "routes": [], "lang": data["lang"]}
            item = self.cat.add(Item("ui", "screen", f"screen:bms:{mapset}.{bms_map['name']}", f"{mapset}.{bms_map['name']}", file.path, bms_map["line"], end, file.language,
                                     "CICS BMS", f"{bms_map['name']} DFHMDI SIZE=({bms_map['size'][0]},{bms_map['size'][1]})", details,
                                     code=snippet(file.text, bms_map["line"], end, 120)))
            self.screens[f"{mapset}.{bms_map['name']}"] = item.key
            self.maps_by_name.setdefault(bms_map["name"], []).append(item.key)

    # -- programs / copybooks -------------------------------------------------------------------------------------

    def _copybook_item(self, file: SourceFile, member: str, details: dict, framework: str) -> None:
        if member in self.copybooks:
            return
        lines = len(file.text.splitlines())
        signature = f"COPY {member}" if framework == "COBOL" else f"/COPY {member}"
        item = self.cat.add(Item("shared", "copybook", f"copybook:{member}", member, file.path, 1, lines, file.language, framework, signature, details,
                                 code=snippet(file.text, 1, lines, 200)))
        self.copybooks[member] = item.key
        for dclgen in details.get("dclgen", []):
            self.declares.append((item.key, sqlmod.table_key(dclgen["table"])))

    def _cobol_items(self, file: SourceFile, data: dict, referenced: set[str]) -> None:
        member = _stem(file.path)
        for dclgen in data["dclgen"]:
            entry = datamod._register(self.cat, dclgen["table"], file.path, dclgen["line"], "DCLGEN")
            datamod._merge_columns(entry, dclgen["columns"])
            entry.setdefault("dialects", [])
            if "DB2" not in entry["dialects"]:
                entry["dialects"].append("DB2")
            schema = dclgen["table"].split(".")[0] if "." in dclgen["table"] else ""
            if schema:
                entry.setdefault("schema", schema)
        if data["copybook"] or file.language == "COBOL Copybook" or (member in referenced and not any(u["name"] for u in data["units"])):
            unit = data["units"][0]
            self._copybook_item(file, member, {"language": file.language, "records": unit["data_items"][:400], "dclgen": data["dclgen"],
                                               "paragraphs": [p["name"] for p in unit["paragraphs"]], "format": data["format"],
                                               "copybooks": [c["name"] for c in unit["copybooks"]]}, "COBOL")
            return
        for unit in data["units"]:
            if not unit["name"]:
                continue
            framework = "CICS" if unit["has_cics"] else ("IMS" if unit["has_dli"] else "Batch")
            if unit["has_sql"]:
                framework += " · DB2"
            if unit["has_cics"]:
                role = "online (CICS)"
            elif unit["using"]:
                role = "subprogram"
            else:
                role = "batch main"
            first_para = unit["paragraphs"][0]["line"] if unit["paragraphs"] else unit["line"]
            details = {
                "language": "COBOL", "dialect": f"COBOL ({data['format']} format)", "program_id": unit["name"], "role": role, "entry": unit["using"],
                "linkage": unit["linkage"], "loc": unit["loc"], "complexity": unit["complexity"],
                "units": [{"name": p["name"], "kind": p["kind"], "line": p["line"], "end": p["end"], "complexity": p["complexity"], "performs": p["performs"],
                           "calls": p["calls"], "go_to": p.get("go_to", []), "statements": p["statements"]} for p in unit["paragraphs"]],
                "calls": [], "copybooks": [dict(c, resolved=False) for c in unit["copybooks"]],
                "files": [{k: v for k, v in f.items() if k != "status"} for f in unit["files"]],
                "sql": [{k: v for k, v in s.items() if k != "columns"} for s in unit["sql"] if s["operation"] != "INCLUDE"],
                "cics": [{"command": c["command"], "line": c["line"], "target": next((legacy.unquote(c["options"][k]) for k in ("PROGRAM", "MAP", "FILE", "DATASET", "TRANSID", "QUEUE")
                                                                                    if c["options"].get(k)), "")} for c in unit["cics"]],
                "dli": [{"command": c["command"], "line": c["line"], "segment": legacy.unquote(c["options"].get("SEGMENT", ""))} for c in unit["dli"]],
                "screens": [], "transids": unit["transids"], "queues": unit["queues"], "ends": unit["ends"], "data_items": len(unit["data_items"]),
                "working_storage": [i for i in unit["data_items"] if i["section"] in ("WORKING-STORAGE", "LOCAL-STORAGE") and i["level"] in (1, 77)][:60],
            }
            item = self.cat.add(Item("service", "program", f"program:{unit['name']}", unit["name"], file.path, unit["line"], unit["end"], "COBOL", framework,
                                     f"PROGRAM-ID. {unit['name']}" + (f" USING {' '.join(unit['using'])}" if unit["using"] else ""), details,
                                     code=snippet(file.text, first_para, unit["end"], 400)))
            self.programs.setdefault(unit["name"], item.key)
            self.program_files[item.key] = unit["files"]

    def _rpg_cl_item(self, file: SourceFile, parsed: legacy.Parsed, referenced: set[str]) -> None:
        data = parsed.data
        member = _stem(file.path)
        is_rpg = parsed.kind == "rpg"
        has_code = bool(data.get("files") or data.get("calls") or data.get("subroutines") or data.get("sql") or data.get("fileops") or data.get("entry"))
        suffix = PurePosixPath(file.path).suffix.lower()
        if is_rpg and (suffix in (".rpgleinc", ".rpginc") or member in referenced and not has_code and not data.get("procedures")):
            self._copybook_item(file, member, {"language": "RPG", "prototypes": list(data["prototypes"].values()), "procedures": [p["name"] for p in data["procedures"]],
                                               "copybooks": [c["name"] for c in data["copybooks"]]}, "RPG")
            return
        nomain = bool(re.search(r"\bNOMAIN\b", file.text[:4000], re.I))
        units = [{"name": p["name"], "kind": "procedure", "line": p["line"], "end": p["end"], "complexity": p["complexity"], "performs": p.get("performs", []),
                  "calls": [], "exported": p.get("exported", False), "params": p.get("params", [])} for p in data.get("procedures", [])]
        units += [{"name": s["name"], "kind": "subroutine", "line": s["line"], "end": s["end"], "complexity": s["complexity"], "performs": s.get("performs", []), "calls": []}
                  for s in data.get("subroutines", [])]
        units.sort(key=lambda u: u["line"])
        for call in data["calls"]:
            owner = next((u for u in units if u["line"] <= call["line"] <= u["end"]), None)
            if owner is not None and call["target"] and call["target"] not in owner["calls"]:
                owner["calls"].append(call["target"])
        if is_rpg:
            workstn = any(f["device"] == "WORKSTN" for f in data["files"])
            role = "service program (NOMAIN)" if nomain else ("interactive (5250)" if workstn else ("called program" if data["entry"] else "batch"))
            framework = data["dialect"] + (" · SQL" if any(s["operation"] for s in data["sql"]) else "")
        else:
            role = "CL control program"
            framework = "CL"
        lines = len(file.text.splitlines())
        details = {
            "language": "RPG" if is_rpg else "CL", "dialect": data["dialect"], "program_id": member, "role": role, "entry": data["entry"], "loc": data["loc"],
            "complexity": data["complexity"], "units": units, "calls": [], "copybooks": [dict(c, resolved=False) for c in data["copybooks"]],
            "files": [dict(f, ops=sorted(f["ops"], key="CRUD".index) if isinstance(f["ops"], set) else f["ops"]) for f in data["files"]],
            "sql": [s for s in data["sql"] if s["operation"] != "INCLUDE"], "screens": [], "prototypes": list(data.get("prototypes", {}).values()),
            "overrides": data.get("overrides", []), "exports": [u["name"] for u in units if u.get("exported")],
        }
        signature = f"{'RPG' if is_rpg else 'CL'} {member}" + (f"({', '.join(data['entry'])})" if data["entry"] else "")
        item = self.cat.add(Item("service", "program", f"program:{member}", member, file.path, 1, lines, file.language, framework, signature, details,
                                 code=snippet(file.text, 1, lines, 400)))
        self.programs.setdefault(member, item.key)
        self.program_by_path[file.path] = item.key
        for unit in units:
            if unit.get("exported"):
                self.exported_procs.setdefault(unit["name"], item.key)

    # -- links ----------------------------------------------------------------------------------------------------

    def _call_links(self, key: str, calls: list[dict], file: SourceFile) -> None:
        item = self.cat.items[key]
        out = item.details.setdefault("calls", [])
        for call in calls:
            target = call.get("target", "")
            target_key = self._program_key(target) if target else None
            entry = {k: call.get(k) for k in ("target", "via", "line", "dynamic", "variable", "paragraph") if call.get(k) not in (None, "")}
            entry["resolved"] = bool(target_key)
            entry["system"] = bool(call.get("system")) or bool(target) and legacy.is_system_program(target)
            out.append(entry)
            if target_key and target_key != key:
                self.cat.link(key, target_key, "calls", f"{call.get('via', 'CALL')} {target}".strip())
        for bound in item.details.get("bound_calls", []):
            target_key = self.exported_procs.get(bound["target"])
            if target_key and target_key != key:
                self.cat.link(key, target_key, "calls", f"bound procedure {bound['target']}")

    def _copy_links(self, key: str) -> None:
        for copy in self.cat.items[key].details.get("copybooks", []):
            target = self.copybooks.get(copy["name"])
            copy["resolved"] = bool(target)
            copy["system"] = not target and (copy["name"].startswith(("DFH", "SQLCA", "SQLDA", "QSYSINC")) or copy["name"] in ("QRPGLESRC",))
            if target:
                self.cat.link(key, target, "uses", copy.get("via", "COPY"))

    def _sql_links(self, key: str, sql: list[dict], file: SourceFile) -> None:
        merged: dict[str, tuple[set[str], int, set[str]]] = {}
        for statement in sql:
            for table, ops in (statement.get("tables") or {}).items():
                entry = merged.setdefault(table, (set(), statement["line"], set()))
                entry[0].update(ops)
                entry[2].add(statement.get("operation", ""))
            if statement.get("procedure"):
                self.cat.pending_routines.append((key, statement["procedure"]))
        for table, (ops, line, operations) in merged.items():
            self._table(table, sorted(ops, key="CRUD".index), key, file, line, "embedded SQL", ", ".join(sorted(operations - {""})))

    def _cobol_links(self, file: SourceFile, unit: dict, key: str) -> None:
        item = self.cat.items[key]
        self._call_links(key, unit["calls"], file)
        self._copy_links(key)
        self._sql_links(key, unit["sql"], file)
        screens = item.details["screens"]
        for screen in unit["screens"]:
            target = self.screens.get(f"{screen['mapset']}.{screen['map']}") or next(iter(self.maps_by_name.get(screen["map"], [])), None)
            screens.append({"name": f"{screen['mapset']}.{screen['map']}", "op": screen["op"].upper(), "line": screen["line"], "resolved": bool(target)})
            if target:
                if screen["op"] == "send":
                    self.cat.link(key, target, "renders", "EXEC CICS SEND MAP")
                else:
                    self.cat.link(target, key, "submits", "EXEC CICS RECEIVE MAP")
        for queue in unit["queues"]:
            self.cat.pending_topics.append((key, {"kind": f"CICS {queue['kind']} queue", "name": queue["name"], "line": queue["line"]}, "publishes" if queue["op"] == "write" else "consumes"))
        for f in unit["files"]:
            if f["device"] == "database":
                self._table(f["external"], f["ops"], key, file, f["line"], "COBOL DATABASE file", f"SELECT {f['name']}")
            elif f["device"] == "workstation":
                dds = self.dds.get(f["external"])
                if dds:
                    for fmt in dds[1]["formats"]:
                        target = self.screens.get(f"{f['external']}.{fmt['name']}")
                        if target:
                            self.cat.link(key, target, "renders", f"WORKSTATION-{f['external']}")
            elif f["device"] == "cics":
                dsn = self.csd_files.get(f["name"])
                target = self._file_item(f"file:{dsn or f['name']}", dsn or f["name"], file, f["line"], "CICS file control",
                                         {"organization": "VSAM (CICS file)", "kind": "dataset" if dsn else "cics file", "dsn": dsn or "", "cics_files": [f["name"]]})
                self._access(key, target, f["ops"], f"EXEC CICS file {f['name']}")
                f["bound"] = target.key
            elif f["device"] == "ims":
                target = self._file_item(f"file:IMS:{f['name']}", f["name"], file, f["line"], "IMS DB", {"organization": "IMS segment", "kind": "ims segment"})
                self._access(key, target, f["ops"], "EXEC DLI")
            # sequential / VSAM files are bound to datasets through JCL later (see _job_links / _unbound_files)

    def _rpg_cl_links(self, file: SourceFile, parsed: legacy.Parsed, key: str) -> None:
        data = parsed.data
        item = self.cat.items[key]
        item.details["bound_calls"] = data.get("bound_calls", [])
        self._call_links(key, data["calls"], file)
        self._copy_links(key)
        self._sql_links(key, data["sql"], file)
        renames: dict[str, str] = {}
        files = {f["name"]: f for f in data["files"]}
        for f in data["files"]:
            for old, new in f.get("rename", {}).items():
                renames[new] = old
        fmt_ops: dict[str, set[str]] = {}
        for op in data.get("fileops", []):
            for token in op["tokens"]:
                fmt = renames.get(token, token)
                if token in files:
                    files[token].setdefault("used_ops", set()).add(op["op"])
                elif fmt in self.formats:
                    fmt_ops.setdefault(fmt, set()).add(op["op"])
        for f in data["files"]:
            external = f.get("external") or f["name"]
            for fmt, ops_ in fmt_ops.items():
                if self.formats.get(fmt) == external:
                    f.setdefault("used_ops", set()).update(ops_)
        screens = item.details["screens"]
        for f in data["files"]:
            external = f.get("external") or f["name"]
            device = f.get("device") or ""
            ops = sorted(f["ops"], key="CRUD".index) if isinstance(f["ops"], (set, list)) else []
            dds = self.dds.get(external)
            dds_type = dds[1]["type"] if dds else ""
            if parsed.kind == "cl" and dds_type == "DSPF" or device == "WORKSTN":
                formats = [fmt["name"] for fmt in dds[1]["formats"]] if dds else []
                used = [fmt for fmt in formats if fmt in fmt_ops or fmt in f.get("formats", []) or fmt in f.get("sfile", [])]
                by_target: dict[str, set[str]] = {}
                for fmt in used or formats:
                    target = self.screens.get(f"{external}.{fmt}")
                    if target:
                        by_target.setdefault(target, set()).update(fmt_ops.get(fmt, set()) | set(f.get("io", [])))
                for target, ops_used in by_target.items():
                    screen_name = self.cat.items[target].name
                    label = "/".join(sorted(ops_used)) or "WORKSTN"
                    screens.append({"name": screen_name, "op": label, "line": f["line"], "resolved": True})
                    if not ops_used or ops_used & {"EXFMT", "WRITE", "SNDRCVF", "SNDF"}:
                        self.cat.link(key, target, "renders", label)
                    if not ops_used or ops_used & {"EXFMT", "READ", "READC", "SNDRCVF", "RCVF"}:
                        self.cat.link(target, key, "submits", label)
                if not dds:
                    screens.append({"name": external, "op": "WORKSTN", "line": f["line"], "resolved": False})
                continue
            if device in ("PRINTER",) or dds_type == "PRTF":
                target = self._file_item(f"file:{external}", external, file, f["line"], "IBM i printer file", {"organization": "printer file", "kind": "printer"})
                self._access(key, target, ["C"], f"PRINTER {f['name']}")
                continue
            if device in ("SEQ", "SPECIAL"):
                target = self._file_item(f"file:{external}", external, file, f["line"], "IBM i file", {"organization": device.lower(), "kind": "file"})
                self._access(key, target, ops, f"{device} {f['name']}")
                continue
            # DISK file (RPG F-spec / dcl-f) or CL DCLF / CPYF / CLRPFM of a database file
            effective = ops
            if parsed.kind == "rpg" and f.get("used_ops"):
                effective = sorted({_op for o in f["used_ops"] for _op in legacy._RPG_FILE_OPS.get(o, "")} | ({"R"} if "R" in ops else set()), key="CRUD".index) or ops
            self._table(external, effective or ["R"], key, file, f["line"], f"{'RPG' if parsed.kind == 'rpg' else 'CL'} file {f['name']}",
                        f"{'F-spec' if parsed.kind == 'rpg' else f.get('via', 'CL')} {f['name']}")
        for override in data.get("overrides", []):
            if override["to"] and override["to"] != override["file"]:
                self._table(override["to"], ["R"], key, file, override["line"], "CL override", f"OVRDBF {override['file']}")

    def _access(self, key: str, target: Item, ops: list[str], detail: str) -> None:
        ops = [op for op in "CRUD" if op in "".join(ops)]
        self.cat.link(key, target.key, "accesses", "/".join(ops) or "R")
        source = self.cat.items.get(key)
        if source is not None:
            accessed = target.details.setdefault("accessed_by", [])
            existing = next((a for a in accessed if a["key"] == key), None)
            if existing is None:
                accessed.append({"key": key, "name": source.name, "kind": source.kind, "ops": ops, "via": detail})
            else:
                existing["ops"] = [op for op in "CRUD" if op in "".join(existing["ops"] + ops)]

    # -- JCL ------------------------------------------------------------------------------------------------------

    def _job_item(self, file: SourceFile, member: dict) -> None:
        steps = []
        datasets: list[str] = []
        for step in member["steps"]:
            for dd in step["dds"]:
                if _data_dd(step, dd) and dd["dsn"] not in datasets:
                    datasets.append(dd["dsn"])
            steps.append({"name": step["name"], "program": step["program"], "proc": step["proc"], "via": step.get("via_program", ""), "plan": step.get("plan", ""), "tool": step.get("tool", False),
                          "parm": step["parm"], "line": step["line"], "cond": step["cond"],
                          "dds": [{k: dd[k] for k in ("name", "dsn", "member", "disp", "sysout", "dummy", "instream", "temporary", "ops", "line")} for dd in step["dds"]]})
        kind = "JCL PROC" if member["type"] == "proc" else "JCL JOB"
        programs = [s["program"] or s["proc"] for s in steps if s["program"] or s["proc"]]
        details = {"job_type": kind, "trigger": kind, "human": f"Batch {'procedure' if member['type'] == 'proc' else 'job'} · {len(steps)} step{'s' if len(steps) != 1 else ''}",
                   "schedule": {}, "steps": steps, "datasets": datasets, "calls": programs, "class": member["class"], "symbols": member["symbols"],
                   "params": member["params"], "includes": [i["name"] for i in member["includes"]]}
        item = self.cat.add(Item("service", "job", f"job:jcl:{member['name']}", member["name"], file.path, member["line"], member["end"], "JCL", "JCL",
                                 f"//{member['name']} {member['type'].upper()}" + (f" CLASS={member['class']}" if member["class"] else ""), details,
                                 code=snippet(file.text, member["line"], member["end"], 200)))
        self.jobs[member["name"]] = item.key

    def _step_dds(self, job_member: dict, step: dict) -> list[tuple[dict, dict]]:
        """(program step, DD) pairs for a job step, expanding EXEC PROC with the job's STEP.DD overrides."""
        if step["program"]:
            return [(step, dd) for dd in step["dds"]]
        proc = self.job_members.get(step["proc"])
        if proc is None:
            return []
        pairs = []
        for proc_step in proc[1]["steps"]:
            overrides = {dd["name"].split(".", 1)[1]: dd for dd in step["dds"] if dd["name"].startswith(f"{proc_step['name']}.")}
            merged = {dd["name"]: dd for dd in proc_step["dds"] if dd["name"]}
            merged.update(overrides)
            pairs += [(proc_step, dd) for dd in merged.values()]
        return pairs

    def _job_links(self, file: SourceFile, member: dict) -> None:
        key = self.jobs.get(member["name"])
        if not key:
            return
        job = self.cat.items[key]
        for step in member["steps"]:
            target = self._program_key(step["program"]) if step["program"] else None
            if target:
                self.cat.link(key, target, "runs", f"{step['name']} EXEC PGM={step['program']}" + (f" via {step['via_program']}" if step.get("via_program") else ""))
            elif step["proc"] and step["proc"] in self.jobs and self.jobs[step["proc"]] != key:
                self.cat.link(key, self.jobs[step["proc"]], "runs", f"{step['name']} EXEC {step['proc']}")
            for dd in step["dds"]:
                if not _data_dd(step, dd):
                    continue
                dataset = self._dataset(dd, file)
                self._access(key, dataset, dd["ops"], f"{step['name']}.{dd['name']} DISP={dd['disp'] or 'NEW'}")
            for program_step, dd in self._step_dds(member, step):
                main_key = self._program_key(program_step["program"]) if program_step["program"] else None
                if not main_key or not _data_dd(program_step, dd):
                    continue
                for program_key, f in self._step_files(main_key):
                    if f["device"] in ("file", "vsam") and f["external"] == dd["name"]:
                        dataset = self._dataset(dd, file)
                        self._access(program_key, dataset, f["ops"] or dd["ops"], f"{f['name']} via DD {dd['name']} ({member['name']}.{program_step['name']})")
                        f["bound"] = dataset.key
                        dataset.details.setdefault("ddnames", [])
                        if dd["name"] not in dataset.details["ddnames"]:
                            dataset.details["ddnames"].append(dd["name"])
                        for record in f.get("records", []):
                            if record not in dataset.details.setdefault("records", []):
                                dataset.details["records"].append(record)
        job.details["programs_resolved"] = [s["program"] for s in member["steps"] if s["program"] and self._program_key(s["program"])]

    def _step_files(self, main_key: str) -> list[tuple[str, dict]]:
        """File declarations of the step program and of every subprogram it calls: they all share the step's DDs."""
        if not hasattr(self, "_callees"):
            self._callees: dict[str, list[str]] = {}
            for lk in self.cat.links:
                if lk.kind == "calls" and lk.source.startswith("program:") and lk.target.startswith("program:"):
                    self._callees.setdefault(lk.source, []).append(lk.target)
        seen, frontier = [main_key], [main_key]
        for _ in range(6):
            frontier = [t for k in frontier for t in self._callees.get(k, []) if t not in seen]
            seen += frontier
        return [(key, f) for key in seen for f in self.program_files.get(key, [])]

    def _dataset(self, dd: dict, file: SourceFile) -> Item:
        member = dd.get("member", "")
        organization = "GDG" if re.match(r"^[+-]?\d+$", member or "") else ("PDS member" if member else "dataset")
        return self._file_item(f"file:{dd['dsn']}", dd["dsn"], file, dd["line"], "z/OS dataset",
                               {"organization": organization, "kind": "dataset", "dsn": dd["dsn"], "defined_in": file.path})

    def _finish(self) -> None:
        """Programs started by JCL are batch mains even when they take a PARM; files get a CRUD matrix like tables."""
        for lk in self.cat.links:
            if lk.kind == "runs" and lk.target.startswith("program:"):
                program = self.cat.items.get(lk.target)
                if program is not None and program.details.get("role") in ("subprogram", "batch main", "batch", "called program"):
                    program.details["role"] = "batch main (JCL)"
                    program.details.setdefault("run_by", [])
                    if lk.source.split(":")[-1] not in program.details["run_by"]:
                        program.details["run_by"].append(lk.source.split(":")[-1])
        for item in self.cat.by_kind("file"):
            crud: dict[str, list[str]] = {}
            for accessor in item.details.get("accessed_by", []):
                for op in accessor.get("ops", []):
                    crud.setdefault(op, []).append(f"{accessor['kind']}:{accessor['name']}")
            item.details["crud"] = {op: sorted(set(crud[op]))[:60] for op in "CRUD" if op in crud}

    def _unbound_files(self) -> None:
        for program_key, files in self.program_files.items():
            program = self.cat.items[program_key]
            path_file = self.ctx.by_path.get(program.path)
            for f in files:
                if f["device"] not in ("file", "vsam") or f.get("bound"):
                    continue
                target = self._file_item(f"file:{f['external']}", f["external"], path_file, f["line"], "COBOL file",
                                         {"organization": f["organization"], "kind": "file (DD name)", "ddnames": [f["external"]], "record_key": f["record_key"],
                                          "access": f["access"], "records": f["records"]})
                self._access(program_key, target, f["ops"], f"SELECT {f['name']} ASSIGN TO {f['assign']}")
            program.details["files"] = [dict({k: v for k, v in f.items() if k != "status"}, target=f.get("bound", "")) for f in files] or program.details.get("files", [])

    # -- CICS transactions ----------------------------------------------------------------------------------------

    def _transactions(self) -> None:
        defined: dict[str, str] = {}
        for file, definition in self.csd:
            if definition["type"] != "TRANSACTION":
                continue
            attrs = definition["attrs"]
            program = attrs.get("PROGRAM", "").upper()
            item = self.cat.add(Item("api", "transaction", f"txn:{definition['name']}", definition["name"], file.path, definition["line"], definition["line"], file.language,
                                     "CICS", f"TRANSACTION({definition['name']}) PROGRAM({program})",
                                     {"transid": definition["name"], "program": program, "group": attrs.get("GROUP", ""), "description": attrs.get("DESCRIPTION", ""),
                                      "profile": attrs.get("PROFILE", ""), "source": "CSD definition", "started_by": []}))
            defined[definition["name"]] = item.key
        for file, parsed in self.parsed:
            if parsed.kind != "cobol":
                continue
            for unit in parsed.data["units"]:
                program_key = self._program_key(unit["name"]) if unit["name"] else None
                if not program_key:
                    continue
                for transid in unit["transids"]:
                    tid = transid["id"]
                    if not tid or not re.match(r"^[\w#@$]{1,4}$", tid):
                        continue
                    if tid not in defined:
                        item = self.cat.add(Item("api", "transaction", f"txn:{tid}", tid, file.path, transid["line"], transid["line"], "COBOL", "CICS",
                                                 f"TRANSID({tid})", {"transid": tid, "program": unit["name"] if transid["via"] == "RETURN" else "", "group": "",
                                                                     "description": "", "source": f"inferred from EXEC CICS {transid['via']} TRANSID", "started_by": []}))
                        defined[tid] = item.key
                    txn = self.cat.items[defined[tid]]
                    if transid["via"] == "START":
                        self.cat.link(program_key, txn.key, "starts", f"EXEC CICS START TRANSID('{tid}')")
                        txn.details["started_by"].append(unit["name"])
                    elif not txn.details.get("program"):
                        txn.details["program"] = unit["name"]
        for key in defined.values():
            txn = self.cat.items[key]
            target = self._program_key(txn.details.get("program", ""))
            txn.details["resolved"] = bool(target)
            if target:
                self.cat.link(key, target, "implemented by", txn.details["program"])


def extract(ctx: Context, cat: Catalog) -> None:
    _Builder(ctx, cat).run()


def link_tables(ctx: Context, cat: Catalog) -> None:
    """After the tables exist: DCLGEN copybooks declare their DB2 tables."""
    for copybook_key, table_key in cat.extra.pop("legacy_declares", []):
        if f"table:{table_key}" in cat.items:
            cat.link(copybook_key, f"table:{table_key}", "declares", "DCLGEN")
