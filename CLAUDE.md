# slim-data — Claude context

> **Read first.** The design is **signed off** (2026-10-19); building (Phase B). M0, M1 done; M2 part 1 (engine framework + cup steps) done, part 2 next.
> Jamie is the **designer**; Claude writes all the code once the plan is signed off, then explains the
> finished product until Jamie can defend every decision in an interview. Drive design through
> decisions with a recommendation each time, push back when you disagree, and raise any design change
> as a new decision before building it. Short replies; Jamie is token-constrained.

## What this is

A **portfolio piece** (public, OSS) for getting into software engineering: the **analytical results**
module of SLIM, a homebrew LIMS for an ISO 17025 analytical chemistry lab. It covers the gap between
sample check-in and the report: raw instrument readings → a rules engine decides each result →
people review, override and release → the report records exactly what the customer saw. It is
**auditable** (nothing is ever updated or deleted; you can "time travel" to any past state) and
**upgradable** (versioned rulesets, versioned rule steps, migrations).

## Documents

| File | Status |
|---|---|
| `docs/DECISIONS.md` | **Authoritative.** All decisions D-01…D-62 with the options weighed and why. Append new ones there. |
| `docs/DESIGN.md` | **Signed off** 2026-10-19 (from D-01…D-61). What gets built. |
| `CLAUDE.md` | This file: summary, plan, open items. |

## The ecosystem (sister repos, all Firehawk41 on GitHub)

| Repo | Role | Notes |
|---|---|---|
| `slim-domain-oss` | Shared domain layer: customers, chemicals, analyses, elements, specs, TR parsing | Frozen pydantic objects + repository/cache/service per aggregate. `infrastructure/audit.py` = Postgres hash-chained `audit_log` (triggers on every public table; `set_change_context(conn, by, why)`; `verify_chain`). slim-data **reuses** this audit log. |
| `slim-lims-oss` | FastAPI app (report generation API, resolve pages) | Module-level `app` + global service cache; API-key auth for VBA callers. Needs a factory before joining a single host process. |
| `slim-report-engine-oss` | Builds Excel reports | Currently produces blank report shapes; will read **released results** from slim-data. Has `demo/demo.py` (fictional seed data). Analytes include ions that are not DB rows. |
| `LabPortal` (slim-lab-portal) | FastAPI customer portal: TR submission, check-in, status workflow | Owns `tr_*` tables + `analysis_catalog`; spec in `docs/SLIM_DOMAIN_V2.md`. TR numbers `TR00001`. Pure-pydantic domain, "flush, caller commits" repositories, SQLite+Postgres portable schema, `oss_guard.py` refuses non-demo DBs. Has a slim-lims link-up contract (bearer token, `tests/test_lims_contract.py`). |

Target deployment: **one host app** (D-40: built from LabPortal, named slim-lims; composition root + UX,
calls every module), **one Postgres**, Caddy in front. slim-data is a library the host calls. The OSS
repos may diverge from the lab's private code (D-39).

**OSS only:** demo/fictional data only, never a real lab's databases, shares or services. Copy
LabPortal's `oss_guard` approach.

## Decisions (summary; full text in docs/DECISIONS.md)

**Ground rules**
- D-01 slim-data owns analytical results only, not a general data-access layer.
- D-02 A library in the shared host process; the host app exposes the HTTP endpoints.
- D-03 Shared Postgres (SQLite for tests and the demo). Owns only its own tables, with its own Alembic
  version table (`alembic_version_slim_data`). No FKs into other modules' tables. Reuses slim-domain's
  audit log.
- D-04 Never update, never delete. Snapshot the limits applied and the result versions on each report;
  don't merely reference them.
- D-05 **Own rules, CHEMTEST-shaped.** CHEMTEST is the lab's internal Excel QC workbook. The public
  ruleset copies its *shape* (cups, spikes, batch-pooled recovery, quality score, supervisor override)
  but **never its real thresholds or weights**. Lab-real versus demo is a configuration swap, never a
  code change.

**Area 1: what gets stored**
- D-06 One row per reading, tagged role (CUP/SPIKE/BLANK…) + replicate. No fixed cup_1/cup_2 columns.
- D-07 The **prep batch** (samples of one matrix prepped together) is the unit of evaluation. Batch
  size limit is a ruleset parameter.
- D-08 The engine's **verdict** (REPORTABLE / NEEDS_SUPERVISOR / RERUN) is separate from the human
  workflow **status** (draft → reviewed → released).
- D-09 Escape hatch = a recorded **exception** (ISO 17025 §7.10): who (not the analyst), reason
  category, required free text. The result is flagged `released_with_exception` permanently;
  exceptions can be queried lab-wide.
- D-10 Override authority is a configuration policy table (tiered by default, flat possible). Roles
  come from the caller.
