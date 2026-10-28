"""Layer catalogue: per-layer drill-down (API, Service, Data, UI, Shared) with cross-layer traceability.

build() runs every extractor over the analysed files and returns a Catalog of items and links. It works from the
in-memory pipeline data or from what an analysis stored in the database (so older analyses can be rebuilt offline).
"""

from __future__ import annotations

import logging
import time

from app.services.analyzer.catalog import android, camel, config, data, jvm, linker, mobile_http, python, swift, web
from app.services.analyzer.catalog.model import Catalog, Context, Item, Link, SourceFile

logger = logging.getLogger(__name__)
__all__ = ["build", "Catalog", "Item", "Link", "SourceFile", "Context"]


def _step(name: str, fn, *args, errors: list[str]):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - one extractor must never sink the catalogue
        logger.exception("Catalogue extractor %s failed", name)
        errors.append(f"{name}: {exc.__class__.__name__}: {exc}"[:300])
        return None


def build(files: list[SourceFile], dependencies: list[dict], edges: list[tuple[str, str, str]], frameworks: list[str], log=lambda _m: None) -> Catalog:
    started = time.time()
    errors: list[str] = []
    ctx = Context(files, dependencies, edges, frameworks)
    cat = Catalog()
    _step("index", ctx.build_index, errors=errors)
    entries = _step("config", config.prime, ctx, errors=errors) or []
    state = _step("schema", data.extract_mappings, ctx, cat, errors=errors) or {"schema_files": [], "migrations": [], "sequences": []}
    cat.extra["openapi_ops"] = _step("openapi", web.openapi_ops, ctx, errors=errors) or {}
    _step("jvm", jvm.extract, ctx, cat, errors=errors)
    _step("camel", camel.extract, ctx, cat, errors=errors)
    _step("python", python.extract, ctx, cat, errors=errors)
    _step("android", android.extract, ctx, cat, errors=errors)
    _step("ios", swift.extract, ctx, cat, errors=errors)
    _step("http-calls", mobile_http.extract, ctx, cat, errors=errors)
    _step("web", web.extract, ctx, cat, errors=errors)
    _step("data", data.extract_late, ctx, cat, entries, state, errors=errors)
    _step("config-items", config.extract, ctx, cat, entries, errors=errors)
    _step("tables", data.finalize_tables, ctx, cat, errors=errors)
    _step("links", linker.link, ctx, cat, errors=errors)
    counts: dict[str, int] = {}
    for item in cat.items.values():
        counts[f"{item.layer}.{item.kind}"] = counts.get(f"{item.layer}.{item.kind}", 0) + 1
    cat.extra["stats"] = {"counts": counts, "items": len(cat.items), "links": len(cat.links), "seconds": round(time.time() - started, 2), "errors": errors,
                          "android": ctx.android, "spring": ctx.spring, "version": 3}
    log(f"Layer catalogue: {len(cat.items)} items, {len(cat.links)} links in {cat.extra['stats']['seconds']}s" + (f" ({len(errors)} extractor error(s))" if errors else ""))
    return cat
