# slim-data — analytical results module: design

Status: **draft for review** (2026-10-12). Nothing is built yet.
Sister modules: slim-domain-oss, slim-lims-oss, slim-report-engine-oss, LabPortal (slim-lab-portal).

## 1. Purpose and boundaries

slim-data owns **analytical results**: what was measured, on which sample, in which run, how the
reported number was calculated, who reviewed and released it, and every correction made since.
It covers the gap between check-in and the report:

```
portal: submit → check-in ──► slim-data: import → review → release ──► report engine: report with results
```

It is **not** a general data-access layer. Each module keeps its own repositories; slim-data is one
more domain alongside them.

| In scope (v1) | Out of scope (v1, see §11) |
|---|---|
| Runs, results, versions, review/release, rejection | Measurement uncertainty |
| Generic CSV import (one documented format) | Instrument-specific formats (ICP-MS, IC, …) |
| Dilution correction, MDL qualification, spec-limit flags | Unit conversion to customer reporting units |
| Correction after release (new version + reason) | Calibration curves, QC sample evaluation |
| Read model for the report engine | UI (slim-lims / portal own screens) |

## 2. How it fits the ecosystem

- **Shipped as a library** (`pip install slim-data`), like slim-domain. It runs in the same process
  as the rest of the ecosystem: the host web app (portal + slim-lims routes) exposes its HTTP
  endpoints, and the report engine imports the read API directly. No separate service.
- **One shared Postgres** with the rest of SLIM, plus SQLite for tests and the demo (same rule as the
  portal: no ARRAY/JSONB-only columns in owned tables, enums stored as ints).
- **Owns only `result_*` tables.** Its own Alembic environment, with
  `version_table="alembic_version_slim_data"`, so its migrations never touch another module's tables.
- **No foreign keys into other modules' tables.** References to customers, analyses, elements and TR
  samples are checked in code through a port (§5), the same rule LabPortal follows.
- **Reuses slim-domain's audit** (`infrastructure/audit.py`). It doesn't add a second audit system (§7).
- **OSS only.** Like the portal, `make_engine` refuses any database URL that isn't a demo database.

### Dependency direction

```
slim_data.domain        pydantic + stdlib only (could be lifted into slim-domain unchanged)
slim_data.pipeline      domain + reader plugins; pure, no I/O except the reader
slim_data.persistence   SQLAlchemy rows, repository, Alembic
slim_data.service       ResultService facade: what other modules call
slim_data.adapters      ReferenceLookup over slim-domain + the portal's TR tables (optional extra)
```

`domain` and `pipeline` never import SQLAlchemy or a sister module. An architecture test enforces this
(§10).

## 3. Identity: how a result points at a sample

A result belongs to a **TR sample**, identified by a natural key: `SampleRef(tr_number, position)`,
written `TR00042-03` on the CSV and in the lab.

- Why not `tr_samples.id`? It's a surrogate key in a table that is moving from the portal to
  slim-domain (spec §9). The natural key survives the move, and a technician can read it.
- Consequence: **only portal-style (v2) TRs can receive results.** v1 xlsx submissions have no TR
  number until slim-domain adopts v2. That's acceptable for a portfolio demo, and is noted as a
  known limitation.

An **analyte** is `AnalyteRef(analysis_id, code)`. `code` is an element symbol (`Fe`) or an ion code
(`Cl-`), because ions aren't rows in SLIM (see report engine `analyte.py`). Validation asks the
`ReferenceLookup` whether `code` is valid for that analysis.

## 4. Domain model (pure pydantic, frozen)