- D-11 Standard report qualifier text per reason category; the free-text reason stays internal.
- D-12 A ruleset change never touches existing results; re-evaluating a draft is explicit and creates
  a new version.

**Area 2: the rules engine**
- D-13 A library of Python steps (procedures) + ruleset data (step order and thresholds).
- D-14 Rulesets are authored as files, then **published into the DB**: numbered, content-hashed,
  immutable.
- D-15 Ruleset chosen by (analysis, prep method), with an analysis default. The batch snapshots its
  prep method.
- D-16 A full step-by-step **decision trace** (JSON) is stored per result.
- D-17 **Steps are versioned and never removed** (`spike_recovery@1`, `@2`); rulesets pin step
  versions. Enables **replay/verify**: re-running stored observations + ruleset must reproduce the
  stored trace exactly.

**Area 3: people and workflow**
- D-18 `Actor(id, display, roles, identity_source)`, where identity_source is AUTHENTICATED or
  ASSERTED and is stored on every event. Four-eyes compares `id`. *(The decision itself is deferred;
  see open items.)*
- D-19 Review per prep batch (with per-result exclusions); release per sample + analysis (enables
  partial reports).
- D-20 A correction after reporting goes through the full cycle → a new report snapshot marked
  "Amended", linked to the one it replaces.

**Area 4: time travel, reports, other modules**
- D-21 Optional `as_of` on every read; DB-clock timestamps + row ID tiebreaker.
- D-22 slim-data owns `report_issues` (result versions on each report, file hash, amended-from link);
  the report engine calls `record_issue(...)`.
- D-23 **The caller owns the transaction**: slim-data flushes, never commits, and returns facts
  ("results released"). The host moves the TR status through the portal's service in the same
  transaction.

**Area 5: data in, tests**
- D-24 A separate "register prep batch" call (a future prep-book module would call it). For v1 the CSV
  import may carry the prep info and register the batch in the same transaction.
- D-25 The CSV carries raw readings (sample, analyte, role, replicate, value, unit, MDL, dilution).
  One file = one instrument run.
- D-26 Tests: unit, golden CSVs, SQLite + Postgres, migrations, replay, architecture (domain/engine
  import only stdlib + pydantic), hypothesis property tests on every step, **95% branch coverage
  gate** on domain + engine.
- D-27 A demo generator with named scenarios (clean pass, cups disagree, spike pooled from another
  sample, below MDL, rerun + exception, amended report), doubling as end-to-end tests.

**Ruleset: cup agreement and cup choice**
- D-28 Agreement has three zones: both cups < MDL → agree ("< MDL"); smaller cup below the switch
  point → absolute rule |c1−c2| ≤ limit; otherwise RPD rule |c1−c2|/mean ≤ limit. The smaller cup
  picks the zone.
- D-29 Demo defaults: switch 5× MDL, RPD 20%, absolute ±1× MDL (EPA-style convention; no jump at the
  switch point since 20% × 5 = 1).
- D-30 Publishing warns, and requires a stored reason, if the numbers create a jump at the switch.
- D-31 Agreement ("do they agree?") and cup choice ("which do we believe?") are separate steps.
- D-32 Cup choice uses an **evidence ladder**; the first conclusive rung decides and is recorded in
  the trace. Default order: analyst exclusion → cross-element pattern (whole cup off across many
  elements) → spike witness → direction prior (contamination-prone → lower cup; loss-prone → higher)
  → no decision (NEEDS_SUPERVISOR/RERUN).
- D-33 Bias: a spike that picks the cup biases that pairing's recovery toward 100%. Resolved by D-37.
- D-51 Rung 2 = multiplicative (cup-level) fault only: ≥ 5 elements, ≥ 75% of cup ratios within ±5% of
  the median, median fails RPD → cup picked once by ≥ 2 agreeing spike witnesses, else RERUN; applies
  to the cluster; outliers and all additive faults (contamination/loss) stay per element.
- D-52 Direction prior = ruleset table by element, optional (element, prep) override;
  CONTAMINATION_PRONE → lower cup, LOSS_PRONE → higher, NEUTRAL → no decision.

**Ruleset: spikes, recovery, pooling**
- D-34 Spike vs spike reuses the agreement test.
- D-35 Spike witness noise guard: w = spiked − added decides only if
  (distance to far cup − distance to near cup) > spiked × spike precision (demo 5%).
- D-36 Recovery = (spiked − chosen value) / added. The 4× rule: sample > 4× spike → NOT_EVALUABLE.
  Window demo default 75–125%.
