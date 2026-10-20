# slim-data

[![CI](https://github.com/Firehawk41/slim-data-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/Firehawk41/slim-data-oss/actions/workflows/ci.yml)

The **analytical results** module of SLIM, a portfolio LIMS for an ISO 17025 analytical chemistry lab.
Raw instrument readings go in; a versioned rules engine decides each result; people review, accept or
release it; and the record keeps exactly what each customer report showed. Nothing is ever updated or
deleted, so any past state can be read back.

It is a library: the SLIM host app calls it, owns the transaction and provides the screens.
Demo data only: it refuses to connect to anything but a demo database.

- Design: [docs/DESIGN.md](docs/DESIGN.md)
- Every decision, with the options weighed: [docs/DECISIONS.md](docs/DECISIONS.md)

**Status:** design signed off; building. Milestone M0 (skeleton) is in place.

## Run the tests

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                                    # SQLite
TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost/slim_data_test pytest   # Postgres
ruff check . && ruff format --check .
```

CI runs the suite on {SQLite, Postgres 16} × Python {3.11, 3.12}, with a 95% branch-coverage gate on
the domain and the rules engine.

## Migrations

slim-data shares one database with the rest of SLIM, so it keeps its own Alembic version table
(`alembic_version_slim_data`) and only migrates its own tables:

```python
from slim_data.persistence import migrate
with engine.begin() as connection:
    migrate.upgrade(connection)
```

## License

MIT, see [LICENSE](LICENSE).
