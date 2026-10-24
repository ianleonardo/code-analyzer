"""The Code Context Graph at file level: resolution of imports into IMPORTS edges, plus EXTENDS / IMPLEMENTS /
INJECTS / CALLS edges, Tarjan strongly connected components, the condensed DAG, PageRank and blast radius.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.analyzer.parsers import ImportRef, ParseResult

JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".d.ts", ".mts", ".cts")
JVM_EXTS = (".java", ".kt", ".kts", ".scala", ".groovy")


@dataclass
class Edge:
    source: str
    target: str
    kind: str  # IMPORTS, EXTENDS, IMPLEMENTS, INJECTS, CALLS, INCLUDES
    weight: int = 1
    detail: str = ""


@dataclass
class GraphResult:
    edges: list[Edge]
    external_imports: dict[str, set[str]]  # file -> external module names
    fan_in: dict[str, int]
    fan_out: dict[str, int]
    pagerank: dict[str, float]
    scc_of: dict[str, int]
    sccs: list[list[str]]
    topo_order: list[str]  # dependencies first (bottom-up)
    unresolved: int = 0
    resolved: int = 0
    adjacency: dict[str, set[str]] = field(default_factory=dict)
    reverse: dict[str, set[str]] = field(default_factory=dict)


class Resolver:
    def __init__(self, parsed: dict[str, ParseResult], languages: dict[str, str], aliases: dict[str, list[str]], go_modules: dict[str, str]):
        self.paths = set(parsed)
        self.parsed = parsed
        self.languages = languages
        self.aliases = aliases
        self.go_modules = go_modules
        self.by_name: dict[str, list[str]] = defaultdict(list)  # lowercase basename without extension -> paths
        self.by_dir: dict[str, list[str]] = defaultdict(list)
        for path in self.paths:
            pure = PurePosixPath(path)
            stem = pure.name.split(".")[0].lower()
            self.by_name[stem].append(path)
            directory = str(pure.parent)
            self.by_dir["" if directory == "." else directory].append(path)
        # type / symbol name -> defining files
        self.type_index: dict[str, list[str]] = defaultdict(list)
        self.symbol_index: dict[str, list[str]] = defaultdict(list)
        self.namespace_index: dict[str, list[str]] = defaultdict(list)
        self.program_index: dict[str, str] = {}
        for path, result in parsed.items():
            for symbol in result.symbols:
                if symbol.kind in ("class", "interface", "enum", "record", "struct", "trait", "object", "type", "protocol", "module"):
                    self.type_index[symbol.name].append(path)
                if not symbol.parent and symbol.kind in ("function", "class", "interface", "struct", "type", "procedure", "program"):
                    self.symbol_index[symbol.name].append(path)
                if symbol.kind == "program":
                    self.program_index[symbol.name.upper()] = path
            if result.namespace:
                self.namespace_index[result.namespace].append(path)

    # -- helpers --------------------------------------------------------------------------------------------------

    def _closest(self, source: str, candidates: list[str]) -> str | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        source_parts = PurePosixPath(source).parts

        def shared(path: str) -> int:
            count = 0
            for a, b in zip(source_parts, PurePosixPath(path).parts, strict=False):
                if a != b:
                    break
                count += 1
            return count

        return max(candidates, key=lambda path: (shared(path), -len(path)))

    def _suffix_match(self, source: str, suffix_parts: list[str], exts: tuple[str, ...]) -> str | None:
        if not suffix_parts:
            return None
        stem = suffix_parts[-1].lower()
        candidates = []
        for path in self.by_name.get(stem, []):
            pure = PurePosixPath(path)
            if exts and not path.endswith(exts):
                continue
            parts = list(pure.parent.parts) + [pure.name.split(".")[0]]
            if [p.lower() for p in parts[-len(suffix_parts):]] == [p.lower() for p in suffix_parts]:
                candidates.append(path)
        return self._closest(source, candidates)

    def _file_or_index(self, base: str, exts: tuple[str, ...]) -> str | None:
        base = str(PurePosixPath(base))
        base = "" if base == "." else base
        if base in self.paths:
            return base
        for ext in exts:
            if base + ext in self.paths:
                return base + ext
        for ext in exts:
            candidate = f"{base}/index{ext}" if base else f"index{ext}"
            if candidate in self.paths:
                return candidate
        return None

    @staticmethod
    def _join(directory: str, relative: str) -> str:
        parts: list[str] = [p for p in directory.split("/") if p] if directory else []
        for piece in relative.split("/"):
            if piece in ("", "."):
                continue
            if piece == "..":
                if parts:
                    parts.pop()
                continue
            parts.append(piece)
        return "/".join(parts)

    # -- per language ---------------------------------------------------------------------------------------------

    def resolve(self, source: str, ref: ImportRef) -> list[str]:
        language = self.languages.get(source, "")
        directory = str(PurePosixPath(source).parent)
        directory = "" if directory == "." else directory
        module = ref.module
        if language == "Python":
            return self._python(source, directory, ref)
        if language in ("JavaScript", "TypeScript", "Vue", "Svelte"):
            return self._javascript(directory, module)
        if language in ("Java", "Kotlin", "Scala", "Groovy"):
            return self._jvm(source, module)
        if language == "Go":
            return self._go(source, module)
        if language == "Rust":
            return self._rust(source, directory, ref)
        if language == "PHP":
            if ref.kind == "include":
                hit = self._file_or_index(self._join(directory, module), ("",))
                return [hit] if hit else []
            hit = self._suffix_match(source, module.split("."), (".php",))
            return [hit] if hit else []
        if language == "Ruby":
            if ref.kind == "require_relative":
                hit = self._file_or_index(self._join(directory, module), (".rb",))
                return [hit] if hit else []
            hit = self._suffix_match(source, module.split("/"), (".rb",))
            return [hit] if hit else []
        if language in ("C", "C++", "C/C++ Header", "Objective-C"):
            hit = self._file_or_index(self._join(directory, module), ("",))
            if not hit:
                hit = self._suffix_match(source, PurePosixPath(module).with_suffix("").parts, (".h", ".hpp", ".hh", ".c", ".cpp", ".cc"))
            return [hit] if hit else []
        if language in ("COBOL", "COBOL Copybook", "RPG", "CL"):
            if ref.kind == "call":
                target = self.program_index.get(module.upper())
                if not target:
                    target = self._closest(source, self.by_name.get(module.lower(), []))
                return [target] if target else []
            hit = self._closest(source, self.by_name.get(module.lower(), []))
            return [hit] if hit else []
        return []

    def _python(self, source: str, directory: str, ref: ImportRef) -> list[str]:
        exts = (".py", ".pyi")
        results: list[str] = []
        if ref.level:
            base_dir = directory
            for _ in range(ref.level - 1):
                base_dir = str(PurePosixPath(base_dir).parent) if base_dir else ""
                base_dir = "" if base_dir == "." else base_dir
            base = self._join(base_dir, ref.module.replace(".", "/")) if ref.module else base_dir
            for name in ref.names or [""]:
                if name and name != "*":
                    hit = self._py_module(f"{base}/{name}" if base else name, exts)
                    if hit:
                        results.append(hit)
                        continue
                hit = self._py_module(base, exts)
                if hit:
                    results.append(hit)
            return results
        parts = ref.module.split(".")
        for name in ref.names or [""]:
            if name and name != "*":
                hit = self._py_suffix(source, parts + [name])
                if hit:
                    results.append(hit)
                    continue
            hit = self._py_suffix(source, parts)
            if hit:
                results.append(hit)
        return results

    def _py_module(self, base: str, exts) -> str | None:
        for ext in exts:
            if base + ext in self.paths:
                return base + ext
        if f"{base}/__init__.py" in self.paths:
            return f"{base}/__init__.py"
        return None

    def _py_suffix(self, source: str, parts: list[str]) -> str | None:
        hit = self._suffix_match(source, parts, (".py", ".pyi"))
        if hit:
            return hit
        candidates = [path for path in self.by_name.get("__init__", []) if [p.lower() for p in PurePosixPath(path).parent.parts[-len(parts):]] == [p.lower() for p in parts]]
        return self._closest(source, candidates)

    def _javascript(self, directory: str, module: str) -> list[str]:
        if module.startswith("."):
            hit = self._file_or_index(self._join(directory, module), JS_EXTS)
            return [hit] if hit else []
        for alias, folders in self.aliases.items():
            if alias == "~baseUrl":
                continue
            if module == alias or module.startswith(alias + "/"):
                rest = module[len(alias) :].lstrip("/")
                for folder in folders:
                    hit = self._file_or_index(f"{folder}/{rest}".strip("/") if rest else folder, JS_EXTS)
                    if hit:
                        return [hit]
        if module.startswith(("@/", "~/")):
            for root in ("src", "app", ""):
                hit = self._file_or_index(f"{root}/{module[2:]}".strip("/"), JS_EXTS)
                if hit:
                    return [hit]
        for folder in self.aliases.get("~baseUrl", []):
            hit = self._file_or_index(f"{folder}/{module}", JS_EXTS)
            if hit:
                return [hit]
        return []

    def _jvm(self, source: str, module: str) -> list[str]:
        parts = module.split(".")
        if parts[-1] == "*":
            package_parts = parts[:-1]
            return [
                path for path in self.paths
                if path.endswith(JVM_EXTS) and [p.lower() for p in PurePosixPath(path).parent.parts[-len(package_parts):]] == [p.lower() for p in package_parts]
            ][:50]
        hit = self._suffix_match(source, parts, JVM_EXTS)
        if not hit and len(parts) > 1:  # static import of a member: drop the member
            hit = self._suffix_match(source, parts[:-1], JVM_EXTS)
        return [hit] if hit else []

    def _go(self, source: str, module: str) -> list[str]:
        for root, name in self.go_modules.items():
            if module == name or module.startswith(name + "/"):
                rel = module[len(name) :].strip("/")
                folder = f"{root}/{rel}".strip("/") if root else rel
                return [path for path in self.by_dir.get(folder, []) if path.endswith(".go") and not path.endswith("_test.go")]
        return []

    def _rust(self, source: str, directory: str, ref: ImportRef) -> list[str]:
        if ref.kind == "mod":
            for candidate in (f"{directory}/{ref.module}.rs", f"{directory}/{ref.module}/mod.rs"):
                candidate = candidate.lstrip("/")
                if candidate in self.paths:
                    return [candidate]
            return []
        parts = ref.module.split("::")
        if parts[0] in ("crate", "super", "self"):
            parts = parts[1:]
        for length in range(len(parts), 0, -1):
            hit = self._suffix_match(source, parts[:length], (".rs",))
            if hit:
                return [hit]
        return []


def build(parsed: dict[str, ParseResult], languages: dict[str, str], aliases: dict[str, list[str]], go_modules: dict[str, str]) -> GraphResult:
    resolver = Resolver(parsed, languages, aliases, go_modules)
    edges: dict[tuple[str, str, str], Edge] = {}
    external: dict[str, set[str]] = defaultdict(set)
    resolved = unresolved = 0

    def add(source: str, target: str, kind: str, detail: str = "", weight: int = 1) -> None:
        if not target or target == source:
            return
        key = (source, target, kind)
        if key in edges:
            edges[key].weight += weight
        else:
            edges[key] = Edge(source, target, kind, weight, detail[:200])

    for source, result in parsed.items():
        language = languages.get(source, "")
        for ref in result.imports:
            if language in ("C#", "VB.NET", "F#") and ref.module in resolver.namespace_index:
                resolved += 1  # internal namespace; edges come from the type references below
                continue
            targets = resolver.resolve(source, ref)
            if targets:
                resolved += 1
                kind = "INCLUDES" if ref.kind in ("include", "copy") else ("CALLS" if ref.kind == "call" else "IMPORTS")
                for target in targets:
                    add(source, target, kind, ref.module)
            else:
                if _looks_external(ref, language):
                    external[source].add(_package_name(ref.module, language))
                else:
                    unresolved += 1
        # Type references: same package (JVM), namespaces (C#), and base types / injection everywhere
        directory = str(PurePosixPath(source).parent)
        type_targets: dict[str, str] = {}
        for name in result.member_refs:
            files = resolver.type_index.get(name)
            if not files:
                continue
            target = None
            same_dir = [path for path in files if str(PurePosixPath(path).parent) == directory]
            if language in ("Java", "Kotlin", "Scala", "Groovy", "C#", "VB.NET", "F#", "Go", "PHP", "Swift", "Dart"):
                if same_dir:
                    target = resolver._closest(source, same_dir)
                elif language in ("C#", "VB.NET", "F#", "PHP"):
                    usings = {ref.module for ref in result.imports}
                    in_scope = [path for path in files if parsed[path].namespace and (parsed[path].namespace in usings or parsed[path].namespace == result.namespace or result.namespace.startswith(parsed[path].namespace + "."))]
                    target = resolver._closest(source, in_scope)
                elif language in ("Java", "Kotlin", "Scala", "Groovy"):
                    in_package = [path for path in files if parsed[path].namespace == result.namespace and result.namespace]
                    target = resolver._closest(source, in_package)
            if target and target != source:
                type_targets[name] = target
                if (source, target, "IMPORTS") not in edges:
                    add(source, target, "IMPORTS", f"type {name}")
        for symbol in result.symbols:
            for base in symbol.bases:
                target = type_targets.get(base) or _unique(resolver.type_index.get(base), source, resolver)
                if target:
                    kind = "IMPLEMENTS" if _is_interface(parsed, target, base) else "EXTENDS"
                    add(source, target, kind, f"{symbol.name} → {base}")
            for injected in symbol.injects:
                target = type_targets.get(injected) or _unique(resolver.type_index.get(injected) or resolver.symbol_index.get(injected), source, resolver)
                if target:
                    add(source, target, "INJECTS", f"{symbol.name} ← {injected}")
    # CALLS: a file calls symbols defined in files it depends on
    imports_of: dict[str, set[str]] = defaultdict(set)
    for edge in edges.values():
        imports_of[edge.source].add(edge.target)
    defined: dict[str, set[str]] = {path: {s.name for s in result.symbols} for path, result in parsed.items()}
    for source, result in parsed.items():
        for target in imports_of.get(source, ()):
            names = defined.get(target, set())
            count = sum(result.calls.get(name, 0) for name in names)
            if count:
                add(source, target, "CALLS", "", count)

    edge_list = list(edges.values())
    nodes = sorted(parsed)
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    reverse: dict[str, set[str]] = {node: set() for node in nodes}
    for edge in edge_list:
        if edge.source in adjacency and edge.target in adjacency:
            adjacency[edge.source].add(edge.target)
            reverse[edge.target].add(edge.source)
    sccs = tarjan(nodes, adjacency)
    scc_of: dict[str, int] = {}
    cyclic = [component for component in sccs if len(component) > 1]
    for index, component in enumerate(cyclic, start=1):
        for node in component:
            scc_of[node] = index
    weights: dict[tuple[str, str], float] = defaultdict(float)
    for edge in edge_list:
        weights[(edge.source, edge.target)] += 1.0 + (0.5 if edge.kind in ("INJECTS", "EXTENDS", "IMPLEMENTS") else 0) + min(edge.weight, 10) * 0.05
    return GraphResult(
        edges=edge_list,
        external_imports={key: value for key, value in external.items()},
        fan_in={node: len(reverse[node]) for node in nodes},
        fan_out={node: len(adjacency[node]) for node in nodes},
        pagerank=pagerank(nodes, adjacency, weights),
        scc_of=scc_of,
        sccs=cyclic,
        topo_order=condensed_order(nodes, adjacency, sccs),
        unresolved=unresolved,
        resolved=resolved,
        adjacency=adjacency,
        reverse=reverse,
    )


def _unique(candidates: list[str] | None, source: str, resolver: Resolver) -> str | None:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return resolver._closest(source, candidates) if len(candidates) <= 3 else None


def _is_interface(parsed: dict[str, ParseResult], path: str, name: str) -> bool:
    for symbol in parsed[path].symbols:
        if symbol.name == name:
            return symbol.kind in ("interface", "trait", "protocol")
    return False


_STDLIB_HINT = re.compile(r"^(java|javax|jakarta|kotlin|scala|System|Microsoft|os|sys|re|json|typing|collections|datetime|fmt|net|io|std|core|alloc)\b")


def _looks_external(ref: ImportRef, language: str) -> bool:
    module = ref.module
    if language in ("JavaScript", "TypeScript", "Vue", "Svelte"):
        return not module.startswith((".", "/", "@/", "~/"))
    if language == "Python":
        return ref.level == 0
    if language in ("Java", "Kotlin", "Scala", "Groovy", "C#", "Go", "Rust", "PHP", "Ruby", "VB.NET", "F#"):
        return True
    return False


def _package_name(module: str, language: str) -> str:
    if language in ("JavaScript", "TypeScript", "Vue", "Svelte"):
        parts = module.split("/")
        return "/".join(parts[:2]) if module.startswith("@") else parts[0]
    if language == "Python":
        return module.split(".")[0]
    if language == "Go":
        return "/".join(module.split("/")[:3])
    if language == "Rust":
        return module.split("::")[0]
    if language in ("Java", "Kotlin", "Scala", "Groovy", "C#", "VB.NET", "F#", "PHP"):
        return ".".join(module.replace("\\", ".").split(".")[:3])
    return module


# --- algorithms ---------------------------------------------------------------------------------------------------


def tarjan(nodes: list[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan SCC (no recursion limit on deep graphs)."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index_of:
            continue
        work = [(root, iter(sorted(adjacency.get(root, ()))))]
        index_of[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(adjacency.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    item = stack.pop()
                    on_stack.discard(item)
                    component.append(item)
                    if item == node:
                        break
                result.append(sorted(component))
    return result


def condensed_order(nodes: list[str], adjacency: dict[str, set[str]], sccs: list[list[str]]) -> list[str]:
    """Bottom-up order over the condensed DAG: a file comes after everything it depends on (cycles grouped)."""
    group: dict[str, int] = {}
    for index, component in enumerate(sccs):
        for node in component:
            group[node] = index
    dag: dict[int, set[int]] = defaultdict(set)
    indegree: dict[int, int] = {index: 0 for index in range(len(sccs))}
    for node in nodes:
        for target in adjacency.get(node, ()):
            a, b = group[node], group[target]
            if a != b and b not in dag[a]:
                dag[a].add(b)
    # reverse edges so dependencies (sinks) come first
    reverse: dict[int, set[int]] = defaultdict(set)
    for a, targets in dag.items():
        for b in targets:
            reverse[b].add(a)
            indegree[a] += 1
    queue = deque(sorted(index for index, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        current = queue.popleft()
        order += sccs[current]
        for dependant in sorted(reverse.get(current, ())):
            indegree[dependant] -= 1
            if indegree[dependant] == 0:
                queue.append(dependant)
    return order


def pagerank(nodes: list[str], adjacency: dict[str, set[str]], weights: dict[tuple[str, str], float], damping: float = 0.85, iterations: int = 60) -> dict[str, float]:
    """Importance flows from a file to the files it depends on, so heavily used modules rank highest."""
    count = len(nodes)
    if count == 0:
        return {}
    rank = {node: 1.0 / count for node in nodes}
    out_weight = {node: sum(weights.get((node, target), 1.0) for target in adjacency.get(node, ())) for node in nodes}
    for _ in range(iterations):
        dangling = sum(rank[node] for node in nodes if not adjacency.get(node))
        fresh = {node: (1 - damping) / count + damping * dangling / count for node in nodes}
        for node in nodes:
            total = out_weight[node]
            if not total:
                continue
            share = damping * rank[node]
            for target in adjacency[node]:
                fresh[target] += share * weights.get((node, target), 1.0) / total
        delta = sum(abs(fresh[node] - rank[node]) for node in nodes)
        rank = fresh
        if delta < 1e-9:
            break
    return rank


def blast_radius(reverse: dict[str, set[str]], start: str, max_depth: int = 6) -> dict[str, int]:
    """Files that (transitively) depend on `start`, with their distance."""
    seen = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if seen[node] >= max_depth:
            continue
        for dependant in reverse.get(node, ()):
            if dependant not in seen:
                seen[dependant] = seen[node] + 1
                queue.append(dependant)
    seen.pop(start, None)
    return seen
