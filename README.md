# slim-data

[![CI](https://github.com/Firehawk41/slim-data-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/Firehawk41/slim-data-oss/actions/workflows/ci.yml)

The **analytical results** module of SLIM, a portfolio LIMS for an ISO 17025 analytical chemistry lab.

```
instrument CSV ──► import (all or nothing) ──► rules engine decides each result ──► people review,
                                                accept, rerun, record exceptions ──► release ──► report issue
```

Raw readings go in. A **versioned rules engine** decides each result and records why. People review,
accept or release it under a four-eyes policy. The record keeps **exactly what each customer report
showed**. Nothing is ever updated or deleted, so any past state can be read back, and `verify` proves
every stored result is what the rules made.

It is a **library**: the host app (slim-lims, built from
[slim-lims-oss](https://github.com/Firehawk41/slim-lims-oss)) calls it, owns the transaction and provides the
screens. Demo data only: it refuses to connect to anything but a demo database.

## What it guarantees

| Guarantee | How |
|---|---|
| **Every result is explainable** | Each version stores a trace: every step's inputs, output, flags and the rule that fired. |
| **Every result is reproducible** | Steps are `name@version` and never edited once released; a published ruleset is content-hashed. `verify` re-runs every stored version on the readings as they stood and must get the same answer. |
| **Nothing is lost** | Append-only tables (database triggers refuse UPDATE / DELETE, on SQLite and Postgres); a change is a new version or event. Every read takes `as_of`. |
| **A report can be rebuilt** | A report issue records which result versions it showed and the file's sha256; `report_view(as_of=...)` rebuilds it. A corrected result amends the issue, it never rewrites it. |
| **Who did what, and on what authority** | Four-eyes review, supervisor-only acceptances and exceptions, reason categories from the policy, and a documented solo sign-off rule for a chemist working alone. Demo personas are recorded as *asserted*, never as logged in. |
| **Tampering is detected** | The host's hash-chained database change log (slim-domain's `audit_log`) is checked by `verify` too. |

## How the engine decides

A prep batch is evaluated in a fixed order of steps (each one data-driven by the ruleset):

`cup_shape` → `linear_range` → `agreement` (do the two cups agree?) → `cup_choice` (if not, which one
to trust: spike witness, then the element's known direction of failure, then a whole-cup scale-fault
check) → `recovery` / `recovery_pooling` → `reported_value` → `uncertainty` → `method_blank` →
`spec_limits`.

Steps raise flags; the ruleset gives each flag a severity; **the verdict is the worst flag**:
REPORTABLE, NEEDS_SUPERVISOR or RERUN. Values are `Decimal` end to end (floats are refused),
compared unrounded and rounded once at the end. The same steps, configured differently, cover other
techniques: the demo has ICP-MS metals ([`demo_metals.json`](slim_data/rulesets/demo_metals.json)),
anions by ion chromatography ([`demo_anions.json`](slim_data/rulesets/demo_anions.json)), and an acid
assay by duplicate titration that passes only when the two titrations' RSD is under 1 %
([`demo_assay.json`](slim_data/rulesets/demo_assay.json)). All values are invented, never a real lab's.

## Try it

```bash
pip install -e ".[dev]"
```

```python
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from slim_data.demo.scenarios import seed
from slim_data.persistence import migrate
from slim_data.persistence.db import make_engine
from slim_data.service.verify import verify
from slim_data.service.views import report_view

engine = make_engine("sqlite:///demo.db")
with engine.begin() as connection:
    migrate.upgrade(connection)

with Session(engine) as session:
    trs, _ = seed(session, datetime.now(UTC))  # the nine demo scenarios, through the service
    session.commit()

    for line in report_view(session, trs["cups_disagree"]).lines:
        print(line.sample, line.analyte.code, line.text, line.shown_flags)
    print(verify(session))
```

```
TR00102-01 Zn 10.000 ('ABOVE_SPEC',)
versions=20 mismatches=() chain_checked=False broken_entries=()
```

The demo scenarios, across 10 Elements, 36 Elements, anions and an acid assay: a clean pass, cups that
disagree, a scale fault, a spike pooled from other samples, a result below the MDL, a rerun released
with an exception, an accepted NEEDS_SUPERVISOR result, an amended report, and a line-by-line import. On SQLite there is no change log, so `verify` reports the
chain as *not checked*. In slim-lims-oss (Postgres) the same scenarios are seeded onto real requests, and
`python -m slim_lab_portal.cli verify` checks both.

## Using it from a host

The host supplies two ports, a `ReferenceLookup` (is this a real sample? what are its spec limits?) and
a `StaffDirectory`, and calls the service. slim-data flushes, **the host commits**, so a result change
and the host's own changes (e.g. the request's status) land together or not at all.

```
preview_import / import_run   evaluate_batch   review_batch   accept / request_rerun / record_exception
reject   release   start_correction   record_issue
reads (all take as_of):  report_view  batch_view  batches  trace  exceptions  issues_for_tr
verify(session, chain_check=None)
```

slim-data shares the database with the rest of SLIM, so it keeps its own Alembic version table
(`alembic_version_slim_data`) and only migrates its own tables:

```python
from slim_data.persistence import migrate
with engine.begin() as connection:
    migrate.upgrade(connection)
```

## Layout

```
slim_data/
├── domain/       pure (stdlib + pydantic): values, observations, results, policies, workflow rules, ports
├── engine/       pure: step protocol, versioned step registry, ruleset model, runner, replay; steps/
├── pipeline/     readers (plugins): generic_csv/1
├── persistence/  append-only tables + triggers, repositories, as_of, migrations, OSS guard
├── service/      what the host calls: imports, workflow, views, verify
├── demo/         the named demo scenarios
└── adapters/     ReferenceLookup over slim-domain
```

`domain/` and `engine/` import nothing but the standard library and pydantic, which keeps the engine
pure so a replay is exact; `tests/test_architecture.py` enforces it.

## Tests

```bash
pytest                                    # SQLite
TEST_DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost/slim_data_test pytest   # Postgres
ruff check . && ruff format --check .
```

CI runs the suite on {SQLite, Postgres 16} × Python {3.11, 3.12}, with a 95% branch-coverage gate on the
domain and the rules engine. The engine steps have hypothesis property tests (e.g. agreement is
symmetric in the cups; the scale-fault check never fires on scattered contamination); readers have
golden CSV fixtures; every released step version is pinned in `tests/engine/step_manifest.txt`.

## Design

- [docs/DESIGN.md](docs/DESIGN.md): what gets built (signed off).
- [docs/DECISIONS.md](docs/DECISIONS.md): every decision, D-01…D-77, with the options weighed and
  why the others were rejected.

Parked for later: instrument-native readers, full measurement-uncertainty budgets, unit conversion,
a result-entry UI, the report engine rendering the actual report file (until then the issue's hash is
of a canonical JSON form of the report, D-73).

## License

MIT, see [LICENSE](LICENSE).
