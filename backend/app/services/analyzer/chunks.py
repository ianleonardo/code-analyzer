"""Syntax-aware chunking for retrieval: one chunk per top-level symbol (large classes split per method), plus a module
header chunk and window chunks for docs/config. Each chunk is addressed by SHA-256 of its normalised text and its
scope (`file :: Class.method`), so unchanged code keeps the same id across commits and its embedding is reused.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

MAX_CHARS = 4000
CLASS_SPLIT_LINES = 120
WINDOW = 60


@dataclass
class Chunk:
    path: str
    scope: str
    kind: str
    line_start: int
    line_end: int
    content: str

    @property
    def breadcrumb(self) -> str:
        return f"# {self.path} :: {self.scope}" if self.scope else f"# {self.path}"

    @property
    def content_hash(self) -> str:
        normalised = re.sub(r"\s+", " ", self.content).strip()
        return hashlib.sha256(f"{self.scope}\n{normalised}".encode()).hexdigest()


def _slice(lines: list[str], start: int, end: int) -> str:
    text = "\n".join(lines[max(0, start - 1) : end])
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n…"


def chunk_file(path: str, text: str, category: str, symbols: list[dict]) -> list[Chunk]:
    lines = text.splitlines()
    chunks: list[Chunk] = []
    if category in ("code", "script", "markup") and symbols:
        top = sorted((s for s in symbols if not s["parent"]), key=lambda s: s["line_start"])
        covered_until = 0
        first_line = top[0]["line_start"] if top else len(lines) + 1
        if first_line > 1:
            header = _slice(lines, 1, min(first_line - 1, 80))
            if header.strip():
                chunks.append(Chunk(path, "(module)", "module", 1, min(first_line - 1, 80), header))
        for symbol in top:
            if symbol["line_start"] <= covered_until:
                continue
            span = symbol["line_end"] - symbol["line_start"] + 1
            members = [s for s in symbols if s["parent"] == symbol["name"]]
            if span > CLASS_SPLIT_LINES and members:
                head_end = min(members[0]["line_start"] - 1, symbol["line_start"] + 30)
                chunks.append(Chunk(path, symbol["qualified_name"], symbol["kind"], symbol["line_start"], head_end, _slice(lines, symbol["line_start"], head_end)))
                for member in members:
                    chunks.append(Chunk(path, member["qualified_name"], member["kind"], member["line_start"], member["line_end"], _slice(lines, member["line_start"], member["line_end"])))
            else:
                chunks.append(Chunk(path, symbol["qualified_name"], symbol["kind"], symbol["line_start"], symbol["line_end"], _slice(lines, symbol["line_start"], symbol["line_end"])))
            covered_until = symbol["line_end"]
        return [chunk for chunk in chunks if chunk.content.strip()]
    if category in ("doc", "config", "code", "script", "markup", "style") and text.strip():
        step = WINDOW if category != "doc" else 40
        for start in range(0, len(lines), step):
            end = min(len(lines), start + step)
            content = _slice(lines, start + 1, end)
            if content.strip():
                heading = next((line.strip("# ").strip() for line in lines[start:end] if line.startswith("#")), "") if category == "doc" else ""
                chunks.append(Chunk(path, heading[:120] or f"lines {start + 1}-{end}", category, start + 1, end, content))
            if len(chunks) >= 40:
                break
    return chunks
