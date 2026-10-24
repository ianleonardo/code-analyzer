"""Hybrid retrieval over a stored analysis: BM25 lexical search (identifier-aware tokens) + dense vectors (when
embeddings exist), fused with Reciprocal Rank Fusion, then expanded one hop through the code graph.
Works fully offline from the database; vectors are optional.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter, OrderedDict

from app import db
from app.services import llm

_cache: OrderedDict[str, "_Index"] = OrderedDict()
_cache_lock = threading.Lock()
CACHE_SIZE = 6
STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "is", "are", "for", "on", "with", "what", "how", "does", "do", "which", "where",
        "this", "that", "it", "be", "by", "as", "from", "at", "can", "i", "we", "you", "me", "show", "explain", "tell", "about", "there", "any", "all"}


SUFFIXES = ("ations", "ation", "ators", "ator", "ions", "ion", "ings", "ing", "ers", "er", "ors", "or", "ed", "es", "s")


def stem(word: str) -> str:
    """Tiny suffix stripper: validated / validation / validator -> valid."""
    if len(word) <= 4:
        return word
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    out: list[str] = []
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", text):
        lower = word.lower()
        if lower not in STOP and len(lower) > 1:
            out.append(stem(lower))
        parts = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", word.replace("_", " "))
        if len(parts) > 1:
            out += [stem(p.lower()) for p in parts if len(p) > 1 and p.lower() not in STOP]
    return out


ROLE_WORDS = {
    "api": "api endpoint route controller handler request http rest",
    "service": "service business logic use case workflow process",
    "data": "data database persistence repository model entity table sql query store",
    "ui": "ui frontend page screen component view",
    "shared": "shared util config common",
}
CONCEPT_WORDS = {
    "db-read": "database read query select fetch",
    "db-write": "database write insert update delete save persist",
    "http-call": "http call client request external api",
    "env-access": "environment config variable",
    "validation": "validation validate",
    "tx-scope": "transaction commit rollback",
    "auth": "authentication authorization login password security",
    "messaging": "message queue event publish consume kafka",
}


class _Index:
    def __init__(self, analysis_id: str):
        rows = db.fetchall("SELECT chunk_id, path, scope, kind, line_start, line_end, content, embedding FROM analysis_chunks WHERE analysis_id = ?", [analysis_id])
        meta = {
            r["path"]: r
            for r in db.fetchall("SELECT path, layer, role, concepts_json, is_test, language FROM analysis_files WHERE analysis_id = ?", [analysis_id])
        }
        self.rows = rows
        self.weights: list[float] = []
        docs = []
        for r in rows:
            info = meta.get(r["path"]) or {}
            try:
                concepts = json.loads(info.get("concepts_json") or "[]")
            except ValueError:
                concepts = []
            # file profile words let questions like "API endpoint to the database" reach controllers and repositories
            profile = " ".join([ROLE_WORDS.get(info.get("layer", ""), ""), info.get("role", ""), *(CONCEPT_WORDS.get(c, c) for c in concepts)])
            docs.append(Counter(tokens(f"{r['path']} {r['scope']} {profile} {r['content']}")))
            weight = 1.0
            if info.get("is_test"):
                weight *= 0.45
            if r["kind"] == "catalog":
                weight *= 1.15  # distilled facts: endpoints, tables, screens and their links
            elif r["kind"] in ("config", "doc") or not info:
                weight *= 0.6
            self.weights.append(weight)
        self.docs = docs
        self.lengths = [sum(doc.values()) for doc in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        df: Counter = Counter()
        for doc in self.docs:
            df.update(doc.keys())
        n = len(self.docs)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}
        self.vectors: list[list[float] | None] = []
        for row in rows:
            try:
                self.vectors.append(json.loads(row["embedding"]) if row["embedding"] else None)
            except ValueError:
                self.vectors.append(None)
        self.has_vectors = any(v is not None for v in self.vectors)

    def bm25(self, query: str, top: int = 40) -> list[int]:
        terms = tokens(query)
        if not terms:
            return []
        wants_tests = any(t in ("test", "tests", "spec", "testing") for t in terms)
        wants_config = any(t in ("config", "configuration", "yaml", "deploy", "deployment", "docker", "kubernetes", "readme", "docs") for t in terms)
        scores: list[tuple[float, int]] = []
        k1, b = 1.4, 0.75
        for index, doc in enumerate(self.docs):
            score = 0.0
            length = self.lengths[index] or 1
            for term in terms:
                freq = doc.get(term)
                if freq:
                    score += self.idf.get(term, 0) * freq * (k1 + 1) / (freq + k1 * (1 - b + b * length / self.avg))
            if score:
                path = self.rows[index]["path"].lower()
                if any(term in path for term in terms):
                    score *= 1.25
                weight = self.weights[index]
                if weight < 1 and ((wants_tests and "test" in path) or (wants_config and self.rows[index]["kind"] in ("config", "doc"))):
                    weight = 1.0
                scores.append((score * weight, index))
        scores.sort(reverse=True)
        return [index for _, index in scores[:top]]

    def dense(self, query: str, top: int = 40) -> list[int]:
        if not self.has_vectors:
            return []
        vector = llm.embed(query)
        if not vector:
            return []
        norm_q = math.sqrt(sum(v * v for v in vector)) or 1.0
        scored = []
        for index, candidate in enumerate(self.vectors):
            if not candidate or len(candidate) != len(vector):
                continue
            dot = sum(a * b for a, b in zip(vector, candidate, strict=False))
            norm = math.sqrt(sum(v * v for v in candidate)) or 1.0
            scored.append((dot / (norm_q * norm), index))
        scored.sort(reverse=True)
        return [index for _, index in scored[:top]]


def _index(analysis_id: str) -> _Index:
    with _cache_lock:
        if analysis_id in _cache:
            _cache.move_to_end(analysis_id)
            return _cache[analysis_id]
    index = _Index(analysis_id)
    with _cache_lock:
        _cache[analysis_id] = index
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return index


def forget(analysis_id: str) -> None:
    with _cache_lock:
        _cache.pop(analysis_id, None)


def search(analysis_id: str, query: str, top: int = 10) -> dict:
    index = _index(analysis_id)
    lexical = index.bm25(query)
    dense = index.dense(query) if index.has_vectors else []
    fused: dict[int, float] = {}
    for ranking in (lexical, dense):
        for rank, item in enumerate(ranking):
            fused[item] = fused.get(item, 0.0) + 1.0 / (60 + rank + 1)
    # direct mentions of file names or symbols in the question get a boost
    mentioned = {m.lower() for m in re.findall(r"[\w./-]+\.\w{1,6}|\b[A-Z][A-Za-z0-9]{3,}\b|\b\w+_\w+\b", query)}
    if mentioned:
        for position, row in enumerate(index.rows):
            name = row["path"].rsplit("/", 1)[-1].lower()
            scope = row["scope"].lower()
            if name in mentioned or any(m in scope.split(".") or m == scope for m in mentioned) or any(m in row["path"].lower() for m in mentioned if "/" in m or "." in m):
                fused[position] = fused.get(position, 0.0) + 0.03
    ranked = sorted(fused, key=lambda i: -fused[i])
    chosen: list[dict] = []
    per_file: Counter = Counter()
    for position in ranked:
        row = index.rows[position]
        if per_file[row["path"]] >= 3:
            continue
        per_file[row["path"]] += 1
        chosen.append({k: row[k] for k in ("chunk_id", "path", "scope", "kind", "line_start", "line_end", "content")} | {"score": round(fused[position], 4)})
        if len(chosen) >= top:
            break
    return {"chunks": chosen, "mode": "hybrid (BM25 + vectors, RRF)" if dense else "BM25 lexical", "neighbours": _neighbours(analysis_id, [c["path"] for c in chosen[:4]])}


def _neighbours(analysis_id: str, paths: list[str]) -> list[dict]:
    """1-hop graph expansion around the best hits: who uses them and what they depend on."""
    if not paths:
        return []
    marks = ", ".join("?" for _ in paths)
    rows = db.fetchall(
        f"SELECT source_path, target_path, kind FROM analysis_edges WHERE analysis_id = ? AND (source_path IN ({marks}) OR target_path IN ({marks}))",
        [analysis_id, *paths, *paths],
    )
    return rows[:60]
