"""Catalogue data model and the shared build context (type index, constants, config values)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.analyzer.catalog.structure import (
    FileStructure,
    TypeDecl,
    parse_structure,
    simple_type,
)

LAYER_ORDER = ("api", "service", "data", "ui", "shared")
STRUCTURED_LANGUAGES = ("Java", "Kotlin", "Swift")


@dataclass
class SourceFile:
    path: str
    language: str
    category: str
    layer: str
    role: str
    is_test: bool
    container: str
    text: str

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def stem(self) -> str:
        return PurePosixPath(self.path).stem


@dataclass
class Item:
    layer: str
    kind: str
    key: str
    name: str
    path: str = ""
    line_start: int = 0
    line_end: int = 0
    language: str = ""
    framework: str = ""
    signature: str = ""
    details: dict = field(default_factory=dict)
    summary: str = ""
    summary_source: str = ""
    code: str = ""  # source excerpt used for LLM enrichment (not stored)

    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.kind}|{self.key}|{self.signature}|{self.code}".encode("utf-8", "replace")).hexdigest()


@dataclass
class Link:
    source: str
    target: str
    kind: str
    detail: str = ""


class TypeKeys(dict):
    """Simple type name -> item key; remembers every item registered under a name (same class name in two apps)."""

    def __init__(self) -> None:
        super().__init__()
        self.alts: dict[str, list[str]] = {}

    def _note(self, name: str, key: str) -> None:
        keys = self.alts.setdefault(name, [])
        if key not in keys:
            keys.append(key)

    def setdefault(self, name, key=None):  # type: ignore[override]
        self._note(name, key)
        return super().setdefault(name, key)

    def __setitem__(self, name, key) -> None:
        self._note(name, key)
        super().__setitem__(name, key)


class Catalog:
    def __init__(self) -> None:
        self.items: dict[str, Item] = {}
        self.links: list[Link] = []
        self._link_keys: set[tuple[str, str, str, str]] = set()
        self.extra: dict = {}
        # cross references resolved by the linker once every extractor has run
        self.type_keys: TypeKeys = TypeKeys()  # class / struct name -> item key
        self.type_members: dict[str, dict[str, str]] = {}  # class -> method -> item key (queries, client calls)
        self.tables_registry: dict[str, dict] = {}  # table key -> merged DDL / ORM description
        self.entity_tables: dict[str, str] = {}  # entity class -> table key
        self.security_items: list[str] = []
        self.pending_calls: list[tuple[str, str, str, int]] = []  # source key, target type, method, line
        self.pending_views: list[tuple[str, str]] = []  # endpoint key, view name
        self.pending_redirects: list[tuple[str, str]] = []  # endpoint key, path
        self.pending_types: list[tuple[str, str, str]] = []  # item key, type name, role (request / response / throws / uses)
        self.pending_topics: list[tuple[str, dict, str]] = []  # item key, {kind, name}, publishes / consumes
        self.pending_implements: list[tuple[str, str]] = []  # item key, interface name
        self.pending_tables: list[tuple[str, str, str, str]] = []  # item key, table key, ops, detail
        self.pending_routines: list[tuple[str, str]] = []  # item key, procedure name
        self.pending_config_usage: list[tuple[str, str, int, str]] = []  # config key, path, line, item key
        self.pending_http: list[tuple[str, str, str, int, str]] = []  # source key, METHOD, url, line, via
        self.pending_screens: list[tuple[str, str, str]] = []  # source key, screen ref (class / route / file), relation

    def add(self, item: Item) -> Item:
        if item.key in self.items:
            existing = self.items[item.key]
            if existing.path == item.path and existing.line_start == item.line_start:
                return existing
            n = 2
            while f"{item.key}#{n}" in self.items:
                n += 1
            item.key = f"{item.key}#{n}"
        self.items[item.key] = item
        return item

    def get(self, key: str) -> Item | None:
        return self.items.get(key)

    def link(self, source: str, target: str, kind: str, detail: str = "") -> None:
        if not source or not target or source == target:
            return
        detail = detail[:500]
        key = (source, target, kind, detail)
        if key in self._link_keys:
            return
        self._link_keys.add(key)
        self.links.append(Link(source, target, kind, detail))

    def by_kind(self, *kinds: str) -> list[Item]:
        return [item for item in self.items.values() if item.kind in kinds]


@dataclass
class TypeRef:
    decl: TypeDecl
    fs: FileStructure
    file: SourceFile

    @property
    def qualified(self) -> str:
        return f"{self.fs.package}.{self.decl.name}" if self.fs.package else self.decl.name


class Context:
    """Everything an extractor may need, built once per analysis."""

    def __init__(self, files: list[SourceFile], dependencies: list[dict], edges: list[tuple[str, str, str]], frameworks: list[str]):
        self.files = files
        self.by_path = {f.path: f for f in files}
        self.dependencies = dependencies
        self.edges = edges
        self.frameworks = frameworks
        self.structures: dict[str, FileStructure] = {}
        self.types: dict[str, list[TypeRef]] = {}
        self.constants: dict[str, str] = {}
        self.config_values: dict[str, str] = {}
        self.imports_of: dict[str, set[str]] = {}
        self.imported_by: dict[str, set[str]] = {}
        for source, target, kind in edges:
            if kind == "IMPORTS":
                self.imports_of.setdefault(source, set()).add(target)
                self.imported_by.setdefault(target, set()).add(source)
        dep_names = " ".join(d.get("name", "") for d in dependencies).lower()
        self.dep_names = dep_names
        self.android = (
            "androidx" in dep_names
            or "com.android" in dep_names
            or any(f.name == "AndroidManifest.xml" for f in files)
            or any(f.language == "Kotlin" and ("import android." in f.text[:4000] or "import androidx." in f.text[:4000] or "@Composable" in f.text[:4000]) for f in files[:400])
        )
        self.spring = "springframework" in dep_names or "spring-boot" in dep_names or any("org.springframework" in (f.text[:4000] if f.language in ("Java", "Kotlin") else "") for f in files[:400])

    def code_files(self, *languages: str, include_tests: bool = False) -> list[SourceFile]:
        return [f for f in self.files if f.language in languages and (include_tests or not f.is_test) and f.text]

    def structure(self, file: SourceFile) -> FileStructure | None:
        if file.language not in STRUCTURED_LANGUAGES or not file.text:
            return None
        if file.path not in self.structures:
            try:
                self.structures[file.path] = parse_structure(file.text, file.language, file.path)
            except (RecursionError, IndexError, ValueError):
                self.structures[file.path] = FileStructure(file.path, file.language)
        return self.structures[file.path]

    def build_index(self) -> None:
        aliases: dict[str, str] = {}
        for file in self.files:
            if file.language not in STRUCTURED_LANGUAGES or not file.text or len(file.text) > 600_000:
                continue
            fs = self.structure(file)
            if fs is None:
                continue
            for decl in fs.all_types():
                if decl.name:
                    self.types.setdefault(decl.name, []).append(TypeRef(decl, fs, file))
                for member in decl.fields():
                    if member.initializer and ({"static", "final"} <= set(member.modifiers) or "const" in member.modifiers or (decl.kind == "object" and "val" in member.modifiers)
                                               or ("final" in member.modifiers and simple_type(member.type) == "String")):
                        value = _literal(member.initializer)
                        if value is not None:
                            self.constants.setdefault(member.name, value)
                            self.constants.setdefault(f"{decl.name}.{member.name}", value)
            for member in fs.fields:
                if member.initializer and "const" in member.modifiers:
                    value = _literal(member.initializer)
                    if value is not None:
                        self.constants.setdefault(member.name, value)
                    elif re.match(r"^[A-Za-z_][\w.]*$", member.initializer.strip()):
                        aliases[member.name] = member.initializer.strip()
            for decl in fs.all_types():
                for member in decl.fields():
                    if member.initializer and ("const" in member.modifiers or {"static", "final"} <= set(member.modifiers)) and re.match(r"^[A-Za-z_][\w.]*$", member.initializer.strip()):
                        aliases.setdefault(member.name, member.initializer.strip())
        for _ in range(3):
            for name, target in aliases.items():
                value = self.constants.get(target) or self.constants.get(target.split(".")[-1])
                if value is not None:
                    self.constants.setdefault(name, value)
            for name, value in list(self.constants.items()):
                if "$" in value:
                    self.constants[name] = re.sub(r"\$\{(\w+)\}|\$([A-Za-z_]\w*)", lambda m: self.constants.get(m.group(1) or m.group(2), m.group(0)), value)

    def type(self, name: str, near: SourceFile | None = None, prefer_test: bool = False) -> TypeRef | None:
        refs = self.types.get(name) or []
        if not refs:
            return None
        if len(refs) == 1:
            return refs[0]
        candidates = [r for r in refs if r.file.is_test == prefer_test] or refs
        if near is not None:
            same = [r for r in candidates if r.file.container == near.container]
            if same:
                candidates = same
            fs = self.structures.get(near.path)
            if fs:
                imported = [r for r in candidates if r.qualified in fs.imports or f"{r.fs.package}.*" in fs.imports or r.fs.package == fs.package]
                if imported:
                    return imported[0]
        return candidates[0]

    def resolve(self, expression: str) -> str:
        """Annotation value with constants and ${config} placeholders resolved: API_PREFIX + "/x" -> "/api/x"."""
        from app.services.analyzer.catalog.structure import string_values

        expression = (expression or "").strip()
        if not expression:
            return ""
        parts = [p.strip() for p in re.split(r"\s*\+\s*", expression)]
        out = []
        for part in parts:
            values = string_values(part)
            if values and re.match(r'^["\'`]', part):
                out.append(values[0])
            elif part in self.constants:
                out.append(self.constants[part])
            elif part.split(".")[-1] in self.constants and re.match(r"^[\w.]+$", part):
                out.append(self.constants.get(part, self.constants[part.split(".")[-1]]))
            else:
                out.append("{" + part + "}" if re.match(r"^[\w.]+$", part) else part)
        return "".join(out)

    def placeholder(self, value: str) -> str:
        """${key:default} -> configured value (or default) when known."""

        def repl(match: re.Match) -> str:
            key, _, default = match.group(1).partition(":")
            return self.config_values.get(key, default if default else match.group(0))

        return re.sub(r"\$\{([^}]+)\}", repl, value or "")


def _literal(initializer: str) -> str | None:
    from app.services.analyzer.catalog.structure import string_values

    text = initializer.strip().rstrip(";")
    if re.match(r'^"(?:\\.|[^"\\])*"$', text) or re.match(r"^'(?:\\.|[^'\\])*'$", text):
        values = string_values(text)
        return values[0] if values else None
    if re.match(r"^-?\d+(?:\.\d+)?[LlFfDd]?$", text):
        return text.rstrip("LlFfDd")
    if re.match(r'^"[^"]*"(?:\s*\+\s*"[^"]*")+$', text):
        return "".join(string_values(text))
    return None


def snippet(text: str, start: int, end: int, limit: int = 120) -> str:
    lines = (text or "").splitlines()
    start = max(1, start)
    end = max(start, min(end or start, start + limit - 1))
    return "\n".join(lines[start - 1 : end])


def normalise_path(path: str) -> str:
    """URL path template normalised for matching: /owners/{id}/pets/:petId?x=1 -> /owners/{}/pets/{}"""
    path = (path or "").strip()
    path = re.sub(r"^[a-z]+://[^/]+", "", path)
    path = path.split("?")[0].split("#")[0]
    path = re.sub(r"^\$\{[^}]*(?:BASE|URL|HOST|ORIGIN|SERVER|ENDPOINT|API)[^}]*\}(?=/|$)", "", path, flags=re.I)
    path = re.sub(r"\$\{[^}]*\}|\\\([^)]*\)|\{[^}]*\}|:[A-Za-z_]\w*|<[^>]+>|\*\*?|%[sd]", "{}", path)
    path = re.sub(r"/+", "/", path).rstrip("/")
    return (path if path.startswith("/") else "/" + path).lower() if path else "/"


def join_paths(*parts: str) -> str:
    out = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        out = out.rstrip("/") + "/" + part.lstrip("/")
    out = re.sub(r"/+", "/", out)
    return out if out.startswith("/") else "/" + out
