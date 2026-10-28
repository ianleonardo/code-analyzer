"""Mainframe and IBM i sources: COBOL (CICS, DB2, batch), RPG (free / fixed), CL, JCL, DDS, BMS and CICS CSD.

The fixture repository (tests/fixtures/legacy_repo) keeps real column layouts: fixed-format COBOL with sequence numbers
and an identification area, RPG and DDS specifications in their columns, BMS with column-72 continuation.
"""

import shutil
from pathlib import Path

import pytest
from app.services.analyzer import classify, graph, ingest, languages, legacy, macro, parsers
from app.services.analyzer.catalog import SourceFile, build

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_repo"


def _text(rel: str) -> str:
    return (FIXTURE / rel).read_text(encoding="utf-8")


# --- source understanding ----------------------------------------------------------------------------------------


def test_cobol_cics_db2_program():
    data = legacy.parse_cobol(_text("cobol/CUSTINQ.cbl"))
    assert data["format"] == "fixed" and not data["copybook"]
    unit = data["units"][0]
    assert unit["name"] == "CUSTINQ" and unit["has_cics"] and unit["has_sql"]
    paragraphs = {p["name"]: p for p in unit["paragraphs"]}
    assert list(paragraphs) == ["0000-MAIN", "1000-SEND-MAP", "2000-PROCESS", "2000-EXIT"]
    assert paragraphs["0000-MAIN"]["performs"] == ["1000-SEND-MAP", "2000-PROCESS", "2000-EXIT"]  # PERFORM ... THRU ...
    assert paragraphs["2000-PROCESS"]["complexity"] == 3  # IF ... AND ...
    # EXEC CICS LINK PROGRAM(WS-PGM-NAME) resolves through the VALUE clause
    assert [(c["target"], c["dynamic"], c["via"], c["paragraph"]) for c in unit["calls"]] == [("CUSTUPD", True, "EXEC CICS LINK", "2000-PROCESS")]
    assert [c["name"] for c in unit["copybooks"]] == ["CUSTMAP", "DCLCUST"]  # COPY and EXEC SQL INCLUDE (SQLCA skipped)
    select = next(s for s in unit["sql"] if s["operation"] == "SELECT")
    assert select["tables"] == {"CUSTOMER": ["R"]}
    assert unit["screens"] == [{"map": "CINQMAP", "mapset": "CUSTSET", "op": "send", "line": 27}, {"map": "CINQMAP", "mapset": "CUSTSET", "op": "receive", "line": 30}]
    assert unit["transids"][0]["id"] == "CINQ" and unit["queues"][0]["name"] == "CUSTLOG"
    assert next(f for f in unit["files"] if f["name"] == "CUSTVSM")["ops"] == ["R"]


def test_cobol_batch_files_and_dynamic_calls():
    unit = legacy.parse_cobol(_text("cobol/CUSTBAT.cbl"))["units"][0]
    files = {f["name"]: f for f in unit["files"]}
    assert files["CUST-IN"]["ops"] == ["R"] and files["CUST-IN"]["records"] == ["CI-REC"]
    assert files["CUST-MST"]["organization"] == "INDEXED" and files["CUST-MST"]["record_key"] == "CM-ID" and files["CUST-MST"]["ops"] == ["R", "U"]
    assert files["RPT-OUT"]["external"] == "RPTOUT" and files["RPT-OUT"]["ops"] == ["C"]  # UT-S-RPTOUT -> DD RPTOUT
    calls = [(c["target"], c["dynamic"]) for c in unit["calls"]]
    assert calls == [("INTCALC", True), ("FEECALC", True), ("AUDITLOG", False)]  # MOVE 'literal' TO WS-CALC-PGM
    assert unit["ends"] == ["STOP RUN"]
    assert unit["sql"][0]["tables"] == {"ACCT_SUMMARY": ["U"]}


def test_copybook_dclgen():
    data = legacy.parse_cobol(_text("copy/DCLCUST.cpy"))
    assert data["copybook"]
    assert data["dclgen"][0]["table"] == "PROD.CUSTOMER"
    assert [c["name"] for c in data["dclgen"][0]["columns"]] == ["CUST_ID", "CUST_NAME", "CUST_BAL"]
    items = data["units"][0]["data_items"]
    assert (items[3]["name"], items[3]["pic"], items[3]["usage"]) == ("CUST-BAL", "S9(9)V9(2)", "COMP-3")


