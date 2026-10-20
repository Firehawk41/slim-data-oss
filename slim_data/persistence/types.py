"""Column types that behave the same on SQLite and Postgres.

* `ExactDecimal`: Postgres `NUMERIC` keeps every digit (12.40 stays 12.40); SQLite would store a float,
  so there it is text. Floats are refused on the way in, as everywhere else.
* `UtcDateTime`: timezone-aware UTC both ways (SQLite stores text without an offset).
* `db_now()`: the database clock (D-21), per statement: `clock_timestamp()` on Postgres (not `now()`,
  which is frozen for the whole transaction, so two events in one transaction would tie), and
  millisecond `strftime` on SQLite (CURRENT_TIMESTAMP has only whole seconds).
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeDecorator


class ExactDecimal(TypeDecorator):
    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(60))
        return dialect.type_descriptor(Numeric(asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError(f"expected a Decimal, got {type(value).__name__}")
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class UtcDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            raise ValueError("datetimes must be timezone-aware")
        return None if value is None else value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class db_now(FunctionElement):  # noqa: N801 (reads like the SQL function it compiles to)
    type = UtcDateTime()
    inherit_cache = True


@compiles(db_now, "postgresql")
def _pg_now(element, compiler, **kw):
    return "clock_timestamp()"


@compiles(db_now, "sqlite")
def _sqlite_now(element, compiler, **kw):
    return "(strftime('%Y-%m-%d %H:%M:%f', 'now'))"
