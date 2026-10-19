# slim-data — Claude context

> **Read first.** This repo is in the **design phase**. No code has been written yet, on purpose.
> Jamie is the **designer**; Claude writes all the code once the plan is signed off, then explains the
> finished product until Jamie can defend every decision in an interview. Drive design through
> decisions with a recommendation each time, push back when you disagree, and don't start building
> until Jamie approves the plan. Short replies; Jamie is token-constrained.

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
| `docs/DECISIONS.md` | **Authoritative.** All decisions D-01…D-44 with the options weighed and why. Append new ones there. |
| `docs/DESIGN.md` | **Out of date** (first draft, 2026-10-12, written before Areas 1–5). Rewrite it from DECISIONS.md before building. |
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

## Deferred / open items

**Architecture**
1. **D-18 identity:** require authenticated user logins, or accept typed initials? Deferred until
   the build. The Actor shape already supports either.
2. **Sample identity limit:** results key on `SampleRef(tr_number, position)` (e.g. `TR00042-03`),
   so only portal (v2) TRs can get results until slim-domain adopts TR v2. Accepted for now; carried
   over from the first draft, still to be re-confirmed in the rewritten DESIGN.md.
3. **Ecosystem fit (current, before the ruleset):** host, screens, billing decided (D-40–D-43). Still open: one report record
   (`report_issues` vs `report_generation_log`); one analyte identity; where ecosystem decisions live.

**Ruleset design (next up, in this order)**
4. Cross-element criterion: how many elements, how far off, before a whole cup is suspect?
5. Direction-prior table: per element, or per element + prep method?
6. Confirm: no valid recovery anywhere in the batch → NEEDS_SUPERVISOR (proposed in D-37).
7. MDL floor and rounding / significant figures (a ruleset step).
8. ± uncertainty (CHEMTEST-style half-delta = half the cup difference; the report already has a ±
   column).
9. Quality score: what it adds up, its scale and cut-offs.
10. Score → verdict mapping, and which verdicts each override role can clear.

**Parked (not v1):** full measurement-uncertainty budgets, unit conversion to the customer's
reporting unit, instrument-specific readers (ICP-MS native formats), results for v1 xlsx TRs, a
result-entry UI.

## Plan

**Phase A: finish the design (current).** Work through open items 4–10 with Jamie, one at a time,
each as a decision with a recommendation; append each to `docs/DECISIONS.md`. Then rewrite
`docs/DESIGN.md` from the decision log, and get sign-off.

**Phase B: build (after sign-off).** Proposed milestones, not yet approved. Each milestone ends green
on CI, and Claude explains it to Jamie before moving on.

| # | Milestone | Contents |
|---|---|---|
| M0 | Skeleton | `pyproject`, package layout (`domain/ engine/ pipeline/ persistence/ service/ adapters/`), CI matrix {SQLite, Postgres 16} × {Py 3.11, 3.12}, architecture test, OSS guard, Alembic env with own version table |
| M1 | Domain | Enums, value objects (Decimal `Quantity`, `Actor`, `SampleRef`, `AnalyteRef`), `PrepBatch`, `Reading`, result versions/events, pure workflow rules (four-eyes, exceptions, override policy) |
| M2 | Engine | Step protocol + versioned registry, ruleset model + validation (cliff check), steps: agreement, evidence ladder, spike witness, recovery, pooling, MDL/rounding, uncertainty, score, verdict; trace; replay |
| M3 | Persistence | Append-only tables, repository, Postgres append-only triggers, `as_of` reads, audit-log integration, ruleset publishing |
| M4 | Data in | `ResultReader` plugin protocol, `generic_csv/1`, import plan/preview, all-or-nothing import, file-hash idempotency, register prep batch |
| M5 | Workflow service | Review, release, exceptions/overrides, corrections, `report_issues`, amended reports |
| M6 | Integration | `ReferenceLookup` adapter over slim-domain + portal tables, read model for the report engine, host-app wiring, demo scenario generator, end-to-end test |
| M7 | Finish | `verify` command, README, DESIGN.md final, walkthrough for Jamie |

## Conventions for the build

- Python ≥ 3.11, pydantic v2 (frozen models), SQLAlchemy 2, Alembic, pytest + hypothesis.
- `domain/` and `engine/` import only stdlib + pydantic (enforced by test); liftable into slim-domain.
- Result values are `Decimal`, never float; raw and reported values are both stored.
- Portable schema (SQLite + Postgres): enums as ints, no ARRAY columns, JSON via SQLAlchemy `JSON`.
- Repositories flush; the caller commits (matches LabPortal).
- Configuration and data are injected (DB, clock, rulesets, policy tables); never `if demo_mode:`
  branches.