def test_rpg_free_and_fixed():
    free = legacy.parse_rpg(_text("qrpglesrc/ORDENT.sqlrpgle"), "qrpglesrc/ORDENT.sqlrpgle")
    assert free["dialect"] == "ILE RPG (free)"
    assert free["entry"] == ["PORDERNO", "PMODE"] and free["procedures"][0]["params"] == ["MSG"]
    assert {f["name"]: f["device"] for f in free["files"]} == {"ORDDSPF": "WORKSTN", "ORDHDR": "DISK", "CUSTMASL1": "DISK"}
    assert next(f for f in free["files"] if f["name"] == "ORDHDR")["ops"] == {"R", "C", "U"}
    assert [(c["target"], c["via"]) for c in free["calls"]] == [("PRICECALC", "CALLP PRICECALC → EXTPGM")]
    assert free["copybooks"][0]["name"] == "ORDPROTOS"
    assert {s["operation"] for s in free["sql"]} == {"SELECT", "INSERT"}
    assert [s["name"] for s in free["subroutines"]] == ["LOADORDER"]
    fixed = legacy.parse_rpg(_text("qrpglesrc/PRICECALC.rpgle"), "qrpglesrc/PRICECALC.rpgle")
    assert fixed["dialect"] == "RPG IV (fixed)" and fixed["entry"] == ["PITEM", "PPRICE"]
    assert {(c["target"], c["system"]) for c in fixed["calls"]} == {("QCMDEXC", True), ("AUDITPGM", False)}
    assert fixed["files"][0]["name"] == "PRICES" and fixed["fileops"][0]["op"] == "CHAIN"


def test_cl_jcl_dds_bms_csd():
    cl = legacy.parse_cl(_text("qclsrc/NIGHTLY.clle"))
    assert cl["entry"] == ["&DATE"]
    assert [(c["target"], c["via"]) for c in cl["calls"]] == [("PRICECALC", "CALL"), ("ORDENT", "SBMJOB ORDBATCH")]
    assert {f["name"]: f["ops"] for f in cl["files"] if f["ops"]} == {"ORDHDR": ["R"], "ORDHIST": ["C"]}
    job = legacy.parse_jcl(_text("jcl/CUSTJOB.jcl"))["members"][0]
    steps = {s["name"]: s for s in job["steps"]}
    assert steps["STEP010"]["program"] == "CUSTBAT" and steps["STEP030"]["proc"] == "CUSTPROC"
    assert steps["STEP020"]["program"] == "AUDITLOG" and steps["STEP020"]["via_program"] == "IKJEFT01"  # DSN RUN PROGRAM(...)
    rpt = next(dd for dd in steps["STEP010"]["dds"] if dd["name"] == "RPTOUT")
    assert (rpt["dsn"], rpt["member"], rpt["ops"]) == ("PROD.CUST.REPORT", "+1", ["C"])  # continued DD, GDG generation
    pf = legacy.parse_dds(_text("qddssrc/ORDHDR.pf"), "ORDHDR.pf")
    assert pf["type"] == "PF" and pf["unique"] and pf["formats"][0]["keys"] == ["ORDNO"]
    assert legacy.dds_sql_type(pf["formats"][0]["fields"][2]) == "DECIMAL(9,2)"
    dspf = legacy.parse_dds(_text("qddssrc/ORDDSPF.dspf"), "ORDDSPF.dspf")
    ctl = dspf["formats"][1]
    assert ctl["subfile_control_of"] == "ORDSFL" and {k["key"] for k in ctl["function_keys"]} == {"F3", "F5"}
    bms = legacy.parse_bms(_text("bms/CUSTSET.bms"))
    fields = bms["maps"][0]["fields"]
    assert bms["mapset"] == "CUSTSET" and fields[0]["initial"] == "CUSTOMER INQUIRY"  # column-72 continuation
    assert [f["name"] for f in fields if f["name"]] == ["CUSTID", "CUSTNM"]
    csd = legacy.parse_csd(_text("csd/CUSTGRP.csd"))
    assert csd[0] == {"type": "TRANSACTION", "name": "CINQ", "attrs": {"GROUP": "CUSTGRP", "PROGRAM": "CUSTINQ", "DESCRIPTION": "Customer inquiry"}, "line": 1}


