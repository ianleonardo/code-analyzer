"""Copy every table between DuckDB and PostgreSQL (both directions). The schema is identical on both engines.

    cd backend
    python -m app.tools.migrate_db --from duckdb --to postgres \
        --duckdb-path ../data/codeanalyzer.duckdb --database-url postgresql://user:pass@host:5432/codeanalyzer

The target's tables are created if needed and their rows are replaced. Stop the application first: DuckDB allows one
writing process at a time.
"""

from __future__ import annotations

import argparse
import os
import sys


def _source_rows(engine: str, duckdb_path: str, url: str, table: str) -> tuple[list[str], list[tuple]]:
    if engine == "duckdb":
        import duckdb

        conn = duckdb.connect(duckdb_path, read_only=True)
        try:
            cursor = conn.execute(f"SELECT * FROM {table}")
            return [c[0] for c in cursor.description], cursor.fetchall()
        finally:
            conn.close()
    import psycopg

    with psycopg.connect(url) as conn:
        cursor = conn.execute(f"SELECT * FROM {table}")
        return [c.name for c in cursor.description], cursor.fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", choices=("duckdb", "postgres"), required=True)
    parser.add_argument("--to", dest="target", choices=("duckdb", "postgres"), required=True)
    parser.add_argument("--duckdb-path", default=os.environ.get("DUCKDB_PATH", ""))
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    args = parser.parse_args(argv)
    if args.source == args.target:
        parser.error("--from and --to must differ")
    if not args.duckdb_path or not args.database_url:
        parser.error("both --duckdb-path and --database-url are needed")

    os.environ["DB_ENGINE"] = args.target
    os.environ["DUCKDB_PATH"] = args.duckdb_path
    os.environ["DATABASE_URL"] = args.database_url
    from app import db

    db.reset()
    db.init()  # creates the schema (and seed rows, which are replaced below)
    total = 0
    with db.transaction():
        for table in db.TABLES:
            columns, rows = _source_rows(args.source, args.duckdb_path, args.database_url, table)
            target_columns = {row["column_name"] for row in db.fetchall(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]
            )}
            keep = [i for i, name in enumerate(columns) if name in target_columns]
            db.exec_sql(f"DELETE FROM {table}")
            db.insert_many(table, [columns[i] for i in keep], [tuple(row[i] for i in keep) for row in rows])
            print(f"{table:28} {len(rows):>9} rows")
            total += len(rows)
    db.reset()
    print(f"Copied {total} rows from {args.source} to {args.target}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
