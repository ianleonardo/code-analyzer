"""Renders the Markdown reports (comparison, pull-request impact) into a styled multi-page PDF 1.4 with no external dependencies.

Supports headings (#, ##, ###), bullet lists, pipe tables (the row above a ---- separator is the header) and paragraphs.
Text is drawn with the standard Helvetica/Courier fonts, so characters outside Latin-1 are transliterated or replaced.
"""

import re
import textwrap

_PDF_TRANSLATE = str.maketrans({
    "—": "-",
    "–": "-",
    "−": "-",
    "·": " | ",
    "•": "-",
    "→": "->",
    "←": "<-",
    "Δ": "Delta ",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "…": "...",
    "×": "x",
    "≥": ">=",
    "≤": "<=",
    "✓": "yes",
    "★": "*",
    "↑": "+",
    "↓": "-",
})


def _pdf_escape(text: str) -> str:
    clean = str(text or "").translate(_PDF_TRANSLATE)
    clean = clean.encode("ascii", "replace").decode("ascii")
    return clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render(md_text: str, footer_label: str = "Code Analyzer Report") -> bytes:
    """Markdown text -> PDF bytes."""
    page_w, page_h = 595.28, 841.89
    margin_x = 42.0
    top_y = page_h - 48.0
    bottom_y = 48.0

    pages: list[list[str]] = [[]]
    y = top_y

    def cur_page() -> list[str]:
        return pages[-1]

    def new_page() -> None:
        nonlocal y
        pages.append([])
        y = top_y

    def ensure_space(height: float) -> None:
        if y - height < bottom_y:
            new_page()

    def draw_text(x: float, y_pos: float, text: str, font: str = "F1", size: float = 9.5, rgb: tuple[float, float, float] = (0.12, 0.16, 0.23)) -> None:
        r, g, b = rgb
        cur_page().append(f"BT /{font} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y_pos:.2f} Td ({_pdf_escape(text)}) Tj ET")

    def draw_rect(x: float, y_pos: float, w: float, h: float, rgb: tuple[float, float, float]) -> None:
        r, g, b = rgb
        cur_page().append(f"{r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y_pos:.2f} {w:.2f} {h:.2f} re f")

    def strip_md_inline(line: str) -> str:
        s = line.replace("**", "").replace("`", "")
        return s.strip()

    def is_separator(text: str) -> bool:
        return text.startswith("|") and "-" in text and set(text) <= set("|:- ")

    source = md_text.splitlines()
    table_width = page_w - 2 * margin_x
    char_w = 0.6 * 8.2  # Courier 8.2pt

    def split_cells(text: str) -> list[str]:
        parts = re.split(r"(?<!\\)\|", text.strip().strip("|"))
        return [strip_md_inline(p.replace("\\|", "|")) for p in parts]

    # Column widths per table, proportional to the longest cell in each column (so paths get the room they need).
    widths_at: dict[int, list[float]] = {}
    index = 0
    while index < len(source):
        if not source[index].startswith("|"):
            index += 1
            continue
        start = index
        while index < len(source) and source[index].startswith("|"):
            index += 1
        rows = [split_cells(r) for r in source[start:index] if not is_separator(r.strip())]
        columns = max(len(r) for r in rows)
        natural = [max(4, min(48, max((len(r[c]) if c < len(r) else 0) for r in rows))) for c in range(columns)]
        need = [n * char_w + 8.0 for n in natural]
        if sum(need) <= table_width:  # spare room goes to every column evenly
            widths = [w + (table_width - sum(need)) / columns for w in need]
        else:
            # Short columns (status, counts, kinds) keep their natural width; long ones share the rest and wrap.
            short = [n <= 14 for n in natural]
            fixed = sum(w for w, is_short in zip(need, short) if is_short)
            flexible = sum(w for w, is_short in zip(need, short) if not is_short)
            room = max(table_width - fixed, table_width * 0.4)
            widths = [w if is_short else w * room / flexible for w, is_short in zip(need, short)]
            total = sum(widths)
            widths = [w * table_width / total for w in widths]
        for row_index in range(start, index):
            widths_at[row_index] = widths

    for index, raw_line in enumerate(source):
        line = raw_line.rstrip()
        if not line:
            y -= 6.0
            continue

        if line.startswith("# "):
            ensure_space(36.0)
            title_text = strip_md_inline(line[2:])
            wrapped = textwrap.wrap(title_text, width=62) or [title_text]
            box_h = 16.0 * len(wrapped) + 14.0
            draw_rect(margin_x, y - box_h + 6.0, page_w - 2 * margin_x, box_h, (0.06, 0.09, 0.16))
            for idx, part in enumerate(wrapped):
                draw_text(margin_x + 10.0, y - 12.0 - idx * 16.0, part, font="F2", size=13.5, rgb=(0.98, 0.98, 1.0))
            y -= box_h + 8.0
            continue

        if line.startswith("## "):
            ensure_space(28.0)
            y -= 6.0
            sec_text = strip_md_inline(line[3:])
            draw_rect(margin_x, y - 4.0, page_w - 2 * margin_x, 18.0, (0.94, 0.96, 0.98))
            draw_rect(margin_x, y - 4.0, 3.0, 18.0, (0.01, 0.52, 0.78))
            draw_text(margin_x + 8.0, y + 1.0, sec_text.upper(), font="F2", size=10.5, rgb=(0.06, 0.09, 0.16))
            y -= 20.0
            continue

        if line.startswith("### ") or line.startswith("#### "):
            ensure_space(20.0)
            y -= 3.0
            sub_text = strip_md_inline(line.lstrip("#"))
            draw_text(margin_x, y, sub_text, font="F2", size=9.8, rgb=(0.01, 0.41, 0.63))
            y -= 14.0
            continue

        if line.startswith("|"):
            if is_separator(line):
                continue
            cells = split_cells(line)
            widths = widths_at.get(index) or [table_width / max(1, len(cells))] * len(cells)
            is_header = index + 1 < len(source) and is_separator(source[index + 1].strip())
            wrapped_cells = []
            for col, cell in enumerate(cells):
                width = widths[col] if col < len(widths) else widths[-1]
                per_line = max(3, int((width - 8.0) / char_w))
                parts = textwrap.wrap(cell, width=per_line, break_long_words=True, break_on_hyphens=False) or [""]
                if len(parts) > 6:
                    parts = parts[:5] + [parts[5][: max(1, per_line - 2)] + ".."]
                wrapped_cells.append(parts)
            row_h = 10.0 * max(len(c) for c in wrapped_cells) + 3.5
            ensure_space(row_h + 2.0)
            if is_header:
                draw_rect(margin_x, y - row_h + 10.0, table_width, row_h, (0.91, 0.94, 0.97))
            else:
                cur_page().append(f"0.900 0.920 0.950 RG 0.4 w {margin_x:.2f} {y - row_h + 10.0:.2f} m {margin_x + table_width:.2f} {y - row_h + 10.0:.2f} l S")
            x = margin_x
            for col, parts in enumerate(wrapped_cells):
                for line_no, part in enumerate(parts):
                    draw_text(x + 4.0, y - line_no * 10.0, part, font="F2" if is_header else "F3", size=8.2,
                              rgb=(0.06, 0.09, 0.16) if is_header else (0.18, 0.22, 0.30))
                x += widths[col] if col < len(widths) else widths[-1]
            y -= row_h
            continue

        is_bullet = line.startswith("- ") or line.startswith("* ")
        clean_line = strip_md_inline(line[2:] if is_bullet else line)
        prefix = "- " if is_bullet else ""
        wrapped = textwrap.wrap(clean_line, width=88) or [clean_line]
        for idx, part in enumerate(wrapped):
            ensure_space(13.0)
            draw_text(
                margin_x + (10.0 if is_bullet and idx > 0 else 0.0),
                y,
                (prefix if idx == 0 else "") + part,
                font="F1",
                size=9.2,
                rgb=(0.15, 0.19, 0.26),
            )
            y -= 12.5

    total_pages = len(pages)
    for idx, page_cmds in enumerate(pages, start=1):
        footer = f"{footer_label} | Page {idx} of {total_pages}"
        page_cmds.append(
            f"BT /F1 8.0 Tf 0.450 0.500 0.580 rg {margin_x:.2f} 24.00 Td ({_pdf_escape(footer)}) Tj ET"
        )

    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(b"")  # placeholder for Pages object (id 2)
    f1_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    f3_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")

    page_ids: list[int] = []
    for page_cmds in pages:
        stream_bytes = "\n".join(page_cmds).encode("ascii", "replace")
        content_id = add_obj(b"<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n" + stream_bytes + b"\nendstream")
        page_dict = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w:.2f} {page_h:.2f}] "
            f"/Resources << /Font << /F1 {f1_id} 0 R /F2 {f2_id} 0 R /F3 {f3_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids.append(add_obj(page_dict))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = [0]
    for obj_id, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{obj_id} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(out)