def test_no_catastrophic_backtracking():
    import time

    body = "".join(f"       P{i:04d}.\n           GO TO P{i + 1:04d} P{i + 2:04d} P{i + 3:04d}\n               DEPENDING ON WS-X.\n" for i in range(1500))
    source = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. BIG.\n       PROCEDURE DIVISION.\n" + body + "       " + "A-B-C " * 3000 + "\n"
    start = time.time()
    unit = legacy.parse_cobol(source)["units"][0]
    assert time.time() - start < 5 and len(unit["paragraphs"]) == 1500
    assert unit["paragraphs"][0]["go_to"] == ["P0001", "P0002", "P0003"]


def test_language_detection_and_line_counts():
    assert languages.classify_path("src/ORDENT.SQLRPGLE") == ("RPG", "code")
    assert languages.classify_path("maps/CUSTSET.bms") == ("BMS", "markup")
    assert languages.sniff_language("members/INTCALC", _text("members/INTCALC")) == ("COBOL", "code")
    assert languages.sniff_language("QDDSSRC/CUSTMAS", _text("qddssrc/CUSTMAS.pf")) == ("DDS", "code")
    assert languages.sniff_language("docs/readme.txt", "Just some notes.") == ("", "")
    code, comment, _ = languages.count_lines(_text("cobol/CUSTINQ.cbl"), "COBOL")
    assert comment == 1 and code == 43
    code, comment, _ = languages.count_lines("     C* comment\n     C                   EVAL      X = 1\n", "RPG")
    assert (code, comment) == (1, 1)


def test_file_parsers_symbols_and_imports():
    result = parsers.parse(_text("cobol/CUSTINQ.cbl"), "COBOL")
    assert result.namespace == "CUSTINQ" and result.has_main
    assert {s.qualified for s in result.symbols} >= {"CUSTINQ", "CUSTINQ.0000-MAIN", "CUSTINQ.2000-PROCESS"}
    assert {(i.module, i.kind) for i in result.imports} == {("CUSTMAP", "copy"), ("DCLCUST", "copy"), ("CUSTUPD", "call")}
    sub = parsers.parse(_text("members/INTCALC"), "COBOL")
    assert not sub.has_main and {s.qualified for s in sub.symbols} >= {"INTCALC.CALC-INTEREST", "INTCALC.CALC-INTEREST.CALC-1"}
    jcl = parsers.parse(_text("jcl/CUSTJOB.jcl"), "JCL")
    assert {i.module for i in jcl.imports} == {"CUSTBAT", "AUDITLOG", "CUSTPROC"}
    rpg = parsers.parse(_text("qrpglesrc/ORDENT.sqlrpgle"), "RPG", "qrpglesrc/ORDENT.sqlrpgle")
    assert {s.name for s in rpg.symbols} == {"LOGEVENT", "LOADORDER"} and rpg.member_refs >= {"ORDLINE", "EVENTLOG"}


# --- repository-level: graph and catalogue ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def legacy_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("legacy") / "core-banking"
    shutil.copytree(FIXTURE, root)
    return root


@pytest.fixture(scope="module")
def analysed(legacy_repo):
    ws = ingest.walk(legacy_repo, lambda: False, lambda n: None)
    m = macro.discover(ws.files)
    parsed = {f.path: parsers.parse(f.text or "", f.language, f.path) for f in ws.text_files if f.category in ("code", "markup", "script")}
    g = graph.build(parsed, {f.path: f.language for f in ws.text_files}, m.path_aliases, {})
    files = []
    for f in ws.files:
        if f.text is None:
            continue
        cls = classify.classify(f.path, f.language, f.category, f.text, parsed.get(f.path))
        files.append(SourceFile(f.path, f.language, f.category, cls.layer, cls.role, cls.is_test, "", f.text))
    cat = build(files, [], [(e.source, e.target, e.kind) for e in g.edges], [])
    assert cat.extra["stats"]["errors"] == []
    return ws, g, files, cat


def _has(cat, source, kind, target):
    return any(lk.source == source and lk.kind == kind and lk.target == target for lk in cat.links)


