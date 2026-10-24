"""Database access for DuckDB and PostgreSQL behind one small API.

The schema is written once in portable SQL (VARCHAR, TEXT, INTEGER, BIGINT, DOUBLE PRECISION, BOOLEAN, TIMESTAMP)
so every table and column is identical on both engines. Choose the engine with DB_ENGINE=duckdb|postgres.

Callers always write `?` placeholders; for Postgres they are rewritten to psycopg's `%s`.
Timestamps are stored as naive UTC.
"""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from app import config

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_conn: Any = None
_engine: str | None = None
_tx_depth = 0


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def engine() -> str:
    return _engine or config.db_engine()


# --- connection -------------------------------------------------------------------------------------------------


def _connect():
    kind = config.db_engine()
    if kind == "postgres":
        import psycopg

        url = config.database_url()
        if not url:
            raise RuntimeError("DB_ENGINE=postgres needs DATABASE_URL, e.g. postgresql://user:pass@host:5432/codeanalyzer")
        conn = psycopg.connect(url, autocommit=True)
        conn.execute("SET TIME ZONE 'UTC'")
        return kind, conn
    import duckdb

    path = config.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return kind, duckdb.connect(str(path))


def init() -> None:
    global _conn, _engine
    with _lock:
        if _conn is not None:
            return
        _engine, _conn = _connect()
        _migrate()
        from app import seed

        seed.run()


def reset() -> None:
    global _conn, _engine
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            finally:
                _conn = None
                _engine = None


def _require():
    init()
    return _conn


def _sql(sql: str) -> str:
    if engine() == "postgres":
        return sql.replace("%", "%%").replace("?", "%s")
    return sql


def _value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _params(params: Iterable | None) -> list:
    return [_value(item) for item in (params or [])]


def exec_sql(sql: str, params: Iterable | None = None) -> None:
    with _lock:
        conn = _require()
        if engine() == "postgres":
            conn.execute(_sql(sql), _params(params))
        else:
            conn.execute(sql, _params(params))


def fetchall(sql: str, params: Iterable | None = None) -> list[dict]:
    with _lock:
        conn = _require()
        if engine() == "postgres":
            cursor = conn.execute(_sql(sql), _params(params))
        else:
            cursor = conn.execute(sql, _params(params))
        if cursor.description is None:
            return []
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def fetchone(sql: str, params: Iterable | None = None) -> dict | None:
    rows = fetchall(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Iterable | None = None) -> Any:
    row = fetchone(sql, params)
    if not row:
        return None
    return next(iter(row.values()))


def insert(table: str, values: dict) -> None:
    columns = list(values)
    marks = ", ".join("?" for _ in columns)
    exec_sql(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})", [values[c] for c in columns])


def insert_many(table: str, columns: list[str], rows: list[tuple | list], chunk: int = 2000) -> None:
    """Bulk insert. Rows are tuples in `columns` order."""
    if not rows:
        return
    marks = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})"
    with _lock:
        conn = _require()
        for start in range(0, len(rows), chunk):
            batch = [_params(row) for row in rows[start : start + chunk]]
            if engine() == "postgres":
                with conn.cursor() as cursor:
                    cursor.executemany(_sql(sql), batch)
            else:
                _duckdb_bulk(conn, table, columns, batch, sql)


