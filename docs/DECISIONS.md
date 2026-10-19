# slim-data — design decisions

Each entry: the decision, the options weighed, and why. Newest last. `DESIGN.md` is rewritten from
these once the design walk-through is finished.

## Ground rules (2026-10-12)

- **D-01 Scope.** slim-data owns analytical results: observations, evaluation, decided results,
  review, release, report snapshots. It is not a general data-access layer, because each module keeps
  its own repositories.
- **D-02 Packaging.** A library, run in the same process as the rest of the ecosystem. The host web
  app exposes its endpoints. There is no separate service.
- **D-03 Database.** Shared Postgres (SQLite for tests and the demo). slim-data owns only its own
  tables, with its own Alembic version table. No foreign keys into other modules' tables. Reuses
  slim-domain's hash-chained `audit_log`.
- **D-04 Never update, never delete.** Every change is a new row, so the state at any past moment can
  be reconstructed ("time travel"). Limits applied, and the result versions on each report, are
  snapshotted rather than referenced.
- **D-05 Own rules, CHEMTEST-shaped.** The public repo ships a fictional ruleset with CHEMTEST's
  general shape (cups, spikes, batch-pooled recovery, quality score, supervisor override). It never
  contains CHEMTEST's real thresholds or weights. Lab-real versus demo is a configuration swap.

## Area 1 — what gets stored (2026-10-12)

- **D-06 One row per reading.** A reading is tagged with a role (CUP, SPIKE, BLANK, …) and a
  replicate number. Rejected: fixed columns (cup_1, cup_2, …), where every new QC shape needs a
  migration.
- **D-07 The prep batch is the unit of evaluation.** Samples of one matrix prepped together are
  evaluated together, so spike recovery can be pooled across them. The maximum batch size is a
  ruleset parameter, not a schema constraint.
- **D-08 Verdict is separate from status.** The engine outputs a verdict (REPORTABLE /
  NEEDS_SUPERVISOR / RERUN). Workflow status (draft → reviewed → released) belongs to people.
  Workflow rules read the verdict.
- **D-09 Escape hatch = recorded exception (ISO 17025 §7.10, nonconforming work).** RERUN and
  NEEDS_SUPERVISOR can be overridden by an exception event that records who, a reason category and
  free text (required), against one result version. The overrider can't be the analyst. The result is
  permanently flagged `released_with_exception`, and exceptions can be queried across the lab.
- **D-10 Override authority is configuration.** A policy table maps each verdict to the roles that may
  override it. Tiered is the default (NEEDS_SUPERVISOR → supervisor or quality; RERUN → quality); a
  flat lab supplies a different table. Roles come from the caller; slim-data only checks them.
- **D-11 Report qualifier.** Standard text per reason category (printed on the report) plus an
  optional note. The free-text reason stays internal.
- **D-12 Rule changes don't touch existing results.** Each result keeps the ruleset version it was
  made with. Re-evaluating a draft is an explicit action that creates a new version; released results
  never change silently.

## Area 2 — the rules engine (2026-10-12)

- **D-13 A step library plus ruleset data.** Procedures (cup agreement, spike recovery, batch pooling,
  MDL floor, scoring, verdict) are Python steps, each tested once. A ruleset is data: which steps,
  in what order, with which thresholds. Rejected: pure decision tables (pooling would need a
  mini-language inside them) and pure Python rules (every threshold change would be a code release).
- **D-14 Rulesets are published into the database and immutable.** A ruleset is authored as a file,
  then published by a command as a numbered, content-hashed version that is never edited. Results
  reference that version. Rejected: files only, where time travel would also need git.
- **D-15 Selection by (analysis, prep method)**, falling back to an analysis-wide default. The batch
  snapshots its prep method, defaulting from the chemical's prep in slim-domain.
- **D-16 A full decision trace is stored per result**: each step's inputs, output and the rule that
  fired, as structured JSON.
- **D-17 Steps are versioned and never removed** (`spike_recovery@1`, `@2`). A published ruleset pins
  exact step versions. A bug fix in a step is a new version, never an edit. This keeps replay
  possible: re-running any stored result from its observations and ruleset version must reproduce its
  stored trace exactly (`verify` command; also a test).

## Area 3 — people and workflow (2026-10-12)

- **D-18 Identity is deferred, but the shape is fixed now.** slim-data never authenticates. It
  receives `Actor(id, display, roles, identity_source)` from the caller and compares `id` for
  four-eyes. `identity_source` is AUTHENTICATED or ASSERTED (typed initials) and is stored on every
  event, so records stay honestly labelled whichever way the open question is settled.
  **OPEN:** require authenticated users, or accept initials? Revisit during the build.