def test_graph_edges_and_roles(analysed):
    ws, g, files, _cat = analysed
    edges = {(e.source, e.target, e.kind) for e in g.edges}
    assert ("cobol/CUSTINQ.cbl", "copy/DCLCUST.cpy", "INCLUDES") in edges
    assert ("cobol/CUSTBAT.cbl", "members/INTCALC", "CALLS") in edges  # dynamic CALL resolved to an extension-less member
    assert ("jcl/CUSTJOB.jcl", "cobol/AUDITLOG.cbl", "CALLS") in edges  # IKJEFT01 RUN PROGRAM
    assert ("qclsrc/NIGHTLY.clle", "qrpglesrc/ORDENT.sqlrpgle", "CALLS") in edges
    assert ("qddssrc/CUSTMASL1.lf", "qddssrc/CUSTMAS.pf", "IMPORTS") in edges
    assert g.external_imports["cobol/AUDITLOG.cbl"] == {"CEE3ABD"}
    roles = {f.path: (f.layer, f.role) for f in files}
    assert roles["cobol/CUSTINQ.cbl"] == ("service", "COBOL Program")
    assert roles["members/INTCALC"] == ("service", "COBOL Program")
    assert roles["bms/CUSTSET.bms"][0] == "ui" and roles["jcl/CUSTJOB.jcl"] == ("service", "Batch Job (JCL)")


def test_catalogue_items(analysed):
    cat = analysed[3]
    kinds = {(i.layer, i.kind) for i in cat.items.values()}
    assert {("service", "program"), ("service", "job"), ("api", "transaction"), ("ui", "screen"), ("data", "file"), ("data", "table"), ("data", "view"), ("shared", "copybook")} <= kinds
    programs = {i.name: i for i in cat.by_kind("program")}
    assert set(programs) == {"CUSTINQ", "CUSTBAT", "CUSTUPD", "AUDITLOG", "INTCALC", "ORDENT", "PRICECALC", "NIGHTLY"}
    assert programs["CUSTINQ"].framework == "CICS · DB2" and programs["CUSTINQ"].details["role"] == "online (CICS)"
    assert programs["INTCALC"].details["role"] == "subprogram" and programs["ORDENT"].details["role"] == "interactive (5250)"
    assert programs["CUSTBAT"].details["role"] == "batch main (JCL)" and programs["CUSTBAT"].details["run_by"] == ["CUSTJOB"]
    customer = cat.items["table:customer"]
    assert [c["name"] for c in customer.details["columns"]] == ["CUST_ID", "CUST_NAME", "CUST_BAL"] and customer.details["schema"] == "PROD"
    ordhdr = cat.items["table:ordhdr"]
    assert ordhdr.details["primary_key"] == ["ORDNO"] and next(c for c in ordhdr.details["columns"] if c["name"] == "ORDTOT")["type"] == "DECIMAL(9,2)"
    screen = cat.items["screen:bms:CUSTSET.CINQMAP"]
    assert screen.details["title"] == "CUSTOMER INQUIRY"
    assert [(f["name"], f["widget"], f["label"]) for f in screen.details["fields"]] == [("CUSTID", "input", "CUSTOMER ID"), ("CUSTNM", "output", "NAME")]
    ctl = cat.items["screen:5250:ORDDSPF.ORDCTL"]
    assert ctl.details["subfile"] == "ORDSFL" and any(f["subfile"] and f["name"] == "ITEM" for f in ctl.details["fields"])
    assert next(f for f in ctl.details["fields"] if f["name"] == "ORDNO")["required"]
    job = cat.items["job:jcl:CUSTJOB"]
    assert job.details["calls"] == ["CUSTBAT", "AUDITLOG", "CUSTPROC"] and "PROD.CUST.MASTER.KSDS" in job.details["datasets"]
    assert cat.items["txn:CINQ"].details["resolved"]


