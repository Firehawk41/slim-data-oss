# slim-data — analytical results module: design

Status: **signed off** by Jamie (2026-10-19). Rewritten from `DECISIONS.md` (D-01…D-61), which stays the
authoritative record of *why*; this document says *what*. Building starts with milestone M0.

## 1. Purpose and boundaries

slim-data owns **analytical results**: the raw readings from an instrument, the rules that decide
what each result is, the people who review and release it, and the record of exactly what each
customer report showed. It is **auditable** (nothing is updated or deleted; any past state can be
read back) and **upgradable** (versioned rulesets and steps, migrations).

```
host (slim-lims): submit → check-in ──► slim-data: prep batch → import readings → evaluate
                                         → review → accept / exception → release ──► report engine
                                         → record report issue ──► invoicing bills what was reported
```

| In scope (v1) | Parked (§13) |
|---|---|
| Prep batches, raw readings (cups, spikes), generic CSV import | Instrument-native readers (ICP-MS formats) |
| Rules engine: agreement, cup choice, recovery, pooling, reported value, ±, verdict | Full measurement-uncertainty budgets |
| Review, acceptance, exceptions, release, corrections, amended reports | Unit conversion to customer units |
| `report_issues`: what each report showed | Result-entry UI |
| Time travel (`as_of`), replay/verify, audit integration | Recovery correction (option only) |

It is **not** a general data-access layer (D-01), and it ships **no UI** (D-42).

## 2. Place in the ecosystem

Sandbox portfolio ecosystem only; it never runs in a lab, and has no legacy Excel/VBA paths (D-44).

| Module | Role | Relationship to slim-data |
|---|---|---|
| **slim-lims** (host; built from LabPortal, D-40/D-41) | Composition root and UX: login, screens, JSON API, transactions | Calls slim-data's service; owns the transaction (D-23); hosts the review/release screens (D-42) |
| **slim-domain** | Shared domain: customers, chemicals, analyses, elements, specs, **TR v2** (D-45), audit log | slim-data reads reference data and TRs through a port; reuses `audit.py` (D-03) |
| **slim-report-engine** | Pure renderer: data in, Excel file out, no DB (D-46/D-47) | Reads slim-data's report read model; the host records the issue in slim-data |
| **invoicing** | Library on slim-domain's tables; quotes + sales orders (D-43) | Bills from the TR + `report_issues` + quotes: only what was reported |

Rules that keep the modules independent:

- slim-data is a **library** (D-02). It never imports the host or another module's internals; it
  sees the outside world only through the ports in §5.
- **One Postgres** (SQLite for tests and the demo). slim-data owns only its own tables, migrated by
  its own Alembic environment (`alembic_version_slim_data`). **No foreign keys** into other modules'
  tables (D-03).
- **The caller owns the transaction** (D-23): slim-data flushes, never commits, and returns facts
  (e.g. `ResultsReleased`) that the host turns into TR status changes in the same transaction.
- **OSS only:** `make_engine` refuses non-demo databases, copying the portal's `oss_guard`.

## 3. Package layout and dependency direction

```
slim_data/
  domain/       pydantic + stdlib only: value objects, entities, enums, workflow rules, policies
  engine/       pydantic + stdlib only: step protocol, versioned step registry, ruleset model, steps, trace, replay
  pipeline/     readers (plugins) → import plan; pure apart from reading the file
  persistence/  SQLAlchemy rows, repositories, Alembic, append-only triggers
  service/      ResultService facade: the only API the host calls
  adapters/     ReferenceLookup over slim-domain (optional extra)
```

`domain/` and `engine/` import nothing outside stdlib + pydantic, so they could be lifted into
slim-domain unchanged; an architecture test enforces it (D-26).

## 4. Identity

- **Sample:** `SampleRef(tr_number, position)`, written `TR00042-03`. A natural key that a technician
  can read and that doesn't depend on another module's surrogate IDs. v2 TRs are the only TRs (D-44).
