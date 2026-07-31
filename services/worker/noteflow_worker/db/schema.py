from __future__ import annotations

from collections.abc import Iterable


class SchemaMigrationRequired(RuntimeError):
    """Raised when the worker starts against a database Flyway has not migrated."""


def require_tables(conn, tables: Iterable[str]) -> None:
    """Validate schema ownership without mutating it.

    Flyway is the only component allowed to create or alter application tables.
    Keeping this check at the repository boundary produces a useful startup
    error while avoiding service-start races and partially applied schemas.
    """

    required = tuple(dict.fromkeys(tables))
    if not required:
        return
    rows = conn.execute(
        """
        SELECT required.name
        FROM unnest(%s::text[]) AS required(name)
        WHERE to_regclass('public.' || required.name) IS NULL
        ORDER BY required.name
        """,
        (list(required),),
    ).fetchall()
    missing = [str(row["name"]) for row in rows]
    if missing:
        raise SchemaMigrationRequired(
            "Database schema is incomplete; run the API Flyway migrations first. "
            f"Missing tables: {', '.join(missing)}"
        )