def _duckdb_bulk(conn, table: str, columns: list[str], batch: list[list], sql: str) -> None:
    """DuckDB's executemany is row-by-row; a multi-row VALUES statement is far faster."""
    width = len(columns)
    step = max(1, 900 // max(width, 1))
    for start in range(0, len(batch), step):
        part = batch[start : start + step]
        row_marks = "(" + ", ".join("?" for _ in range(width)) + ")"
        statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES " + ", ".join(row_marks for _ in part)
        flat = [value for row in part for value in row]
        conn.execute(statement, flat)


@contextmanager
def transaction():
    """All statements inside run as one unit. Nested use joins the outer transaction."""
    global _tx_depth
    with _lock:
        conn = _require()
        outer = _tx_depth == 0
        if outer:
            conn.execute("BEGIN")
        _tx_depth += 1
        try:
            yield
        except BaseException:
            _tx_depth -= 1
            if outer:
                conn.execute("ROLLBACK")
            raise
        _tx_depth -= 1
        if outer:
            conn.execute("COMMIT")


def table_counts() -> dict[str, int]:
    return {name: int(scalar(f"SELECT count(*) AS c FROM {name}") or 0) for name in TABLES}


# --- schema -------------------------------------------------------------------------------------------------------
# One definition for both engines. Bump a new list entry in MIGRATIONS for every change; never edit an applied one.

SCHEMA_V1 = [
    """
    CREATE TABLE IF NOT EXISTS roles (
        role_id VARCHAR(80) PRIMARY KEY,
        name VARCHAR(80) NOT NULL,
        description VARCHAR(300) NOT NULL DEFAULT '',
        repositories VARCHAR(10) NOT NULL DEFAULT 'none',
        insights VARCHAR(10) NOT NULL DEFAULT 'none',
        settings VARCHAR(10) NOT NULL DEFAULT 'none',
        is_system BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id VARCHAR(64) PRIMARY KEY,
        username VARCHAR(120) NOT NULL,
        login_id VARCHAR(64) NOT NULL UNIQUE,
        password_hash VARCHAR(200) NOT NULL,
        role_id VARCHAR(80) NOT NULL,
        auth_provider VARCHAR(10) NOT NULL DEFAULT 'local',
        ldap_domain VARCHAR(100) NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL,
        last_login_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        settings_id VARCHAR(20) PRIMARY KEY,
        active_provider VARCHAR(20) NOT NULL DEFAULT 'spirare',
        global_system_prompt TEXT NOT NULL,
        llm_file_limit INTEGER NOT NULL DEFAULT 40,
        embedding_chunk_limit INTEGER NOT NULL DEFAULT 3000,
        updated_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS llm_providers (
        provider VARCHAR(20) PRIMARY KEY,
        label VARCHAR(40) NOT NULL,
        chat_model VARCHAR(200) NOT NULL DEFAULT '',
        chat_base_url VARCHAR(500) NOT NULL DEFAULT '',
        embedding_model VARCHAR(200) NOT NULL DEFAULT '',
        embedding_base_url VARCHAR(500) NOT NULL DEFAULT '',
        api_key_enc TEXT NOT NULL DEFAULT '',
        embedding_api_key_enc TEXT NOT NULL DEFAULT '',
        verify_ssl BOOLEAN NOT NULL DEFAULT TRUE,
        max_model_length INTEGER NOT NULL DEFAULT 128000,
        updated_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS git_credentials (
        credential_id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        provider VARCHAR(30) NOT NULL,
        host VARCHAR(255) NOT NULL,
        username VARCHAR(200) NOT NULL DEFAULT '',
        account_name VARCHAR(200) NOT NULL DEFAULT '',
        token_enc TEXT NOT NULL,
        auth_method VARCHAR(20) NOT NULL DEFAULT 'token',
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        last_verified_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS repositories (
        repo_id VARCHAR(64) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        source_type VARCHAR(30) NOT NULL,
        url VARCHAR(1000) NOT NULL DEFAULT '',
        host VARCHAR(255) NOT NULL DEFAULT '',
        owner VARCHAR(255) NOT NULL DEFAULT '',
        slug VARCHAR(255) NOT NULL DEFAULT '',
        default_branch VARCHAR(255) NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        is_private BOOLEAN NOT NULL DEFAULT FALSE,
        created_by VARCHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analyses (
        analysis_id VARCHAR(64) PRIMARY KEY,
        repo_id VARCHAR(64) NOT NULL,
        branch VARCHAR(255) NOT NULL DEFAULT '',
        commit_sha VARCHAR(64) NOT NULL DEFAULT '',
        commit_message TEXT NOT NULL DEFAULT '',
        commit_author VARCHAR(255) NOT NULL DEFAULT '',
        commit_date VARCHAR(64) NOT NULL DEFAULT '',
        status VARCHAR(20) NOT NULL,
        stage VARCHAR(80) NOT NULL DEFAULT '',
        progress DOUBLE PRECISION NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        merkle_root VARCHAR(64) NOT NULL DEFAULT '',
        file_count INTEGER NOT NULL DEFAULT 0,
        loc_total BIGINT NOT NULL DEFAULT 0,
        health_score DOUBLE PRECISION,
        architecture_style VARCHAR(60) NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        stats_json TEXT NOT NULL DEFAULT '{}',
        macro_json TEXT NOT NULL DEFAULT '{}',
        c4_json TEXT NOT NULL DEFAULT '{}',
        llm_used BOOLEAN NOT NULL DEFAULT FALSE,
        started_by VARCHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL,
        started_at TIMESTAMP,
        finished_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_events (
        event_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        ts TIMESTAMP NOT NULL,
        stage VARCHAR(80) NOT NULL,
        level VARCHAR(10) NOT NULL,
        message TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_containers (
        container_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        name VARCHAR(255) NOT NULL,
        path VARCHAR(1000) NOT NULL,
        ecosystem VARCHAR(40) NOT NULL DEFAULT '',
        kind VARCHAR(40) NOT NULL DEFAULT '',
        manifest VARCHAR(1000) NOT NULL DEFAULT '',
        frameworks_json TEXT NOT NULL DEFAULT '[]',
        file_count INTEGER NOT NULL DEFAULT 0,
        loc BIGINT NOT NULL DEFAULT 0,
        summary TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_files (
        file_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        path VARCHAR(1000) NOT NULL,
        directory VARCHAR(1000) NOT NULL DEFAULT '',
        language VARCHAR(40) NOT NULL DEFAULT '',
        container VARCHAR(255) NOT NULL DEFAULT '',
        layer VARCHAR(20) NOT NULL DEFAULT 'shared',
        role VARCHAR(60) NOT NULL DEFAULT '',
        role_confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
        size_bytes BIGINT NOT NULL DEFAULT 0,
        loc INTEGER NOT NULL DEFAULT 0,
        code_lines INTEGER NOT NULL DEFAULT 0,
        comment_lines INTEGER NOT NULL DEFAULT 0,
        blank_lines INTEGER NOT NULL DEFAULT 0,
        complexity INTEGER NOT NULL DEFAULT 0,
        max_function_complexity INTEGER NOT NULL DEFAULT 0,
        symbol_count INTEGER NOT NULL DEFAULT 0,
        fan_in INTEGER NOT NULL DEFAULT 0,
        fan_out INTEGER NOT NULL DEFAULT 0,
        pagerank DOUBLE PRECISION NOT NULL DEFAULT 0,
        scc_id INTEGER,
        is_entry BOOLEAN NOT NULL DEFAULT FALSE,
        is_test BOOLEAN NOT NULL DEFAULT FALSE,
        is_generated BOOLEAN NOT NULL DEFAULT FALSE,
        is_source BOOLEAN NOT NULL DEFAULT TRUE,
        sha256 VARCHAR(64) NOT NULL DEFAULT '',
        summary_source VARCHAR(10) NOT NULL DEFAULT '',
        concepts_json TEXT NOT NULL DEFAULT '[]',
        evidence_json TEXT NOT NULL DEFAULT '[]',
        summary TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_file_contents (
        file_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        content TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_symbols (
        symbol_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        path VARCHAR(1000) NOT NULL,
        name VARCHAR(500) NOT NULL,
        qualified_name VARCHAR(1000) NOT NULL,
        kind VARCHAR(30) NOT NULL,
        parent VARCHAR(500) NOT NULL DEFAULT '',
        line_start INTEGER NOT NULL DEFAULT 0,
        line_end INTEGER NOT NULL DEFAULT 0,
        complexity INTEGER NOT NULL DEFAULT 1,
        signature TEXT NOT NULL DEFAULT '',
        exported BOOLEAN NOT NULL DEFAULT FALSE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_edges (
        edge_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        source_path VARCHAR(1000) NOT NULL,
        target_path VARCHAR(1000) NOT NULL,
        kind VARCHAR(20) NOT NULL,
        weight INTEGER NOT NULL DEFAULT 1,
        detail VARCHAR(500) NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_dependencies (
        dependency_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        container VARCHAR(255) NOT NULL DEFAULT '',
        ecosystem VARCHAR(40) NOT NULL,
        name VARCHAR(300) NOT NULL,
        version VARCHAR(120) NOT NULL DEFAULT '',
        scope VARCHAR(20) NOT NULL DEFAULT 'runtime',
        manifest VARCHAR(1000) NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_concerns (
        concern_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        severity VARCHAR(10) NOT NULL,
        category VARCHAR(60) NOT NULL,
        title VARCHAR(500) NOT NULL,
        path VARCHAR(1000) NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        evidence TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_chunks (
        chunk_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        path VARCHAR(1000) NOT NULL,
        scope VARCHAR(1000) NOT NULL DEFAULT '',
        kind VARCHAR(30) NOT NULL,
        line_start INTEGER NOT NULL DEFAULT 0,
        line_end INTEGER NOT NULL DEFAULT 0,
        content TEXT NOT NULL,
        content_hash VARCHAR(64) NOT NULL,
        embedding TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS comparisons (
        comparison_id VARCHAR(64) PRIMARY KEY,
        left_analysis_id VARCHAR(64) NOT NULL,
        right_analysis_id VARCHAR(64) NOT NULL,
        title VARCHAR(500) NOT NULL DEFAULT '',
        result_json TEXT NOT NULL,
        narrative TEXT NOT NULL DEFAULT '',
        created_by VARCHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id VARCHAR(64) PRIMARY KEY,
        user_id VARCHAR(64) NOT NULL,
        scope_type VARCHAR(20) NOT NULL,
        scope_id VARCHAR(64) NOT NULL,
        title VARCHAR(300) NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chat_messages (
        message_id VARCHAR(64) PRIMARY KEY,
        session_id VARCHAR(64) NOT NULL,
        role VARCHAR(20) NOT NULL,
        content TEXT NOT NULL,
        sources_json TEXT NOT NULL DEFAULT '[]',
        created_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        audit_id VARCHAR(64) PRIMARY KEY,
        ts TIMESTAMP NOT NULL,
        user_id VARCHAR(64),
        login_id VARCHAR(100),
        action VARCHAR(80) NOT NULL,
        method VARCHAR(10) NOT NULL DEFAULT '',
        path VARCHAR(1000) NOT NULL DEFAULT '',
        entity_type VARCHAR(60) NOT NULL DEFAULT '',
        entity_id VARCHAR(200) NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        status INTEGER NOT NULL DEFAULT 200,
        ip_address VARCHAR(100),
        detail_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_analyses_repo ON analyses (repo_id)",
    "CREATE INDEX IF NOT EXISTS ix_events_analysis ON analysis_events (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_containers_analysis ON analysis_containers (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_files_analysis ON analysis_files (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_contents_analysis ON analysis_file_contents (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_symbols_analysis ON analysis_symbols (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_edges_analysis ON analysis_edges (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_deps_analysis ON analysis_dependencies (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_concerns_analysis ON analysis_concerns (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_chunks_analysis ON analysis_chunks (analysis_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_session ON chat_messages (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_sessions_user ON chat_sessions (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_creds_user ON git_credentials (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log (ts)",
]

SCHEMA_V2 = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS ldap_domain VARCHAR(100) DEFAULT ''",
]

# v3: layer catalogue (per-layer drill-down items and cross-layer links) + LLM budget for catalogue explanations
SCHEMA_V3 = [
    """
    CREATE TABLE IF NOT EXISTS analysis_catalog (
        item_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        layer VARCHAR(20) NOT NULL,
        kind VARCHAR(40) NOT NULL,
        item_key VARCHAR(1000) NOT NULL,
        name VARCHAR(1000) NOT NULL,
        path VARCHAR(1000) NOT NULL DEFAULT '',
        line_start INTEGER NOT NULL DEFAULT 0,
        line_end INTEGER NOT NULL DEFAULT 0,
        language VARCHAR(40) NOT NULL DEFAULT '',
        framework VARCHAR(80) NOT NULL DEFAULT '',
        signature TEXT NOT NULL DEFAULT '',
        details_json TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '',
        summary_source VARCHAR(20) NOT NULL DEFAULT '',
        content_hash VARCHAR(64) NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_catalog_links (
        link_id VARCHAR(64) PRIMARY KEY,
        analysis_id VARCHAR(64) NOT NULL,
        source_key VARCHAR(1000) NOT NULL,
        target_key VARCHAR(1000) NOT NULL,
        kind VARCHAR(40) NOT NULL,
        detail VARCHAR(500) NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_catalog_analysis ON analysis_catalog (analysis_id, layer)",
    "CREATE INDEX IF NOT EXISTS ix_catalog_links_analysis ON analysis_catalog_links (analysis_id)",
    "ALTER TABLE app_settings ADD COLUMN IF NOT EXISTS llm_catalog_limit INTEGER DEFAULT 60",
]

# v4: chat session visibility (private by default, optional public sharing)
SCHEMA_V4 = [
    "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE",
]

MIGRATIONS: list[list[str]] = [SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4]

TABLES = (
    "roles",
    "users",
    "app_settings",
    "llm_providers",
    "git_credentials",
    "repositories",
    "analyses",
    "analysis_events",
    "analysis_containers",
    "analysis_files",
    "analysis_file_contents",
    "analysis_symbols",
    "analysis_edges",
    "analysis_dependencies",
    "analysis_concerns",
    "analysis_chunks",
    "analysis_catalog",
    "analysis_catalog_links",
    "comparisons",
    "chat_sessions",
    "chat_messages",
    "audit_log",
)

# Child tables of one analysis, deleted together with it.
ANALYSIS_TABLES = (
    "analysis_events",
    "analysis_containers",
    "analysis_files",
    "analysis_file_contents",
    "analysis_symbols",
    "analysis_edges",
    "analysis_dependencies",
    "analysis_concerns",
    "analysis_chunks",
    "analysis_catalog",
    "analysis_catalog_links",
)


def _migrate() -> None:
    conn = _conn

    def run(sql: str) -> None:
        conn.execute(sql)

    run(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL)"
    )
    cursor = conn.execute("SELECT version FROM schema_migrations")
    applied = {row[0] for row in cursor.fetchall()}
    for version, statements in enumerate(MIGRATIONS, start=1):
        if version in applied:
            continue
        logger.info("Applying schema version %s on %s", version, _engine)
        for statement in statements:
            run(statement)
        if _engine == "postgres":
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (%s, %s)", [version, now()])
        else:
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", [version, now()])


def schema_version() -> int:
    return int(scalar("SELECT max(version) AS v FROM schema_migrations") or 0)


def describe() -> dict:
    if engine() == "postgres":
        url = config.database_url()
        safe = url
        if "@" in url and "://" in url:
            scheme, rest = url.split("://", 1)
            safe = f"{scheme}://***@{rest.split('@', 1)[1]}"
        location = safe
    else:
        location = str(config.db_path())
    return {"engine": engine(), "location": location, "schema_version": schema_version()}