- **Analyte:** `AnalyteRef(analysis_id, code)` (D-48). `analysis_id` is slim-domain's; `code` is an
  element symbol (`Fe`) or an ion code (`Cl-`). The same identity is used by every module.
- **Actor:** `Actor(id, display, roles, identity_source)` (D-18). `identity_source` is AUTHENTICATED
  (real login) or ASSERTED (demo persona) and is stored on every event (D-50). Four-eyes compares
  `id`.

## 5. Ports (what slim-data needs from outside)

```python
class ReferenceLookup(Protocol):
    def analysis_id(self, code: str) -> int | None: ...                        # metals_icpms -> 1 (D-66)
    def sample_exists(self, ref: SampleRef) -> bool: ...
    def sample_requests(self, ref: SampleRef, analysis_id: int) -> bool: ...   # was it ordered?
    def analyte_valid(self, analyte: AnalyteRef) -> bool: ...
    def limits_for(self, ref: SampleRef, analyte: AnalyteRef) -> Limits: ...    # spec limits, snapshotted

class StaffDirectory(Protocol):                                                 # the host's staff (D-66)
    def find(self, staff_id: str) -> Actor | None: ...

class Clock(Protocol): ...          # injected; tests and the demo seed control time
```

`adapters/slim.py` implements `ReferenceLookup` over slim-domain (including the TR v2 repository).
Tests use a fake. Rulesets and policy tables are data, injected or published, never `if demo:`
branches (D-05).

## 6. Domain model

### 6.1 What is observed

- **PrepBatch** (D-07, D-24): samples of one matrix prepped together; the unit of evaluation.
  Snapshots its prep method (D-15) and records the spike amount added per sample and analyte.
  Registered by its own call; for v1 the CSV import may register it in the same transaction.
- **Run**: one instrument run = one imported file (D-25), with the file's sha256 and the reader name
  and version.
- **Reading** (D-06): one row per measurement: sample, analyte, **role** (CUP, SPIKE, BLANK, …),
  replicate, value (Decimal, keeps its digits), unit, MDL, dilution. No fixed cup columns. A BLANK
  reading has no sample: it belongs to its prep batch (D-64).
- **ReadingExclusion**: an analyst marks a reading bad, with a reason. An event, never an edit.

### 6.2 What is decided

- **ResultVersion**: one immutable version of one result (sample × analyte). Holds the raw and
  reported values, ±, qualifier, the **flags**, the **verdict**, the full **decision trace** (D-16),
  the ruleset version used (D-12), the snapshotted limits (D-04), and a reason when version > 1.
- **Verdict** (D-08): REPORTABLE / NEEDS_SUPERVISOR / RERUN, from the engine.
- **Status** (D-08): DRAFT → REVIEWED → RELEASED (or REJECTED), from people. Lives in events, not a
  column.
- **ResultEvent**: review, acceptance, rerun request, exception, release, correction; each with actor,
  time and note. Latest event = current status.
- **Exception** (D-09): reason category + required free text, by someone who isn't the analyst; the
  result is flagged `released_with_exception` permanently; queryable lab-wide.
- **ReportIssue** (D-22/D-47): which result versions went onto a report, the file's hash, and the
  link to the issue it amends (D-20).

## 7. The rules engine

### 7.1 Rulesets and steps

- A ruleset is **data**: which steps run, in what order, with which parameters and tables (D-13).
- Authored as a file, **published** into the DB as a numbered, content-hashed, immutable version
  (D-14). Chosen by (analysis, prep method), else the analysis default (D-15).
- Steps are Python, **versioned and never removed** (`spike_recovery@1`, `@2`); a ruleset pins exact
  step versions (D-17).
- **Cliff check** on publish (D-30): if absolute limit ≠ RPD × switch point, publishing warns and
  requires a stored reason.
- Demo values are invented but plausible, never a real lab's (D-05).

### 7.2 Evaluation order (per prep batch)