```python
class Qualifier(IntEnum):        # how the reported value should be read
    DETECTED = 1                 # a plain number
    BELOW_MDL = 2                # "< MDL": reported as the MDL, blue font on the report
    ABOVE_RANGE = 3              # "> upper limit": needs re-run with more dilution
    NOT_ANALYZED = 4             # requested but not measured (with reason)

class ResultStatus(IntEnum):
    DRAFT = 1; REVIEWED = 2; RELEASED = 3; REJECTED = 4

class Quantity(Frozen):          # Decimal, never float (see §4.1)
    value: Decimal
    unit: str                    # validated against a small unit registry

class Run(Frozen):               # one instrument run = one imported file
    id: int = 0
    run_code: str                # lab's own run id
    analysis_id: int
    instrument: str
    analyst: str                 # initials
    analyzed_at: AwareDatetime
    source: SourceFile           # file name, sha256, reader name + version

class ResultVersion(Frozen):     # one immutable version of one result
    result_id: int               # stable across versions
    version: int                 # 1, 2, 3 …
    sample: SampleRef
    analyte: AnalyteRef
    run_id: int
    raw: Quantity                # as measured, before correction
    dilution_factor: Decimal     # >= 1
    reported: Quantity           # raw × dilution, rounded for reporting
    mdl: Quantity | None
    qualifier: Qualifier
    flags: frozenset[Flag]       # EXCEEDS_SPEC, EXCEEDS_WARNING, …
    calc_version: str            # which transform produced `reported` (see §9)
    entered_by: Actor
    entered_at: AwareDatetime
    reason: str = ""             # required when version > 1

class ResultEvent(Frozen):       # status changes; latest event = current status
    result_id: int
    version: int                 # the version this event acts on
    from_status: ResultStatus | None
    to_status: ResultStatus
    actor: Actor
    at: AwareDatetime
    note: str = ""

class Result(Frozen):            # aggregate: what callers get back
    result_id: int
    versions: tuple[ResultVersion, ...]
    events: tuple[ResultEvent, ...]
    # properties: current, status, is_amended (corrected after release)
```

`Actor` has the same shape as the portal's `Actor` (type, id, display), so the two can merge later.

### 4.1 Decisions worth defending

- **Decimal, not float.** `0.1 + 0.2 != 0.3` is a bad thing to find on an accredited report.
  Values are stored as `Numeric` and keep the significant figures they arrived with. The report
  engine still uses floats for spec limits; comparisons convert at that one boundary.
- **Raw and reported are both stored.** Corrections and audits need to recompute, not trust.
- **Status lives in events, not a column.** The same pattern as the portal's `status_history`, so
  "who released this and when" is never lost.

### 4.2 Workflow rules (pure functions in `domain/workflow.py`)

| From | To | Rule |
|---|---|---|
| — | DRAFT | import or manual entry |
| DRAFT | REVIEWED | reviewer ≠ `entered_by` of the current version (four-eyes rule) |
| REVIEWED | RELEASED | releaser holds the release role (passed in by the caller) |
| DRAFT, REVIEWED | REJECTED | note required |
| any non-REJECTED | DRAFT (new version) | **correction**: new `ResultVersion` with `reason`; must be reviewed again |

A correction after release makes `is_amended` true. The report engine must label the report as
amended. Released versions are never changed.

## 5. Ports (what slim-data needs from the outside)

```python
class ReferenceLookup(Protocol):
    def sample_exists(self, ref: SampleRef) -> bool: ...
    def sample_requests(self, ref: SampleRef, analysis_id: int) -> bool: ...   # was it ordered?
    def analysis_id_for_code(self, code: str) -> int | None: ...
    def analyte_valid(self, analysis_id: int, code: str) -> bool: ...
    def limits_for(self, ref: SampleRef, analyte: AnalyteRef) -> Limits: ...    # spec / warning
```