- D-37 Pooling = **batch median** of valid recoveries (not CHEMTEST's best-of, which is cherry-picking).
  If a sample's spike chose its cup, it uses the median of the *other* samples (leave-one-out); alone
  in its batch → its own recovery, flagged "used for selection".
- D-38 Results are **not** corrected for recovery; recovery is reported beside them.
- D-53 Per element: all spikes NOT_EVALUABLE → NEEDS_SUPERVISOR + standard qualifier; pooled median
  (over evaluable recoveries) outside the window → RERUN.

**Ruleset: reported value**
- D-54 Mean of agreeing cups (else chosen cup) × dilution; "< effective MDL" (MDL × dilution); optional
  QL (demo 3.33 × MDL) → "estimated"; round once to fixed decimal places per analyte (demo 3 dp),
  half-even; compare unrounded.
- D-55 ± = half-delta × dilution when cups agree (same dp as the value); N/A when a cup was chosen or
  below MDL.
- D-56 No quality score: checks emit flags (INFO/WARN/NEEDS_SUPERVISOR/RERUN, table in the ruleset);
  verdict = worst flag; trace lists every flag.
- D-57 Reviewer clears REPORTABLE; supervisor accepts NEEDS_SUPERVISOR (acceptance, not an exception)
  or sends to rerun; releasing a RERUN without rerunning = supervisor exception (D-09). Four-eyes
  throughout; policy table (severity, action) → minimum role.
- D-58 Method blank > MDL: samples ≥ 10× blank → WARN, below → RERUN; never subtracted.
- D-59 Any cup above the verified linear range (may exceed the top calibration standard) → RERUN.
- D-60 Spec exceedance → INFO only; quality and compliance kept apart.
- D-61 Above the top calibration standard but within the linear range → INFO.

**Build**
- D-62 `analyst_ids` on each version for four-eyes; correction = CORRECTION_STARTED event then new
  version; REPORTABLE rerun request needs reviewer; four-eyes on review/accept/exception/release only.

**Ecosystem**
- D-39 The OSS repos may diverge from the lab's private code; they're portfolio code now.
- D-40 One host app built from LabPortal, named slim-lims: composition root + UX + transaction owner.
  Modules stay libraries. (Legacy VBA router dropped by D-44.)
- D-41 LabPortal's repo becomes `slim-lims-oss` (old one renamed `slim-lims-legacy-oss`, archived);
  package `slim_lab_portal` → `slim_lims` in its own PR.
- D-42 Review/release screens are host staff pages; slim-data ships no UI.
- D-43 Invoicing becomes a library on slim-domain's tables and bills from TR + `report_issues` +
  quotes; "Invoice" on a complete-report TR → sales order + status invoiced, one transaction.
- D-44 Sandbox only: no legacy Excel/VBA paths (no VBA router, no xlsx invoicing). v2 TR is the only
  TR; v1 parsers, `to_slim_v1`, `network_paths.py`, `report_generation_log` are legacy to retire.
- D-45 The portal's TR domain + repository move into slim-domain (modules can't import the host).
- D-46 Reports stay Excel, behind a renderer interface.
- D-47 slim-data owns `report_issues`; the report engine is a pure renderer (no DB).
- D-48 `AnalyteRef(analysis_id, code)` everywhere; `analysis_id` is slim-domain's.
- D-49 Ecosystem decisions + docker-compose live in the host repo; move D-39–D-49 there once it has
  an ecosystem doc.

**People (public demo)**
- D-50 Real login + config-enabled demo personas recorded as ASSERTED; policy table of accepted
  identity sources per action; four-eyes on `Actor.id`; seeded history, nightly reset, relative dates.

## Deferred / open items

**Architecture**
1. ~~D-18 identity~~ resolved by D-50: real auth + demo personas recorded as ASSERTED.
2. ~~Sample identity limit~~ resolved by D-44/D-45: v2 TRs are the only TRs.
3. ~~Ecosystem fit~~ resolved by D-39–D-49.

**Ruleset design (next up, in this order)**
4. ~~Cross-element criterion~~ resolved by D-51.
5. ~~Direction-prior table~~ resolved by D-52.
6. ~~No valid recovery~~ resolved by D-53.
7. ~~MDL floor and rounding~~ resolved by D-54.
8. ~~± uncertainty~~ resolved by D-55.
9. ~~Quality score~~ resolved by D-56 (replaced by severity flags).
10. ~~Who may clear what~~ resolved by D-56/D-57. **Ruleset design complete.**

**Parked (not v1):** full measurement-uncertainty budgets, unit conversion to the customer's
reporting unit, instrument-specific readers (ICP-MS native formats), results for v1 xlsx TRs, a
result-entry UI.

## Plan

**Phase A: design.** ✅ Signed off by Jamie 2026-10-19 (D-01…D-61, DESIGN.md).

**Phase B: build (current).** Milestones approved 2026-10-19. Each milestone ends green
on CI, and Claude explains it to Jamie before moving on.