- **D-19 Review per prep batch; release per sample + analysis.** The reviewer judges the batch the
  rules evaluated and can exclude individual results. Release per sample and analysis makes partial
  reports natural. Rejected: per result (about 180 clicks for one ICP-MS batch) and per TR (one slow
  analysis would block the whole request).
- **D-20 Corrections after reporting go through the full cycle.** Correction → new draft version →
  review → release → new report snapshot marked "Amended", pointing at the report it replaces. The
  original report stays retrievable. Rejected: a supervisor fast path, which would skip four-eyes on
  the change most likely to be scrutinised.

## Area 4 — time travel, reports, other modules (2026-10-12)

- **D-21 Optional `as_of` on every read.** `as_of=None` means now. A single mechanism, so a single test
  suite proves "the state at time T" for everything. Timestamps come from the database clock, with the
  row ID as tiebreaker, never the app server's clock. Only slim-data's own tables can be read as of a
  past time; other modules' data is covered by snapshots (D-04).
- **D-22 slim-data owns `report_issues`**: which result versions went onto a report, the report file's
  hash, and the amended-from link. The report engine calls `record_issue(...)`. This keeps the chain
  from result to report in one module.
- **D-23 Transactions are owned by the caller.** slim-data flushes but never commits, and returns
  facts such as "results released". The host app moves the TR through the portal's service in the
  same transaction, so the two happen together or not at all, and slim-data knows nothing about the
  portal. Rejected: an event/outbox table, which is the right pattern for separate services but
  overkill for one process and one database.

## Area 5 — data in, upgrades, tests (2026-10-12)

- **D-24 Prep batches are registered by their own call.** Prep information (which samples were
  prepped together, the prep method, spike amounts) comes from the prep book at prep time; readings
  come from the instrument later. slim-data has a "register prep batch" call for a future prep-book
  module. For v1, the CSV import can carry the prep information and register the batch in the same
  transaction, so the demo works. Rejected: prep information always travelling in the CSV, which makes
  the instrument file the source for data it doesn't own and forces a redesign when a prep module
  arrives.
- **D-25 The CSV carries raw readings** (sample, analyte, role, replicate, value, unit, MDL, dilution),
  not finished results. One file is one instrument run.
- **D-26 Test bar.** Unit, golden CSV fixtures, SQLite and Postgres, migrations, replay, architecture
  tests, plus hypothesis property tests on every engine step. CI fails below 95% branch coverage on
  domain and engine.
- **D-27 A demo generator with named scenarios**: clean pass, cups disagree, a spike fails in one
  sample but is pooled from another, below MDL, rerun plus exception, amended report. The same
  scenarios double as end-to-end tests.

## Ruleset design — cup agreement and cup choice (2026-10-13)

- **D-28 The agreement test has three zones.** (1) Both cups below MDL: they agree, and the result
  reports as "< MDL". (2) The smaller cup is below the switch point: the absolute rule,
  |cup1 − cup2| ≤ absolute limit. (3) Otherwise the RPD rule, |cup1 − cup2| / mean ≤ RPD limit.
  The smaller cup decides the zone; that's the cautious choice, and it matches the published
  convention "if either result is below…".
- **D-29 Demo defaults: switch at 5× MDL, RPD 20%, absolute ±1× MDL.** This is the textbook
  convention (US EPA contract-lab style), adapted from reporting limit to MDL. These values have no
  jump at the switch point, because 20% × 5 = 1.
- **D-30 Cliff check on publish.** If a ruleset's numbers create a jump at the switch point
  (absolute limit ≠ RPD × switch), publishing warns and requires a reason, which is stored with the
  ruleset. A property test proves the engine itself has no jump when the numbers line up.
- **D-31 Agreement and cup choice are separate steps.** Agreement only answers "do the cups agree?".
  Cup choice runs only when they disagree, and weighs evidence through a ladder (D-32).
- **D-32 The evidence ladder.** Rungs are tried in the order the ruleset gives; the first conclusive
  rung decides, and the trace records which one. v1 rungs, in default order (strongest evidence
  first):
  1. *Analyst exclusion*: a reading marked bad with a reason, recorded as an event. A human-observed
     fact outranks inference.
  2. *Cross-element pattern*: one cup reads high (or low) for many elements at once, so that whole cup
     is suspect.
  3. *Spike witness*: the cup closest to (spiked reading − spike added). Only used when the spike is
     informative, i.e. its expected noise is clearly smaller than the cup difference.
  4. *Direction prior*: per element and prep, contamination-prone → believe the lower cup; loss-prone
     → believe the higher cup. It's a prior, not evidence, so it comes last.
  5. *No decision*: verdict NEEDS_SUPERVISOR or RERUN, as the ruleset sets.
