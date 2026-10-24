import time

from app.services.analyzer import parsers


def test_python_ast():
    result = parsers.parse("import os\nfrom .x import y\n\nclass A(B):\n    def m(self, svc: Service):\n        if a and b:\n            return 1\n", "Python")
    assert result.parse_mode == "ast"
    assert [(s.kind, s.qualified) for s in result.symbols] == [("class", "A"), ("method", "A.m")]
    assert result.symbols[0].bases == ["B"] and result.symbols[1].complexity == 3
    assert result.imports[1].level == 1 and result.imports[1].names == ["y"]


def test_java_symbols_annotations_and_injection():
    source = '''package com.acme;
import com.acme.data.UserRepository;
import org.springframework.web.bind.annotation.*;

@RestController
public class UserController extends Base implements Api {
    public UserController(UserRepository repo) { this.repo = repo; }

    @GetMapping("/users/{id}")
    public User get(Long id) {
        Runnable r = () -> { helper(); };
        if (id == null) { return null; }
        return repo.find(id);
    }
}
'''
    result = parsers.parse(source, "Java")
    names = {(s.kind, s.qualified) for s in result.symbols}
    assert ("class", "UserController") in names and ("method", "UserController.get") in names
    ctor = next(s for s in result.symbols if s.kind == "constructor")
    assert ctor.injects == ["UserRepository"]
    assert result.namespace == "com.acme" and "RestController" in result.annotations
    assert "/users/{id}" in result.routes
    assert any(i.module == "org.springframework.web.bind.annotation.*" for i in result.imports)


def test_typescript_and_go():
    ts = parsers.parse("import { A } from './a';\nexport class S extends Base {\n  constructor(private repo: Repo) {}\n  run() { return this.repo.go(); }\n}\nexport const f = async (x) => x;\n", "TypeScript")
    assert {s.qualified for s in ts.symbols} >= {"S", "S.run", "f"}
    go = parsers.parse('package main\n\nimport (\n  "fmt"\n  "example.com/app/internal/store"\n)\n\ntype Server struct{}\n\nfunc (s *Server) Run() { fmt.Println(store.Open()) }\n\nfunc main() {}\n', "Go")
    assert go.has_main and {s.qualified for s in go.symbols} >= {"Server", "Server.Run", "main"}


def test_cobol_copybooks_and_calls():
    cobol = "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. PAYCALC.\n       PROCEDURE DIVISION.\n       MAIN-PARA.\n           COPY CUSTREC.\n           CALL 'TAXCALC'.\n           PERFORM CALC-PARA.\n       CALC-PARA.\n           IF A > B DISPLAY 'X'.\n"
    result = parsers.parse(cobol, "COBOL")
    assert result.namespace == "PAYCALC"
    assert {i.module for i in result.imports} == {"CUSTREC", "TAXCALC"}


def test_no_catastrophic_backtracking_on_whitespace():
    source = "/*\n" + " *\n" * 2000 + "*/\n" + "\n" * 5000 + "class A {\n" + "    \n" * 3000 + "}\n"
    start = time.time()
    parsers.parse(source, "Java")
    assert time.time() - start < 2