| # | Milestone | Contents |
|---|---|---|
| M0 ✅ | Skeleton | `pyproject`, package layout (`domain/ engine/ pipeline/ persistence/ service/ adapters/`), CI matrix {SQLite, Postgres 16} × {Py 3.11, 3.12}, architecture test, OSS guard, Alembic env with own version table |
| M1 ✅ | Domain | Enums, value objects (Decimal `Quantity`, `Actor`, `SampleRef`, `AnalyteRef`), `PrepBatch`, `Reading`, result versions/events, pure workflow rules (four-eyes, exceptions, override policy) |
| M2 | Engine | Step protocol + versioned registry, ruleset model + validation (cliff check), steps: linear range, agreement, evidence ladder, spike witness, recovery, pooling, scale-fault check, MDL/rounding, ±, method blank, spec flags, flags → verdict; trace; replay |
| M3 | Persistence | Append-only tables, repository, Postgres append-only triggers, `as_of` reads, audit-log integration, ruleset publishing |
| M4 | Data in | `ResultReader` plugin protocol, `generic_csv/1`, import plan/preview, all-or-nothing import, file-hash idempotency, register prep batch |
| M5 | Workflow service | Review, release, exceptions/overrides, corrections, `report_issues`, amended reports |
| M6 | Integration | `ReferenceLookup` adapter over slim-domain + portal tables, read model for the report engine, host-app wiring, demo scenario generator, end-to-end test |
| M7 | Finish | `verify` command, README, DESIGN.md final, walkthrough for Jamie |

## File map

```
slim_data/
├── domain/          # pure: stdlib + pydantic only (tests/test_architecture.py)
│   ├── base.py      # Frozen base model; Dec = Decimal that refuses floats
│   ├── enums.py     # ReadingRole, IdentitySource, Role (tiered), Severity, Verdict, Qualifier, ResultStatus, Action, EventKind
│   ├── values.py    # SampleRef ("TR00042-03"), AnalyteRef, Quantity
│   ├── actor.py     # Actor(id, display, roles, identity_source)
│   ├── observations.py  # PrepBatch, SpikeAddition, Run, SourceFile, Reading, ReadingExclusion
│   ├── results.py   # Flag, verdict_from_flags, ResultVersion, ResultEvent, Result (status from events)
│   ├── policy.py    # Policy (role rules + identity rules), DEMO_POLICY, PRODUCTION_EXAMPLE_POLICY
│   └── workflow.py  # review / accept / request_rerun / record_exception / release / reject / start_correction
├── engine/          # pure: stdlib + pydantic + domain only
│   ├── numbers.py   # fixed Decimal context (exact replay), rpd, median
│   ├── model.py     # EvaluationInput; Cell/BatchState (working state); CellOutcome, BatchEvaluation
│   ├── step.py      # Step protocol: name@version, Params, flag_codes, requires, run, warnings
│   ├── ruleset.py   # Ruleset (steps + params + severity table), content_hash, validate_ruleset
│   ├── registry.py  # StepRegistry (add, never replace), default_registry
│   ├── runner.py    # evaluate(input, ruleset) -> BatchEvaluation; replay(...) -> differences
│   └── steps/       # cup_shape@1, linear_range@1, agreement@1, cup_choice@1 (manifest: tests/engine/step_manifest.txt)
├── pipeline/        # reader plugins -> import plan
├── persistence/
│   ├── oss_guard.py # refuses non-demo DBs ("precilab" never; other hosts via SLIM_DATA_ALLOWED_DB_HOSTS)
│   ├── db.py        # Base (own MetaData, naming convention), make_engine: the only create_engine call
│   ├── migrate.py   # upgrade/downgrade(connection); VERSION_TABLE = alembic_version_slim_data
│   ├── alembic.ini, migrations/   # env.py: own version table + include_object (own tables only)
├── rulesets/        # ruleset files (JSON); demo_metals.json
├── service/         # ResultService facade (DESIGN.md §11)
└── adapters/        # ports over other modules (ReferenceLookup)
tests/               # SQLite per test, Postgres via TEST_DATABASE_URL; factories.py builds domain objects
.github/workflows/ci.yml  # lint (ruff) + {SQLite, Postgres 16} × {3.11, 3.12} + coverage gate
```

Run: `pip install -e ".[dev]" && pytest`; `ruff check . && ruff format --check .`.

## Conventions for the build

- Python ≥ 3.11, pydantic v2 (frozen models), SQLAlchemy 2, Alembic, pytest + hypothesis.
- `domain/` and `engine/` import only stdlib + pydantic (enforced by test); liftable into slim-domain.
- Result values are `Decimal`, never float; raw and reported values are both stored.
- Portable schema (SQLite + Postgres): enums as ints, no ARRAY columns, JSON via SQLAlchemy `JSON`.
- Repositories flush; the caller commits (matches LabPortal).
- Configuration and data are injected (DB, clock, rulesets, policy tables); never `if demo_mode:`
  branches.