- **D-33 Known bias, to settle in the spike-recovery step.** When the spike witness picks the cup,
  recovery computed from that same pairing is biased towards 100%. Options: flag it as "used for
  selection", or report the batch-pooled recovery instead.

**OPEN (ruleset):** the noise-guard formula for the spike witness; the cross-element criterion (how
many elements, how far off); whether the direction-prior table lives per element or per element and
prep; how D-33 is resolved.

## Ruleset design — spikes, recovery, pooling (2026-10-13)

- **D-34 Spike vs spike reuses the agreement test.** Two spikes on one sample are checked with the
  same three-zone test as cups (D-28). One step, tested once, used twice.
- **D-35 The spike witness noise guard is a single inequality.** The witness value
  w = spiked − added decides only if (distance from w to the farther cup − distance to the nearer
  cup) > spiked × spike precision. It is inconclusive both when w sits midway between the cups and
  when the spike is too large for its noise to resolve the cup gap. (Spike precision is a ruleset
  number; demo default 5%.)
- **D-36 Recovery = (spiked − chosen sample value) / spike added.** Two guards:
  - The 4× rule: if the sample exceeds 4× the spike added, recovery is NOT_EVALUABLE, not failed.
  - The acceptance window (demo default 75–125%).
- **D-37 Pooling uses the batch median of valid recoveries.** Rejected: best of batch (CHEMTEST-like),
  which is cherry-picking: it reports the luckiest spike as if it were typical. If the spike witness
  chose a sample's cup, that sample uses the median of the other samples in the batch
  (leave-one-out). This resolves D-33. With no other samples, it reports its own recovery flagged
  "used for selection".
  Proposed default, to confirm: if no valid recovery exists in the batch, the verdict is
  NEEDS_SUPERVISOR.
- **D-38 Results are not corrected for recovery.** Recovery is reported beside the result as QC
  evidence. A ruleset option for correction can be added later; if it is ever on, it must be stated
  on the report.

**OPEN (ruleset):** the cross-element criterion; the direction-prior table (per element, or per
element and prep); the steps still to design (MDL floor and rounding, ± / half-delta uncertainty,
quality score, mapping from score to verdict).

## Ecosystem — how slim-data fits SLIM as a whole (2026-10-19)

The scope has grown from separate related modules (only one wired in, called by VBA hooks) to a full
LIMS with its own UX, demonstrable end to end: submission → check-in → results → report → invoice.

- **D-39 The OSS repos may diverge from the lab's private code.** slim-domain-oss, slim-lims-oss and
  slim-report-engine-oss were synced from private repos; from now on they are portfolio code and are
  changed freely. Syncing from private stops.
- **D-40 One host app, built from LabPortal, carrying the name slim-lims.** It is the composition
  root and the UX: app factory, config, engine, auth (logins and roles), CSRF/CSP, every screen and
  the JSON API, and it owns the transaction. It *calls* the modules; the modules never import it or
  each other's internals. slim-data, the report engine, invoicing and slim-domain stay libraries with
  their own tables and Alembic version tables. Today's slim-lims endpoints (`/generate-report`,
  `/resolve/*`, API-key auth) become one legacy router inside it, so the VBA hooks keep working until
  screens replace them. Customer and staff pages are separate routers with separate auth rules, so
  the customer side can be deployed apart later without a rewrite.
  - Rejected: UX in slim-lims, LabPortal keeps intake and login. Needs SSO or a shared session across
    two apps and splits the staff workflow (check-in in one app, everything else in the other). Its
    real benefit, isolating the customer-facing surface, is kept by the separate routers.
  - Consequences: the portal ↔ slim-lims HTTP link (bearer token, `test_lims_contract.py`) becomes
    in-process calls. Closes open item 3 (host process). Resolves D-18 towards authenticated logins
    (decision to confirm).

- **D-41 The host lives in LabPortal's repo, renamed on GitHub to `slim-lims-oss`.** The current
  slim-lims-oss is renamed first (e.g. `slim-lims-legacy-oss`) and archived; its `app.py` is ported in
  as the legacy VBA router. The Python package `slim_lab_portal` becomes `slim_lims` in its own PR.
  - Rejected: copy the portal's code into slim-lims-oss (loses the portal's PR history, the best
    portfolio evidence); leave the names alone (the "portal" repo would be the LIMS, confusing).
  - GitHub redirects renamed repos, so old links keep working. Jamie does the renames on GitHub.

**OPEN (ecosystem):** where the review/release screens sit; the billing source for invoicing; one report
record (`report_issues` vs slim-domain's `report_generation_log`); one analyte identity across modules;
where ecosystem-level decisions live (here, or an umbrella repo).
