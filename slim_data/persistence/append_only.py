"""Append-only, enforced by the database too (D-04, DESIGN.md §10.3).

The repositories have no update or delete path; these triggers make the database refuse UPDATE,
DELETE (and on Postgres TRUNCATE) on every slim-data table, whoever writes: the app, a script or a
hand-typed statement. As with slim-domain's audit log, they stop accidents, not a table owner who
drops a trigger; slim-domain's hash-chained `audit_log` makes that detectable.

Installed by the migrations, on SQLite as well as Postgres, so the tests prove it on both.
"""

from sqlalchemy import Connection, text

_PG_FUNCTION = """
CREATE OR REPLACE FUNCTION slim_data_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'slim-data is append-only: % on % is not allowed (add a new row instead)',
        TG_OP, TG_TABLE_NAME;
END
$$
"""


def install(connection: Connection, tables: tuple[str, ...]) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(text(_PG_FUNCTION))
        for table in tables:
            connection.execute(
                text(
                    f'CREATE TRIGGER slim_data_append_only BEFORE UPDATE OR DELETE ON "{table}" '
                    "FOR EACH ROW EXECUTE FUNCTION slim_data_append_only()"
                )
            )
            connection.execute(
                text(
                    f'CREATE TRIGGER slim_data_no_truncate BEFORE TRUNCATE ON "{table}" '
                    "FOR EACH STATEMENT EXECUTE FUNCTION slim_data_append_only()"
                )
            )
        return
    for table in tables:
        for operation in ("UPDATE", "DELETE"):
            connection.execute(
                text(
                    f'CREATE TRIGGER "slim_data_no_{operation.lower()}_{table}" BEFORE {operation} '
                    f'ON "{table}" BEGIN SELECT RAISE(ABORT, '
                    f"'slim-data is append-only: {operation} on {table} is not allowed'); END"
                )
            )


def uninstall(connection: Connection) -> None:
    """Only for downgrading the migration that installed them (the tables are dropped with them)."""
    if connection.dialect.name == "postgresql":
        connection.execute(text("DROP FUNCTION IF EXISTS slim_data_append_only() CASCADE"))