def test_catalogue_links(analysed):
    cat = analysed[3]
    assert _has(cat, "txn:CINQ", "implemented by", "program:CUSTINQ")
    assert _has(cat, "program:CUSTINQ", "renders", "screen:bms:CUSTSET.CINQMAP") and _has(cat, "screen:bms:CUSTSET.CINQMAP", "submits", "program:CUSTINQ")
    assert _has(cat, "program:CUSTINQ", "calls", "program:CUSTUPD") and _has(cat, "program:CUSTUPD", "calls", "program:CUSTINQ")
    assert _has(cat, "program:CUSTINQ", "uses", "copybook:DCLCUST") and _has(cat, "copybook:DCLCUST", "declares", "table:customer")
    assert _has(cat, "program:CUSTINQ", "accesses", "table:customer")
    assert _has(cat, "program:CUSTBAT", "calls", "program:INTCALC") and _has(cat, "program:CUSTBAT", "calls", "program:AUDITLOG")
    assert _has(cat, "job:jcl:CUSTJOB", "runs", "program:CUSTBAT") and _has(cat, "job:jcl:CUSTJOB", "runs", "job:jcl:CUSTPROC")
    # batch (via the JCL DD) and online (via the CSD FILE definition) reach the same VSAM dataset
    master = cat.items["file:PROD.CUST.MASTER.KSDS"]
    assert {(a["name"], "".join(a["ops"])) for a in master.details["accessed_by"]} >= {("CUSTBAT", "RU"), ("CUSTINQ", "R"), ("CUSTJOB", "RU")}
    assert master.details["ddnames"] == ["CUSTMST"] and master.details["cics_files"] == ["CUSTVSM"]
    assert master.details["crud"] == {"R": ["job:CUSTJOB", "program:CUSTBAT", "program:CUSTINQ"], "U": ["job:CUSTJOB", "program:CUSTBAT"]}
    assert _has(cat, "program:CUSTBAT", "accesses", "file:PROD.CUST.REPORT")
    assert _has(cat, "program:ORDENT", "calls", "program:PRICECALC") and _has(cat, "program:NIGHTLY", "calls", "program:ORDENT")
    assert _has(cat, "program:ORDENT", "renders", "screen:5250:ORDDSPF.ORDCTL") and _has(cat, "screen:5250:ORDDSPF.ORDCTL", "submits", "program:ORDENT")
    assert _has(cat, "program:ORDENT", "accesses", "view:custmasl1") and _has(cat, "view:custmasl1", "accesses", "table:custmas")
    ops = next(lk.detail for lk in cat.links if lk.source == "program:ORDENT" and lk.target == "table:ordhdr")
    assert ops == "C/R"  # CHAIN + WRITE of the ORDHDRR record format
    assert any(lk.source == "program:CUSTINQ" and lk.kind == "publishes" and lk.target.endswith(":CUSTLOG") for lk in cat.links)
    crud = cat.items["table:customer"].details["crud"]
    assert crud["R"] == ["program:CUSTINQ"]


def test_legacy_analysis_api(client, admin, legacy_repo):
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(legacy_repo), "name": "core-banking"}, headers=admin)
    analysis_id = response.json()["started_analysis"]["analysis_id"]
    assert client.get(f"/api/analyses/{analysis_id}", headers=admin).json()["status"] == "completed"
    summary = client.get(f"/api/analyses/{analysis_id}/catalog", headers=admin).json()
    kinds = {k["kind"]: k for k in summary["layers"]["service"]["kinds"]}
    assert kinds["program"]["label"] == "Programs" and kinds["program"]["count"] == 8
    programs = client.get(f"/api/analyses/{analysis_id}/catalog/items", params={"layer": "service", "kind": "program"}, headers=admin).json()["items"]
    inq = next(p for p in programs if p["name"] == "CUSTINQ")
    assert inq["badge"]["role"] == "online (CICS)"
    txn = client.get(f"/api/analyses/{analysis_id}/catalog/items", params={"layer": "api", "kind": "transaction"}, headers=admin).json()["items"][0]
    trace = client.get(f"/api/analyses/{analysis_id}/catalog/items/{txn['item_id']}/trace", headers=admin).json()
    assert {"CINQ", "CUSTINQ", "CUSTOMER", "CUSTSET.CINQMAP"} <= {n["name"] for n in trace["nodes"]}
    crud = client.get(f"/api/analyses/{analysis_id}/catalog/crud", headers=admin).json()
    assert any(a["kind"] == "program" for a in crud["actors"]) and any(r["table"] == "CUSTOMER" and r["cells"] for r in crud["rows"])
    assert any(r["table"] == "PROD.CUST.MASTER.KSDS" and r["cells"].get("program:CUSTBAT") == "RU" for r in crud["rows"])
    md = client.get(f"/api/analyses/{analysis_id}/catalog/export.md", headers=admin)
    assert md.status_code == 200 and "### Programs" in md.text