| # | Step | Rule | Decisions |
|---|---|---|---|
| 1 | Linear range | Any cup above the analysis's verified linear range (which may exceed the top calibration standard) → RERUN ("dilute and rerun"); above the top standard but within the linear range → INFO | D-59, D-61 |
| 2 | Cup agreement | Three zones: both < MDL agree; smaller cup < switch → \|c1−c2\| ≤ absolute limit; else RPD ≤ limit. Demo: switch 5× MDL, RPD 20%, absolute 1× MDL | D-28, D-29 |
| 3 | Scale-fault check (per sample) | ≥ 5 elements with both cups above the switch; ≥ 75% of cup ratios within ±5% of the median; median fails RPD → one cup chosen for the cluster by ≥ 2 agreeing spike witnesses, else RERUN. Outliers stay per element | D-51 |
| 4 | Cup choice (per element, disagreeing cups only) | Evidence ladder, first conclusive rung wins: analyst exclusion → spike witness → direction prior → no decision | D-31, D-32, D-35, D-52 |
| 5 | Spike agreement | Same three-zone test as cups | D-34 |
| 6 | Recovery | (spiked − chosen value) / added; sample > 4× spike → NOT_EVALUABLE | D-36 |
| 7 | Batch pooling | Median of evaluable recoveries per element (leave-one-out when the sample's spike chose its cup); all NOT_EVALUABLE → NEEDS_SUPERVISOR; median outside 75–125% → RERUN | D-37, D-53 |
| 8 | Reported value | Mean of agreeing cups, else chosen cup, × dilution; "< effective MDL"; optional QL (3.33× MDL) → "estimated"; round once to per-analyte decimal places (demo 3), half-even | D-54 |
| 9 | ± | Half-delta × dilution when cups agree, same decimal places; else N/A | D-55 |
| 10 | Method blank | Batch blank > MDL: samples ≥ 10× blank → WARN, below → RERUN; never subtracted | D-58 |
| 11 | Spec limits | Exceedance → INFO flag, shown on the report; never affects the verdict | D-60 |
| 12 | Verdict | Every step emits flags (INFO / WARN / NEEDS_SUPERVISOR / RERUN, severity table in the ruleset); verdict = worst flag | D-56 |

Details worth defending:

- **Spike witness noise guard** (D-35): w = spiked − added decides only if (distance to far cup −
  distance to near cup) > spiked × spike precision (demo 5%).
- **Direction prior** (D-52): table by element with optional (element, prep) overrides:
  CONTAMINATION_PRONE → lower cup, LOSS_PRONE → higher, NEUTRAL → no decision (no guessing).
- **Additive vs multiplicative faults** (D-51): contamination and loss are element-level and stay on
  the per-element ladder; only a whole-cup scaling (weighing, dilution) is decided per cup.
- **No recovery correction** (D-38): recovery is reported beside the result.
- **All comparisons use unrounded values**; raw and reported are both stored (D-54).

### 7.3 Trace and replay

Each result version stores a JSON trace: every step's inputs, output, flags, and the rung or rule that
fired (D-16). **Replay** re-runs stored readings through the stored ruleset version and must reproduce
the stored trace exactly; `verify` runs it across the database and is also a test (D-17).

## 8. Workflow

### 8.1 Who may do what (D-57, D-10, D-50)

| Verdict | Normal path | Who clears | Recorded as |
|---|---|---|---|
| REPORTABLE | analyst → reviewer (≠ analyst) reviews the batch → release | reviewer | normal events; WARN flags shown at review |
| NEEDS_SUPERVISOR | supervisor decides | supervisor: **accept** (reason category) or **send to rerun** | acceptance event; standard qualifier on the report (D-11); *not* an exception |
| RERUN | new readings → new version → re-evaluated | releasing without a rerun: supervisor only | **exception** (D-09), `released_with_exception`, qualifier on the report |

- Roles: analyst < reviewer < supervisor; a policy table maps (severity, action) → minimum role,
  tiered by default, flat possible.
- A second policy says which identity sources each action accepts (D-50): the demo lets ASSERTED
  personas release; a production config would require AUTHENTICATED.
- Nobody reviews or clears a result on readings they entered (four-eyes on `Actor.id`), except a
  **solo** review and release by a chemist working alone, after the policy's delay, labelled (D-68).

*Supersedes in part:* D-09's "NEEDS_SUPERVISOR is overridden by an exception" (now an acceptance) and
D-10's example tiers ("RERUN → quality"; now supervisor, matching the demo roles).

### 8.2 Granularity

- **Review per prep batch**, with per-result exclusions (D-19).
- **Release per sample + analysis**, so partial reports are natural (D-19): the ready elements go;
  the rest wait. Released results stay as reported while the batch is still running (D-67).
- **Re-evaluation is explicit** and creates new versions; a ruleset change never touches existing
  results (D-12).
- **Corrections after reporting** go through the full cycle → new draft version → review → release →
  new report issue marked "Amended", linked to the one it replaces (D-20).

### 8.3 Facts returned to the host

`ResultsReleased(tr_number, sample, analysis)`, `ReportIssued(...)`, etc. The host maps them to TR
status changes (in progress → partial report → complete report) in the same transaction (D-23).
Invoicing is triggered by staff on a complete-report TR (D-43).

## 9. Data in

- Readers are plugins behind a `ResultReader` protocol; v1 ships `generic_csv/1` (D-25).
- `preview_import` returns an **ImportPlan** (runs, readings, row errors) without writing.
- **All or nothing**: any row error → nothing is written.
- **Idempotent by file hash**: the same sha256 again is refused, with a pointer to the first import.

`generic_csv/1` columns (one file = one run; run columns identical on every row):

| Column | Example | Notes |
|---|---|---|
| `lab_sample_id` | `TR00042-03` | `SampleRef`; `BLANK` for a method blank (D-64) |
| `analysis_code`, `analyte` | `metals_icpms`, `Fe` | → `AnalyteRef` |
| `role`, `replicate` | `CUP`, `2` | D-06 |
| `value`, `unit` | `12.40`, `ppb` | text → Decimal |
| `mdl`, `dilution_factor` | `0.05`, `10` | dilution optional, default 1 |
| `run_code`, `instrument`, `analyst`, `analyzed_at` | | run columns; ISO 8601 with offset; `analyst` is a staff ID, the uploader is recorded separately (D-66) |
| `prep_batch`, `prep_method`, `spike_added` | | `prep_batch` required; the others register the batch when no prep call was made, or add to it (D-24, D-66) |
| `rejected`, `note` | `yes`, `drift check failed` | optional: rejected in the run, imported and excluded; a rerun is a newer attempt (D-66) |

## 10. Persistence, time travel, audit

### 10.1 Tables (all append-only)

| Table | Holds |
|---|---|
| `rulesets` | published rulesets: number, content hash, body (JSON), cliff reason, published by/at |
| `policies` | published policy tables (roles per action, identity sources per action), versioned like rulesets |
| `prep_batches`, `prep_batch_samples`, `prep_batch_spikes` | batch, prep method snapshot, samples, spike amounts (D-65) |
| `runs` | run metadata, source file sha256 (unique), reader name + version |
| `readings` | one row per reading, with its prep batch (a blank's only owner, D-64) |
| `reading_exclusions` | analyst exclusions (events) |
| `results` | identity only: tr_number, position, analysis_id, analyte code (unique) |
| `result_versions` | values, flags, verdict, trace, ruleset id, limits snapshot; PK (result_id, version) doubles as optimistic concurrency |
| `result_events` | status events: review, acceptance, rerun request, exception, release, correction |
| `report_issues`, `report_issue_lines` | which result versions each report showed, file hash, amended-from link |

Portable schema: enums as ints, JSON via SQLAlchemy `JSON`, no ARRAY columns, Decimal as `Numeric`.

### 10.2 Time travel (D-21)

Every read takes an optional `as_of`. Timestamps come from the **database clock**, with the row ID as
the tiebreaker. Only slim-data's own tables can be read as of a past time; other modules' data enters
through snapshots (limits on result versions, D-04).

### 10.3 Three audit layers, each with its own job

1. **Domain history**: the append-only tables above, which the app shows and the report uses.
2. **Database change log**: slim-domain's hash-chained `audit_log` (Postgres); slim-data calls
   `set_change_context(actor, reason)` on every write transaction.
3. **Provenance**: file sha256, reader version, ruleset version and step versions on every result, so
   a reported number traces back to its file and the code that produced it.

Append-only is enforced twice: the repositories have no update path, and on Postgres a trigger refuses
UPDATE/DELETE on slim-data's tables.

## 11. Service API (what the host calls)

```
publish_ruleset(file, actor, cliff_reason=None)      publish_policy(file, actor)
register_prep_batch(batch, actor)
preview_import(file) -> ImportPlan                    import_run(file, actor) -> ImportReceipt
exclude_reading(reading, reason, actor)               re_evaluate(batch, reason, actor)
review_batch(batch, exclusions, actor)
accept(result, reason_category, note, actor)          request_rerun(result, note, actor)
record_exception(result, category, text, actor)
release(sample, analysis, actor) -> ResultsReleased   correct(result, reason, actor)
record_issue(tr_number, versions, file_sha256, amends=None, actor) -> ReportIssued

reads (all take as_of):  report_view(tr_number)  results_for_tr(tr_number)  batch_view(batch)
                         trace(result, version)  exceptions(filter)  issues_for_tr(tr_number)
verify() -> VerifyReport                              # replay + audit chain
```

`report_view` is the read model the report engine renders: released versions only, with qualifiers
and ±. Invoicing reads `issues_for_tr`.

## 12. Demo, tests, upgrades

### 12.1 The public demo (D-27, D-50)

- Real login stays; demo personas (customer, analyst, reviewer, supervisor, billing) are a second,
  config-enabled sign-in method, recorded as ASSERTED.
- The seed pre-runs the named scenarios, so a first visitor already sees history: clean pass, cups
  disagree, scale fault, spike pooled from another sample, below MDL, rerun + exception, accepted
  NEEDS_SUPERVISOR, amended report, billed TR.
- Nightly reset with a banner; seed dates relative to today; the audit chain restarts each reset and
  `verify` checks it from its start.
- Personas can't create users, change policies or publish rulesets; uploads are size-limited.

### 12.2 Tests (D-26)

| Layer | How |
|---|---|
| Domain, workflow, policies | table-driven pytest |
| Engine steps | pytest + **hypothesis** property tests on every step (e.g. no jump at the switch when the numbers line up; agreement is symmetric in the cups; scale-fault check never fires on scattered contamination) |
| Readers | golden CSV fixtures (good, each error kind, BOM, CRLF) |
| Replay | stored trace == recomputed trace for every scenario |
| Persistence | SQLite per test; Postgres via `TEST_DATABASE_URL`; append-only triggers; `as_of` |
| Migrations | upgrade → downgrade → upgrade; models == migrations |
| Architecture | `domain`/`engine` import only stdlib + pydantic; no `create_engine` outside `db.py` |
| End to end | the demo scenarios, through the service API |

CI: {SQLite, Postgres 16} × Python {3.11, 3.12}; **95% branch coverage** gate on `domain` + `engine`.

### 12.3 Upgradability

Semantic versioning (public API = `service` + domain objects); Alembic migrations with tests; steps
and readers versioned, never removed; recalculation only ever by explicit re-evaluation.

## 13. Parked (not v1)

Full measurement-uncertainty budgets · unit conversion to the customer's units · instrument-native
readers · result-entry UI · optional recovery correction (would have to be stated on the report) · a
prep-book module (the register call is ready for it).

## 14. Gaps closed during the rewrite

Method blanks (D-58), above the linear range (D-59) and spec-limit exceedances (D-60) were not
covered by D-01…D-57; they are now steps 1, 10 and 11 of §7.2.