- `adapters/slim.py` implements it over slim-domain's services and the portal's `tr_*` /
  `analysis_catalog` tables (read-only Core table definitions, as the portal's `slim_tables.py` does).
- Tests use `FakeReferenceLookup`, so the pipeline is tested without a database.

## 6. Read → transform → write

```
CsvResultReader.read(path)          -> Iterable[RawRow]          (I/O, format-specific)
transform(rows, lookup, clock)      -> ImportPlan                (pure)
    parse      RawRow -> ParsedRow | RowError     (types, units, required fields)
    resolve    lab_sample_id/analysis_code -> refs (via ReferenceLookup)
    calculate  raw × dilution -> reported; qualifier from MDL / range
    evaluate   flags from spec / warning limits
ResultRepository.write(plan, actor) -> ImportReceipt             (one transaction)
```

- **`ImportPlan` is a value**: the runs, the new result versions and the row errors. `preview_import`
  returns it without writing. That's the dry run a technician sees before committing.
- **All or nothing.** If any row has an error, nothing is written. Partial imports of a run are how
  results go missing.
- **Idempotent by file hash.** Re-importing the same file (same sha256) is refused with a pointer to
  the earlier import. Re-running a sample means a new file and a new run.
- **Readers are plugins** behind a `ResultReader` protocol, registered via the
  `slim_data.readers` entry point. v1 ships `generic_csv/1`.

### 6.1 Generic CSV v1

One file = one run. Run columns must have the same value on every row.

| Column | Example | Notes |
|---|---|---|
| `lab_sample_id` | `TR00042-03` | TR number + sample position |
| `analysis_code` | `metals_icpms` | `analysis_catalog.code` |
| `analyte` | `Fe` | element symbol or ion code |
| `value` | `12.40` | text in the file → Decimal; keeps its digits |
| `unit` | `ppb` | unit registry |
| `dilution_factor` | `10` | optional, default 1 |
| `mdl` | `0.05` | optional; same unit as `value` |
| `run_code`, `instrument`, `analyst`, `analyzed_at` | | run columns (ISO 8601 with offset) |

## 7. Audit: three layers, each with a different job

1. **Domain history** (`result_versions`, `result_events`): what the app shows and what the report
   uses. Append-only.
2. **Database change log** (slim-domain `audit.py`): catches everything, including hand-written SQL.
   slim-data sets `set_change_context(actor, reason)` on every write transaction. Postgres only.
3. **Provenance**: source file sha256, reader name/version and `calc_version` on every result, so a
   reported number can be traced back to the file and the code that produced it.

Append-only is enforced twice. The repository has no update path for result values, and on Postgres
a trigger refuses UPDATE/DELETE on `result_versions` and `result_events`.

## 8. Persistence

| Table | Key | Notes |
|---|---|---|
| `result_runs` | `id` | run metadata + source file (unique `source_sha256`) |
| `results` | `id` | identity only: `tr_number`, `sample_position`, `analysis_id`, `analyte_code`; unique on those four |
| `result_versions` | (`result_id`, `version`) | values; the PK doubles as optimistic concurrency (two concurrent corrections → one fails with `StaleResult`) |
| `result_events` | `id` | status changes |

The current state is "highest version + latest event", read through one repository query (a view on
Postgres is a later optimisation). The `unique(result identity)` constraint stops a second import
creating a duplicate result. A re-measure becomes a new version of the same result, with reason
"re-run in RUN-0043".

## 9. Upgradability

- **Semantic versioning** of the package. The public API is `slim_data.service` plus the domain
  objects; anything else is internal.
- **Alembic migrations** with upgrade/downgrade tests and a "models match migrations" test.
- **`calc_version`** on each result. Changing a calculation adds a new version string, and old
  results keep the one they were made with. Recalculating is an explicit correction, never a silent
  side effect of upgrading.
- **Versioned readers** (`generic_csv/1`, later `generic_csv/2`, `icpms_agilent/1`). Old formats keep
  working.
- **Contract tests** against the sister packages (skipped when they aren't installed), so a breaking
  change on either side fails loudly.

## 10. Testing strategy

| Layer | What | How |
|---|---|---|
| Domain | invariants, workflow transitions, four-eyes rule | plain pytest; table-driven |
| Transform | dilution, rounding, qualifier, flags | pytest + **hypothesis** property tests (e.g. reported ≥ raw when dilution ≥ 1; BELOW_MDL ⇔ value < MDL) |
| Readers | the CSV format, bad files | golden fixtures in `tests/fixtures/` (good, each error kind, BOM, CRLF) |
| Repository | round-trip, append-only, concurrency | SQLite per test; Postgres via `TEST_DATABASE_URL` (same switch as the portal) |
| Audit | trigger lock, `verify_chain` intact after imports/corrections | Postgres only (skipped otherwise) |
| Migrations | upgrade → downgrade → upgrade; models == migrations | Alembic + metadata diff |
| Architecture | `domain`/`pipeline` import nothing but stdlib/pydantic; no `create_engine` outside `db.py` | AST scan test |
| Contract | `ReferenceLookup` adapter against the real OSS demo DB; report engine reads released results | skipped without the sister packages |
| End to end | demo: seed → portal TR → CSV import → review → release → report with numbers | one slow test |

CI runs the matrix {SQLite, Postgres 16} × Python {3.11, 3.12}.

## 11. Parked (not v1)

Measurement uncertainty on reports · unit conversion to the customer's reporting unit · QC samples
(blanks, spikes, duplicates) and run acceptance · instrument-specific readers · results for v1 xlsx TRs
· result-entry UI · authenticated actors (today slim-lims trusts the caller's initials, as the rest of
SLIM does).

## 12. Open questions

1. Who may release: any staff, or a named role? SLIM has no roles today. v1 proposes that the caller
   passes `can_release` and slim-lims decides.
2. Should releasing a result change the TR's status (IN_PROGRESS → PARTIAL/COMPLETE_REPORT)? Proposed:
   **no**. slim-data emits a "results released" fact and the portal workflow decides. That keeps the
   modules decoupled.
3. Rounding rule for `reported`: significant figures from the MDL, or a fixed number per analysis?
   Needs a lab-style decision; v1 proposes 3 significant figures, which can be changed through
   `calc_version`.
