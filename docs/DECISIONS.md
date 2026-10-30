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

- **D-42 Review/release screens are host staff pages.** They call slim-data's services; slim-data ships
  no UI. (Follows from D-40.)
- **D-43 Invoicing becomes a library and bills what was reported.** It keeps its own `quotes*` and
  `sales_order*` tables and drops its copied customers/chemicals/analyses/elements tables for
  slim-domain's (one set of IDs). A sales order is built from the TR (customer, PO, turnaround),
  slim-data's `report_issues` (what the customer was shown) and the quotes (prices). Staff press
  "Invoice" on a complete-report TR; the host creates the sales order and moves the TR to invoiced in
  one transaction. Reruns, failed attempts and amendments are never billed twice. Jamie is reworking
  the invoicing module to fit.
- **D-44 Sandbox only: no legacy Excel or VBA paths.** The OSS ecosystem exists to run as a portfolio
  sandbox, informed by real lab experience; it will never run in a lab. Supersedes the legacy parts of
  D-40 and D-41 (no VBA router: the old slim-lims code is archived, not ported) and of D-43 (no xlsx
  invoicing route). Consequences: the portal's v2 TR is the only TR; slim-domain's v1 TR form parsers,
  `network_paths.py`, the portal's `to_slim_v1` bridge and `report_generation_log` (keyed on an xlsx
  input path) are legacy to retire.
- **D-45 The TR domain moves into slim-domain.** The portal's `domain/` (TR v2, enums, status workflow)
  and its TR repository move to slim-domain, replacing v1. Modules may not import the host (D-40), yet
  slim-data, the report engine and invoicing all need TR types; the host uses them like any module.
- **D-46 Reports stay Excel, behind a renderer interface.** The report engine's template-faithful
  layouts are the asset; a PDF/HTML renderer can be added later without touching callers.
- **D-47 slim-data owns `report_issues`; the report engine is a pure renderer.** Data in, file out, no
  DB. Amended reports belong to the results lifecycle (D-20), which lives here.
- **D-48 One analyte identity: `AnalyteRef(analysis_id, code)`** in every module. `analysis_id` is
  slim-domain's; `code` is an element symbol or ion code. Invoicing quotes key on the same IDs; the
  portal's `analysis_catalog` is display/grouping, matched by ID.
- **D-49 Ecosystem decisions and docker-compose live in the host repo** (`slim-lims-oss`). Rejected:
  an umbrella repo (one more repo to keep in sync; the host is already the composition root).
  D-39–D-49 stay here until the host repo has its ecosystem doc, then move there.

## People — identity in a public demo (2026-10-19)

- **D-50 Real authentication, plus demo personas recorded as ASSERTED.** Settles D-18.
  - Password login (bcrypt, roles, sessions) stays the production path, as in the portal today.
  - Demo personas are a second sign-in method enabled by configuration: "Continue as…" customer, analyst,
    reviewer, supervisor, billing, with a persona switcher in the header. The persona list is data; no
    `if demo:` branches.
  - Persona actions carry `identity_source = ASSERTED`: the audit trail never claims more than it knows.
  - A policy table says which identity sources each action accepts (demo config: ASSERTED may release;
    a production config would require AUTHENTICATED). Same spirit as D-05: config swap, not code.
  - Four-eyes still compares `Actor.id`, so a visitor who reviews their own batch is blocked and told
    to switch persona; the rule is visible in the demo.
  - Non-persistent sandbox: the D-27 scenarios are pre-run at seed so a first visitor sees history
    (time travel, amended report, exception, billed TR); scheduled reset (e.g. nightly) with a banner;
    seed dates relative to today; the audit hash chain restarts per reset and `verify` checks it from
    its start. Shared state between resets is accepted (per-visitor databases rejected as cost
    without visible benefit). Personas can't create users, change policy or publish rulesets; CSV
    uploads stay size-limited and are parsed, never executed.
  - Rejected: typed initials only (no real auth to show); login-only (reviewers won't sign up, and
    four-eyes would dead-end a lone visitor).

## Ruleset design — cross-element pattern (2026-10-19)

- **D-51 Rung 2 detects only a multiplicative (cup-level) fault.** Refines D-32's "cross-element
  pattern". Additive faults (contamination, loss) are element-level: cup A can pick up Fe while cup B
  picks up Zn and every other element in both cups is good, so they stay on the per-element ladder.
  Multiplicative faults (weighing, dilution, volume error) scale a whole cup, leaving the same ratio
  cup A / cup B across nearly every element.
  - Evaluable elements: both cups above the switch point (D-28), not analyst-excluded.
  - Trigger: ≥ 5 evaluable elements, ≥ 75% of their ratios within ±5% of the median ratio, and the
    median ratio itself fails the RPD limit. (Ruleset parameters; demo defaults.)
  - Decision, once per sample: spike witnesses across elements (the spike is a separate weighing);
    ≥ 2 conclusive witnesses that agree pick the cup, else the sample's verdict is RERUN. The choice
    applies to every element inside the cluster and is recorded once in the trace.
  - Elements outside the cluster go down the per-element ladder, so contamination on top of a scale
    fault is still caught.
  - Rejected: "≥ 75% disagree in the same direction" (contamination can look like that); forcing one
    cup for all elements (discards good data when faults are element-level).

## Ruleset design — direction prior (2026-10-19)

- **D-52 The direction prior is a ruleset table keyed by element, with optional per-prep overrides.**
  Susceptibility depends on the prep (a hot acid digest can lose Hg/As/Se; glassware leaches B/Na/Si;
  handling adds Zn/Fe), so a row for (element, prep method) overrides the element default. Values:
  CONTAMINATION_PRONE (believe the lower cup), LOSS_PRONE (believe the higher cup), NEUTRAL (rung is
  inconclusive → no decision; no guessing). The table is part of the ruleset, so it's versioned and
  content-hashed (D-14); the trace records the row used and whether it was a prep override. Demo
  values are invented but plausible (D-05). After D-51 it only ever handles additive faults.

## Ruleset design — no valid recovery (2026-10-19)

- **D-53 "No valid recovery" splits in two, per element across the batch.** Refines D-37's proposed
  default.
  - Every spike NOT_EVALUABLE (4× rule): recovery can't be measured, which says nothing about the
    prep → NEEDS_SUPERVISOR with a standard qualifier ("spike recovery not evaluable: sample
    concentration exceeds spike level"); the supervisor may release or order a rerun with a larger
    spike.
  - Recoveries evaluated but the pooled median is outside the window (demo 75–125%): evidence the
    prep or matrix failed → RERUN (a supervisor can still override through an exception, D-09).
  - Clarifies D-37: the pooled median is taken over evaluable recoveries (individual out-of-window
    values still count) and the median is what's compared with the window; leave-one-out still
    applies when a sample's spike chose its cup.

## Ruleset design — reported value, MDL floor, rounding (2026-10-19)

- **D-54 The reported value step.**
  1. Value: the mean of the two cups when they agree, else the chosen cup; then × dilution.
  2. MDL floor on the *effective* MDL (MDL × dilution): below it the result reads "< effective MDL".
  3. Optional quantitation limit (QL), demo default 3.33 × MDL (MDL ≈ 3σ, QL ≈ 10σ): values between
     MDL and QL carry an "estimated" qualifier. Fits the report's existing MDL/QL column.
  4. Round once, at the end, to a fixed number of decimal places set per analyte in the ruleset, with a
     ruleset-wide default (demo 3 dp); round half to even (no upward bias over many results).
     *Revised the same day:* significant figures were proposed first (constant relative precision
     across magnitudes) but rejected: customers read fixed-decimal columns, and a column jumping
     between `0.00123` and `12300` confuses them. The MDL floor already covers the small values that
     fixed decimals would hurt, and per-analyte places match how LIMS commonly configure reporting.
  5. Every comparison (agreement, recovery window, spec limits) uses unrounded values; raw and
     reported values are both stored, so replay (D-17) reproduces the result exactly.

## Ruleset design — ± on the report (2026-10-19)

- **D-55 ± is the half-delta, only when the cups agree.**
  - Cups agree: ± = (|c1 − c2| / 2) × dilution, rounded to the same decimal places as the value (D-54).
  - Cups disagree (one cup chosen): N/A. Half the gap would describe the rejected cup, not the result.
  - Below MDL: N/A.
  - Labelled as a replicate-based estimate, not an expanded uncertainty.
  - Rejected: a floor for identical cups and a pooled batch repeatability for chosen cups (too complex
    for what the customer gains; N/A is more honest than an estimate).

## Ruleset design — verdict from flags (2026-10-19)

- **D-56 No quality score: severity flags, worst wins.** Deliberate departure from D-05's "quality
  score" shape. Every check emits named flags with a severity INFO → WARN → NEEDS_SUPERVISOR → RERUN
  (e.g. cup chosen by spike witness = WARN; all spikes NOT_EVALUABLE = NEEDS_SUPERVISOR, D-53; pooled
  recovery outside the window = RERUN). The flag → severity table belongs to the ruleset (versioned,
  D-14). The verdict is the most severe flag; none or only INFO/WARN = REPORTABLE. The trace lists
  every flag, so "why this verdict?" always has a one-line answer.
  - Rejected: a weighted score with cut-offs (CHEMTEST-style). Weights and cut-offs are tuning knobs
    nobody can defend; the explicit checks already carry the reasons.
  - Settles open item 9 and the score → verdict half of item 10.

## Ruleset design — who may clear what (2026-10-19)

- **D-57 Accepting a judgment call is not an exception; releasing a failure is.** Roles (D-50 personas):
  analyst < reviewer < supervisor.
  - REPORTABLE (INFO/WARN only): analyst submits → reviewer (not the analyst) reviews, with WARN flags
    shown → release. Normal events.
  - NEEDS_SUPERVISOR: the supervisor **accepts** (reason category; standard qualifier printed, D-11)
    or **sends to rerun**. Recorded as an acceptance event, not flagged as an exception: the rules
    asked for a human judgment.
  - RERUN: the normal path is new readings → new version → re-evaluated. Releasing without a rerun is
    supervisor-only, via an **exception** (D-09: reason category + required text,
    `released_with_exception` permanently, qualifier on the report) — nonconforming work, ISO 17025
    §7.10.
  - Nobody clears or reviews a result on readings they entered (four-eyes on `Actor.id`).
  - Policy table: (severity, action) → minimum role; tiered by default (higher roles inherit), flat
    possible (D-10); combined with D-50's accepted identity sources per action.
  - Closes open item 10 and the ruleset design.

## Ruleset design — gaps found while rewriting DESIGN.md (2026-10-19)

- **D-58 Method blanks: the 10× blank rule, no subtraction.** Per prep batch and element: a blank below
  MDL is fine. If it exceeds MDL, samples reading ≥ 10× the blank get WARN; samples below 10× the
  blank get RERUN (the contamination could be a large share of the result). Blank values are never
  subtracted from results.
- **D-59 Above the linear range → RERUN.** The limit is the analysis's verified **linear range**, which
  can extend above the top calibration standard (determined per analysis in practice). It comes per
  analyte from the ruleset. Any cup over it raises RERUN ("dilute and rerun"); a value beyond the
  linear range is never reported. *Revised the same day:* first written as "above the calibration
  range", which would have rerun valid results between the top standard and the linear limit.
- **D-60 Spec-limit exceedances are INFO only.** Shown on the report; never affect the verdict. The
  verdict answers "is the data trustworthy?", not "does the sample pass?"; a result over spec is still
  a valid result. Keeps quality and compliance apart.

## Sign-off (2026-10-19)

- **D-61 Above the top calibration standard but within the linear range → INFO.** The reviewer sees
  when that zone was used; it never affects the verdict.
- **Design signed off by Jamie** (DESIGN.md, D-01…D-61) together with the Phase B milestones M0–M7.

## Build — choices made while building M1 (2026-10-19)

- **D-62 Four small workflow choices**, approved by Jamie:
  1. Each result version stores the IDs of the analysts who entered its readings (`analyst_ids`), so
     four-eyes can be checked on the result alone.
  2. A correction is two steps: a CORRECTION_STARTED event on the old version, then the new version.
     Sign-offs are blocked in between.
  3. Sending a REPORTABLE result back for a rerun needs a reviewer; a NEEDS_SUPERVISOR one still needs
     a supervisor (D-57).
  4. Four-eyes applies to review, accept, exception and release, not to reject or correct: an analyst
     may correct their own released result, but someone else reviews and releases the new version.

## Build — choices made while building M2 part 1 (2026-10-19)

- **D-63 Seven engine choices**, approved by Jamie:
  1. A first step, `cup_shape@1`, checks each sample × analyte has cups 1 and 2, one unit across cups
     and spikes, and an MDL. Wrong shape → nothing reportable (RERUN flag); missing MDL → agreement
     falls back to the RPD rule alone (NEEDS_SUPERVISOR flag).
  2. An analyst exclusion applies even when the cups agree (a reading marked bad never feeds a mean);
     it is always the first rung.
  3. Only the per-element rungs (spike witness, direction prior) are reorderable in the ruleset;
     exclusion is always first and the scale-fault check is switched on or off.
  4. A scale-fault decision applies to every element in the cluster, including any whose cups happen
     to agree (D-51).
  5. A batch over the ruleset's size limit is refused (`BatchRejected`), not evaluated with a flag.
  6. Comparisons use dilution-corrected values; the linear-range check uses the raw instrument
     reading, which is what the range was verified on.
  7. When the two cups' MDLs differ, the larger effective MDL is used (cautious).


## Build — choices made while building M2 part 2 (2026-10-19)

- **D-64 Ten engine choices**, approved by Jamie (item 1 before building, the rest at the M2
  walkthrough; the method blank stays a contamination check, never a correction, as D-58 says).
  1. **A method blank has no sample.** `Reading.sample` is required for CUP and SPIKE and must be empty
     for BLANK: the blank belongs to its prep batch (M3 stores the batch on the reading; in the CSV,
     M4, `lab_sample_id` reads `BLANK` beside `prep_batch`). Rejected: a separate BlankReading type
     (a second reading path through import, persistence and replay) and a reserved fake SampleRef
     (a fake TR number would leak into every module). Several blanks → the highest is judged
     (cautious); an excluded blank is dropped.
  2. Spike agreement (§7.2 row 5) runs inside `recovery@1` with the same agreement function (D-34),
     not as a step of its own. Disagreeing spikes → SPIKES_DISAGREE, no recovery from that sample.
  3. Leave-one-out (D-37) applies when the cup was chosen by the spike witness *or* the scale-fault
     check, whose witnesses are spikes too.
  4. Every result gets its element's pooled recovery; the window is judged on the recovery each result
     reports (with leave-one-out it can differ from the batch median).
  5. New flags, demo severities: SPIKE_AMOUNT_MISSING (WARN), RECOVERY_NOT_MEASURED — no usable spike
     for the element in the batch (NEEDS_SUPERVISOR), RECOVERY_USED_FOR_SELECTION (WARN),
     BLANK_MISSING (NEEDS_SUPERVISOR). Missing QC is never silent.
  6. The "< MDL" figure is the effective MDL rounded **up**, so a report never states a lower limit
     than was achieved; values still round half to even (D-54).
  7. A result below MDL is not affected by a contaminated blank (contamination only pushes up).
     Against spec limits it is below a low limit only if its MDL is; SPEC_UNDECIDED when its MDL is
     above a high limit.
  8. D-60 is enforced: a step can mark flags INFO-only, and a ruleset rating them higher is refused.
     Limits in another unit are not compared (SPEC_UNIT_MISMATCH; unit conversion is parked).
  9. The blank is judged against its own effective MDL, else the cups'.
  10. The verdict (§7.2 row 12) is not a step: `CellOutcome.verdict` is the worst flag (D-56).
      Spec limits arrive in the evaluation input (the caller looks them up, §5) and are snapshotted on
      the outcome (D-04).

## Build — choices made while building M3 (2026-10-20)

- **D-65 Persistence choices**, approved by Jamie (items 1–2 before building, #10 revised at their
  request, the rest at the M3 walkthrough).
  1. **No `result_exceptions` table.** An exception is a `result_events` row of kind EXCEPTION (it
     already holds the category and the required text), indexed on kind for the lab-wide query.
     Rejected: a second table holding the same facts, which could drift. Revises DESIGN.md §10.1.
  2. **slim-data sets the audit settings itself.** Every write sets `app.changed_by` (Actor.id) and
     `app.change_reason`, the two names slim-domain's `audit_log` reads; nothing on SQLite. Rejected:
     importing slim-domain, which would make it (and openpyxl) a hard dependency. A test compares the
     names with slim-domain's when it is installed.
  3. The database clock is read **per statement**: `clock_timestamp()` on Postgres (`now()` is frozen
     for the whole transaction, so a review and a release in one transaction would tie), millisecond
     `strftime` on SQLite. `as_of` resolves to that tick: microseconds on Postgres, milliseconds on
     SQLite (acceptable for the demo database; the row id still orders ties).
  4. The append-only triggers are installed on SQLite too, so the tests prove them on both databases;
     Postgres also refuses TRUNCATE.
  5. Decimals are `NUMERIC` on Postgres and text on SQLite (which would store floats), so `12.40` keeps
     its digits on both.
  6. Publishing the same content again returns the existing number instead of a new row. To make an
     older ruleset current again, publish it changed (e.g. renamed). Reads re-check the content hash,
     so a body edited behind the triggers' back is refused.
  7. One policy is in force at a time: the latest published.
  8. A reading row carries its prep batch (where a blank belongs, D-64); spike amounts get their own
     table, `prep_batch_spikes`.
  9. People are stored as an id column (what four-eyes and queries compare) plus the whole Actor as
     JSON, identity source included, as it stood then.
  10. **An exclusion can be undone** (Jamie, same day; first written as "never withdrawn"). Each
      exclusion row is EXCLUDED or RESTORED, with a reason and the person; the newest row for a reading
      decides, so a reading can go out, come back and go out again with its history kept, and `as_of`
      shows what was excluded at any moment. Results already decided don't change; the next explicit
      re-evaluation reads the new state (D-12). Spikes can't be excluded, matching the engine.
  11. `make_engine` lets SQLAlchemy emit BEGIN on SQLite (the documented pysqlite recipe), so
      savepoints and transactional DDL work as on Postgres.
  12. `ResultVersion` gains `raw` and `recovery` (DESIGN.md §6.2 and D-38 say a version holds them).
  13. The report-issue tables exist now; their repository comes with `record_issue` in M5.

## Build — choices made while building M4 (2026-10-20)

- **D-66 Data-in choices**, approved by Jamie: items 1–2 before building, 3–8 at the M4 walkthrough,
  then 4 (revised) and 9–11, which follow from Jamie's lab practice (results exported line by line
  while the run goes on; cups rejected and rerun in the run).
  1. **The file names the analyst; the uploader is recorded too.** Someone may upload on the analyst's
     behalf (lab efficiency). The run's analyst is whoever the file's `analyst` column names, checked
     against the host's staff (`StaffDirectory` port) and stored as ASSERTED, since a file is a claim,
     not a login (D-50). `Run.imported_by` is who uploaded it. **Neither may review or release** those
     results: `Run.entered_by` feeds the result version's `analyst_ids` (D-62), wired in M5.
     Rejected: the importer as the only analyst (no uploads on someone's behalf); trusting the file
     alone (someone could upload their own readings under a colleague's name, then review them).
  2. **Lookups go through `ReferenceLookup`**, which gains `analysis_id(code)`. The preview turns its
     checks into row errors: unknown analysis, analyte not in the analysis, unknown sample, analysis
     not ordered for the sample. Rejected: a numeric `analysis_id` column with no checks.
  3. `prep_batch` is required on every row: every reading belongs to a batch (a blank has nothing
     else, D-64). `prep_method` is needed only to register a new batch; `spike_added` only on SPIKE
     rows.
  4. A file may add readings to a batch that is already registered, but must match it: same analysis
     and prep method, spike amounts as recorded. *Revised the same day:* it may also **add** samples
     and spike amounts the batch doesn't have yet (new rows, never changes), because a file exported
     while the run is going registers the batch with only the samples it has so far. A batch's sample
     and spike rows carry `created_at`, so `as_of` shows the batch as it stood.
  5. *Revised the same day:* first "a reading the batch already has is refused". Replaced by items
     9–10.
  6. The reader is strict: missing, unknown (usually a typo) or repeated columns are file errors;
     values are read as text, so digits are kept; `analyzed_at` needs a UTC offset.
  7. Preview and import are in `service/imports.py`, since they read the database; the reader in
     `pipeline/` stays pure. The import runs in a savepoint, so it is all or nothing even inside the
     host's larger transaction.
  8. Migration 0003 adds `runs.imported_by` as nullable: a run stored before it has no separate
     uploader (none exist outside tests). On SQLite the downgrade puts back the append-only triggers
     that rebuilding the table drops.
  9. **Re-exports of a growing file.** For each cup, the file's n-th reading in a run must match the
     n-th one already stored from that run (value, unit, MDL and dilution, digits included); it is
     then skipped. A difference is a row error: nothing is overwritten. Readings beyond those are new.
     A file with nothing new imports nothing (no run row).
  10. **Attempts: every reading is kept; the newest good one counts.** A cup measured again (rejected
      and rerun in the run, or rerun in a later run) is a new attempt of the same cup. The engine uses,
      per cup, the newest attempt that isn't excluded, else the newest one (as excluded); the others
      are listed in the trace as set aside. This replaces a separate rerun design for M5.
  11. **Rejected in the run.** Optional `rejected` (yes/no) and `note` columns: a rejected row is
      imported and then excluded, with the note as the reason (default "rejected in run …"), recorded
      as the file's analyst's exclusion. A rejection that appears only on a re-export excludes the
      stored reading. Spikes may now be excluded too: an excluded spike is as if it wasn't measured.
  **Open for M5:** early release vs. batch pooling. A result released while the batch is still
  running gets its recovery from fewer spikes, which may change later (an amended report). M5 needs a
  rule, e.g. release only once the batch's blank and enough spikes are in, or a provisional recovery.

## Build — choices made while building M5 (2026-10-20)

- **D-67 Workflow-service choices**, approved by Jamie (items 1–2 before building, the rest at the M5
  walkthrough).
  1. **Released results stay as reported** (early release vs. batch pooling, open since D-66). Results
     may be released while the batch is still running; the gate is the engine's own flags (no blank
     yet, or no usable spike for the element, is NEEDS_SUPERVISOR), so one usable spike is the
     minimum. A released result is frozen with the recovery it was released with, and its trace
     lists the spikes behind it. Re-evaluating only changes unreleased results; a released one that
     would now read differently is listed in `released_affected`, so a person can start a correction
     (and an amended report). Rejected: no release until the batch is done (no early results);
     re-evaluating released results too (many amended reports).
  2. **Release the ready elements.** `release(sample, analysis)` releases the elements that are
     reviewed and cleared; the rest wait, with the reason, for a later partial or complete report.
     Rejected: all or nothing per sample + analysis.
  3. `evaluate_batch` stores a new version only when the outcome changed (values, flags, trace,
     ruleset or who entered the readings), so calling it after every line-by-line import is cheap.
     The reason defaults to "re-evaluated after new readings or exclusions"; a correction always
     makes a new version, with the correction's own reason.
  4. "Reads differently" for a released result means its reported value, qualifier or verdict
     changed; a pooled recovery that moved with later spikes alone doesn't count.
  5. A rejected result is never re-evaluated.
  6. A version's `analyst_ids` are everyone who entered any reading of that sample × analyte in the
     batch: each file's named analyst and its uploader (D-62, D-66).
  7. New qualifier NOT_REPORTABLE for a result with nothing to report (e.g. no cup could be chosen).
  8. `review_batch` reviews every draft of the batch or none (listing every problem); results waiting
     on a rerun or a correction are left for later.
  9. `record_issue` accepts only released versions; a line carrying a version released after a
     correction makes the report amended, so it must name the issue it replaces, of the same TR.
  10. Actions use the latest published policy and refuse when none is published.
  11. A version's trace holds the ruleset's content hash, the selection, the chosen cup and every
      step's entry, so it explains itself without the ruleset at hand.
  Left for M6: the report read model (`report_view`) and `batch_view`; for M7: `verify`.

## Build — solo sign-off (2026-10-20)

- **D-68 A chemist working alone may sign off their own results, after a delay, labelled solo.**
  Jamie's lab practice: on weekends the one chemist on site makes the report and its decisions, waits
  about 15 minutes (cleaning up), then validates and sends it: fresh eyes on their own work instead
  of a second person. ISO 17025 asks for review and authorization before release, not strictly two
  people; what an auditor wants is a documented rule and evidence it was followed.
  - The policy sets `solo_review_minutes`; None (the default, and the production example) means
    strict four-eyes. The demo policy allows it with 15 minutes.
  - **Review and release only** (Jamie). Accepting a NEEDS_SUPERVISOR result and recording an
    exception stay strictly four-eyes.
  - The chemist asks for it (`solo=True`) with a reason (e.g. "only chemist on site"). It is allowed
    once the delay has passed since the result last changed: its current version, or a decision the
    chemist took on it since (a sign-off doesn't restart the clock). Measured by the database clock.
  - The event is marked `solo` forever; `exceptions(solo=True)` lists every solo sign-off for an
    auditor. Solo sign-offs are not exceptions.
  - Rejected: a retrospective second check on the next working day (Jamie: not needed).

## Build — M6 part 1 (2026-10-20)

- **D-69 Integration choices, slim-data side**, approved by Jamie (item 1 before building, the rest at
  the M6 walkthrough).
  1. **M6 is split** (Jamie). Part 1 changes slim-data only: read models, the adapter, the demo seed
     and the end-to-end test. Part 2 wires slim-data into LabPortal (slim-lims) and needs Jamie's
     go-ahead, since it changes another repo.
  2. The `ReferenceLookup` adapter (`adapters/slim.py`) is built from plain objects and callables in
     slim-domain's and LabPortal's shapes (analysis catalog code → SLIM name → slim-domain ID;
     elements; a `TrSamples` reader the host provides; `SpecificationService.get_specs`), so it
     imports neither. TR samples come from the host until D-45 moves TR v2 into slim-domain.
  3. slim-domain has no analysis → element table, so any known element or ion is a valid analyte.
     Specs are slim-domain's upper limits (floats, read back through `str`, so `0.5` stays `0.5`),
     in a unit given to the adapter; warning limits are not used.
  4. `report_view` shows, per result, the latest released version: the printed text (`12.400`,
     `< 0.050`, `0.120 (estimated)`), ±, recovery, acceptance and exception categories as the
     standard qualifiers (D-11), the spec-limit INFO flags (D-60), and whether it is amended.
     Unreleased results are listed as waiting; rejected ones are neither. Solo sign-off is carried
     for the record, not for printing. Every read takes `as_of`, so an issued report can be rebuilt.
  5. The demo seed (`demo/scenarios.py`) runs each named scenario through the real service from CSV
     files, with ASSERTED personas, its own TR and batch, and dates relative to today. Not seeded: the
     solo sign-off (it needs its real wait) and billing (invoicing's job).
  6. **Parked (Jamie):** teach slim-domain which analytes belong to which analysis, so the adapter
     can refuse e.g. `Cl-` on a metals analysis (item 3 accepts any known analyte until then).

## Build — M6 part 2: wiring into the host (2026-10-20)

- **D-70 Host-side choices for wiring slim-data into LabPortal (slim-lims)**, approved by Jamie before
  building (items 1–4); items 5–8 are build choices for the slice 1 walkthrough.
  1. **Staff roles: a `lab_role` on portal users** (analyst / reviewer / supervisor, stored as `Role`'s
     ints; staff only). `Actor.id` is the user's UUID (what four-eyes compares; stays right if initials
     change), `display` their initials, AUTHENTICATED. The result file's `analyst` column is the staff
     member's initials, now unique. Rejected: initials as `Actor.id` (four-eyes history breaks if
     initials are reused or changed).
  2. **LabPortal requires slim-data**, installed from `git+https://github.com/Firehawk41/slim-data-oss@main`
     (not on PyPI; a bare name could resolve to an unrelated package). Results are core to the LIMS, so
     the tests always run. Rejected: an optional local checkout with skipped tests.
  3. **Three slices, one PR each:** (1) wiring, upload → preview / import → evaluate, batch page and
     API; (2) review / accept / rerun / exception / release screens, `ResultsReleased` → TR status;
     (3) report view + record issue, demo personas (D-50), the demo scenarios seeded in the portal.
  4. **The demo metals ruleset is bound to "10 Elements" by catalog code at publish time.** slim-data's
     own demo uses an invented analysis (`metals_icpms`, ID 1); in the portal, analysis IDs come from
     the SLIM demo seed (real panel names, IDs in alphabetical order), so the seed resolves
     `10_elements_icpms` → its ID by name and refuses to publish if the code is missing; the ruleset
     body stays data. 10 Elements: the smallest real ICP-MS panel, closest to the demo's six elements.
     Rejected: one copy per ICP-MS panel (near-identical rulesets); a catalog code inside the ruleset
     file (cleanest, but changes slim-data's ruleset format: parked beside D-69 item 6).
  5. Only a supervisor may give a lab role above analyst or change one (existing staff start as
     analysts; the CLI sets any role, since it needs server access). Nobody changes their own role.
  6. A file is imported and then **every prep batch it names is evaluated, in one transaction**; if
     evaluation fails (e.g. no published ruleset for the batch's analysis), the whole upload rolls
     back, so readings never sit in a batch nothing can decide.
  7. A TR's samples take readings from received until invoiced (submitted, cancelled and invoiced TRs
     are unknown samples to the adapter). Spec limits come from SLIM's `specifications` (kind
     "specification", exact location), read by the host the way slim-domain's `get_specs` does.
  8. New read `batches()`: the prep batches, newest first, for the host's list (takes `as_of`).
  9. *Same-day revision (Jamie):* **the public demo runs on Postgres only**; SQLite stays for tests and
     quick local runs. Revises DESIGN §2's "SQLite for tests and the demo": on SQLite the demo would
     lose the append-only triggers, slim-domain's hash-chained `audit_log` (so `verify` has no chain to
     check) and concurrent visitors.

- **D-71 TR status follows what happened, not releases** (Jamie). In the portal, "partial report" and
  "complete report" mean a report reached the customer; a release only clears results for reporting.
  1. The first readings imported for a TR's sample move it received → in progress, in the import's
     transaction.
  2. Recording a report issue (`ReportIssued`) moves it to partial report, or complete report when every
     analysis ordered on every sample has results, all released and on an issued report. The host checks
     "ordered" against the TR, since slim-data doesn't know which analytes make up a panel (D-69 item 6).
  3. A release changes results only. Automatic moves go forward only; staff keep the manual status form.
  - Rejected: release drives the status (DESIGN §8.3 as signed off): the TR would read "partial report"
    before any report existed. *Supersedes* DESIGN §8.3's mapping of `ResultsReleased`.

- **D-72 Reason categories are a fixed list in the policy** (Jamie). D-11 prints a standard text per
  category on the report, so the category can't be typed at the bench.
  1. `Policy.reason_categories`: (action, text) pairs, for ACCEPT and RECORD_EXCEPTION only, each listed
     once, in the order the screens offer them. An acceptance or exception must give one of its action's
     texts (`UnknownCategory` otherwise); an action with none listed can't be given at all.
  2. The event stores the text itself (no code), so a report rebuilt `as_of` prints exactly what it did,
     even after a later policy changes the list; no schema change.
  3. Demo and production-example policies carry the same invented wording (D-05): accept: "MDL taken from
     the method validation", "Blank contribution assessed as negligible", "Recovery from a single spike
     accepted"; exception: "Released without rerun", "Insufficient sample for a rerun", "Matrix
     interference confirmed". Changing the list = publishing a new policy (content-hashed).
  - Rejected: free text (wording drifts, the report prints whatever was typed); category codes resolved
    to text at print time (a later policy would rewrite old reports, or needs a snapshot column).

- **D-73 Report issues before the report engine is wired** (Jamie: option B). The Excel report engine
  joins later (LabPortal item D, needs Jamie's go-ahead); issues shouldn't wait for it.
  1. The host shows the report as a page built from `report_view` (released results, latest versions)
     and "Issue report" records those versions with the sha256 of a canonical JSON form of what the
     page shows (internal facts like solo sign-off left out). Nothing extra is stored: `report_view`
     with `as_of` rebuilds it, and the hash proves the rebuild matches. Once the engine renders a file,
     the hash becomes the file's; callers don't change.
  2. Build choices: a report shows every released result (cumulative); issuing again with nothing
     changed is refused; a report carrying a corrected result amends the latest issue automatically;
     any staff member may issue (the release was the sign-off). The TR moves in the same transaction
     (D-71): partial report, or complete report when every ordered analysis on every sample has
     results and none wait; never backwards (an amended report on a complete TR leaves it complete).
     An ordered analysis slim-data never gets readings for (e.g. pH) keeps the TR partial until staff
     close it with the status form.
  - Rejected: wait for the report engine (blocks slice 3); also store the rendered page (duplicates
    what `as_of` rebuilds).

- **D-74 Demo personas are real user rows** (Jamie: option A), the host side of D-50.
  1. The host's users table gets `is_persona`; the demo seed creates the personas from a data file
     (customer, analyst, reviewer, supervisor), each with a random password nobody knows. Everything
     already built works unchanged (four-eyes on the user ID, analyst initials in result files, customers
     seeing only their own TRs); `actor_for` records persona actions as ASSERTED.
  2. Offered only when the host's `DEMO_PERSONAS` flag is on (explicit, not tied to the production
     setting, since the public sandbox runs as production): a "Continue as..." page, a switcher in the
     header and a banner. Personas can't sign in through the password login and can't manage users or
     lab roles; the host has no policy or ruleset screens to restrict.
  3. Billing persona: added with invoicing (D-43); no billing role exists yet.
  - Rejected: session-only personas (every place that loads a user would need a second path).

- **D-75 The demo scenarios are host-agnostic** (Jamie: option A). One copy of the scenario data,
  run by slim-data's own tests and by the host's demo seed.
  1. `run_scenarios(session, host, today)` takes a `DemoHost`: analysis code and ID, the reference lookup
     and staff directory, the analyst (and how files name them), reviewer and supervisor, `new_tr(scenario,
     samples, spec)` for a received TR, and `report_sha256` for what the host's issue would record. The
     host publishes the ruleset and policy first. `seed` keeps the in-memory host (TR00101...).
  2. LabPortal's host makes real TRs (checked in by the supervisor persona, 10 Elements, customer A's
     "Chemical 01", or customer B's when a spec is needed: the OSS demo data's Zn spec), uses the demo
     personas, records issues with D-73's canonical-report hash, then settles each TR's status by D-71.
     Runs once (skipped when results exist).
  - Rejected: a portal copy of the nine scenarios (duplicated data that drifts).


## Build — M7 (2026-10-21)

- **D-76 `verify` takes the audit-chain check from the host** (Jamie: option A). The chain is
  slim-domain's `audit_log`, in the host's database; slim-data never imports slim-domain.
  1. `verify(session, chain_check=None) -> VerifyReport`: replays every stored result version through
     the ruleset version it names, on the batch's readings and exclusions `as_of` the version's
     `created_at`, with the spec limits it snapshotted, and compares every engine-made field. Then it
     runs `chain_check(connection)` (the IDs of entries that don't check out) if the host passed one.
  2. No checker = "chain not checked", never "intact" (`chain_checked` is reported beside `ok`).
     Read-only. Also a test: the nine demo scenarios must verify clean.
  - Rejected: B, import slim-domain when installed (a hidden dependency; the architecture test needs an
    exception); C, copy the hash function (two copies of an integrity check drift).

- **D-77 The host installs slim-domain's audit log** (Jamie). Found while settling D-76: LabPortal
  never called `infrastructure.audit.install`, so the Postgres demo had no chain (D-70 item 9 assumed
  one). `cli migrate` and the demo seed install it on Postgres after both migrations (it covers every
  public table: SLIM's, the portal's, slim-data's), and `cli verify` passes slim-domain's
  `verify_chain` in. slim-domain becomes a required dependency of LabPortal (it was only the bridge's).

- **D-78 Four demo analyses, each with its ruleset** (Jamie). The nine scenarios stay on the same nine
  TRs, spread over 10 Elements, 36 Elements, anions and an acid assay, so the demo shows more than one
  technique. All values invented (D-05).
  1. Rulesets are data: `demo_metals` (10 and 36 Elements share it, bound to each analysis at
     publish), `demo_anions` (F, Cl, NO3, SO4 by IC: duplicate injections, spike, blank; no linear range,
     no scale fault, no direction prior) and `demo_assay` (% w/w by duplicate titration: no spike,
     blank or MDL steps; an unknown MDL is INFO).
  2. **Assay passes only when the two titrations' RSD is under 1 %** (Jamie). For two values the
     sample RSD is |a − b| / (√2 · mean), so RSD < 1 % is RPD < √2 % and `agreement@1` takes
     `rpd_limit` 0.014142 (just under √2 %); a failure, an excluded titration or an undecided choice is
     RERUN. No new step. A triplicate titration would need one (an `rsd` step): parked.
  3. No spec limits for the assay (Jamie).
  4. Mapping: clean pass and scale fault on 36 Elements; cups disagree, pooled spike and accepted on 10
     Elements; below MDL (F) and rerun + exception (SO4) on anions; amended (a titration redone) and
     line by line on the assay. `DemoHost.analyses` maps each demo analysis to the host's code and ID;
     `publish_demo_rules` publishes them all. In LabPortal the anions are bound to "5 Anions" (the lab's
     4 Anions has no F).

- **D-79 Reports print the MDL, not ±** (Jamie). The report shows result · recovery · MDL, the
  columns the lab's report template already has. `ReportLineView` gains `mdl` and `ql`: the
  effective MDL and the QL from the `reported_value` step's trace, rounded *up* to the result's
  decimal places like the "<" figure (D-64), so a report never states a limit lower than the one
  achieved. The ± (D-55) is still computed and kept in the trace; it just isn't printed. No MDL (a
  titration): blank. Panels whose template labels the column QL (the ion panels) print the QL.

- **D-80 The report prints ½Δ only where it bears on a spec limit** (Jamie). ½Δ, half the
  difference between the two cups (D-55), is not a full uncertainty budget, and Jamie calls it by
  that name: "½Δ" on the staff screens and "half delta" on the customer's report, with what it means
  written next to it. ISO 17025 §7.8.3.1 (c) asks for it where it affects conformity with a spec
  limit, so the report shows it when the result is outside the limit or within ½Δ of it.
  1. `ReportLineView.half_delta`: the rounded ½Δ when that holds, else None. Compared on the
     unrounded value and ½Δ from the trace (D-54). `uncertainty` stays the stored ½Δ, always.
  2. No ½Δ: no spec, limits in another unit, one cup chosen or below MDL (D-55 has none).
  3. A view rule, not ruleset data: it reads only what the spec_limits and uncertainty steps traced,
     and has no threshold of its own.
  4. The host's report explains MDL, QL and half delta wherever it prints them.
  5. Demo: cups_disagree also reports Fe 0.295 against a 0.3 limit (in spec, but ½Δ 0.010 reaches
     the limit), so the demo shows a half delta (Jamie).
  - Rejected: always print it (it reads as ±, which it isn't); never print it (D-79) loses it where
    a customer needs it to judge a pass.

- **D-81 The batch view shows every cup** (Jamie). Staff reviewing a batch see what the engine saw and
  chose for each result, beside the buttons that override it: `BatchResultView.cups` (`CupsView`)
  with the dilution-corrected cups, whether they agreed, the cup believed and the rung that decided
  (D-31), recovery and its basis (D-38), the blank, MDL, QL and ½Δ (always, for staff). Read from the
  current version's trace, so it is exactly what decided; the full trace stays one call away
  (`trace`). Not the quality layer: trends and control charts across batches stay parked.

- **D-82 The host is folded into `slim-lims-oss`; the mirror is retired** (Jamie, 2026-10-24).
  Supersedes D-41, and D-40's legacy router. slim-lims-oss was an anonymised mirror of the lab's
  private slim-lims, kept so the portfolio followed its features; the host app now goes well beyond
  that brief, so the mirror ends.
  1. LabPortal's history is merged into slim-lims-oss (an unrelated-histories merge, so every commit
     is kept) and its tree replaces the mirror's; the package `slim_lab_portal` becomes `slim_lims`.
  2. The VBA report API (`/generate-report`, `/resolve/*`, Windows service scripts) is not ported: it
     needs the lab share and Excel, which a web demo has neither of. It stays in git history and, in
     production, in the private slim-lims, which is never changed from here.
  3. The LabPortal repo is archived, not deleted, so its pull requests stay readable (D-41's reason).
     Render's Blueprint is reconnected to slim-lims-oss; the private sync into it stops.
  - Rejected: D-41's rename swap (keeps the mirror alive for no reader); leaving it as LabPortal.

- **D-83 Invoicing V5: a library in `SLIM-Invoicing-Program/V5-LIMS-Module`** (Jamie, 2026-10-24).
  Carries out D-43. V1–V4 each answered the one before; V5 answers V4's limits: it read Excel forms
  from disk and kept its own copies of customers, chemicals and analyses.
  1. A subfolder like V1–V4 (Jamie), named without spaces so the host can install it with
     `#subdirectory=V5-LIMS-Module`; its own CI job. Package `slim_invoicing`.
  2. Ported from V4 unchanged in behaviour: quotes (customer quotes first, then defaults; most
     analyses per line first; bulk price from the line's minimum), the pricing engine and the
     accounting CSV. Dropped: the Excel parsers, the file loader, the copied reference data.
  3. Input is a `BillableRequest` the host builds from its TR and slim-data's report issues; the
     library never imports the host or slim-data. Its own tables (quotes, sales orders) with their
     own Alembic version table; IDs are slim-domain's, with no foreign keys (portable schema).
  4. **Only a complete report is invoiced** (Jamie). Bills each ordered analysis of a sample that a
     report issue showed, once: reruns, failed attempts and amendments are never billed twice, and
     an analysis never reported is never billed. One sales order per TR.
  5. Slices: (1) the library; (2) the host: "Invoice" on the board's To invoice column creates the
     sales order and moves the TR to invoiced in one transaction, an invoice page and the CSV
     export; (3) demo quotes in the seed, paid.
  - Parked: pricing additional elements (V4 didn't either); chemical-specific quote lines.
  - Rejected: a new repo (breaks the V1–V5 story); reading slim-data from the library (couples two
    libraries; the host already owns both).

- **D-84 Invoicing prices the lowest total for the customer** (Jamie, 2026-10-24). Changes V4's
  pricing rule in V5 (D-83 point 2).
  1. The bulk price is decided on the merged quantity: groups priced on the same quote price become
     one line, and that line's total meets the bulk minimum (6 × 36 Elements and 6 × 36 Elements +
     pH are 12 × 36 Elements at the 10+ price). V4 decided it per group, before merging.
  2. Of every way to cover the groups exactly with quote lines (a bundle or its parts), the engine
     takes the lowest total; ties go to fewer lines. V4 always took the widest line first.
  3. Kept from V4: the customer's own quotes come before the defaults, which price only what the
     customer's quotes don't, even when a default would be cheaper.
  4. The search is exhaustive (a TR has a handful of groups and covers); above 10,000 combinations
     each group takes its own cheapest cover.

- **D-85 The ecosystem roadmap: four departments, as labels** (Jamie, 2026-10-24). The plan lives in
  slim-lims-oss `docs/ROADMAP.md`.
  1. Modules are grouped into **Admin, Lab, Quality and IT**, for organisation and explanation only:
     no repo, package or import follows them.
  2. Module rules: each module a library with its own tables and version table; modules never import
     each other (slim-domain aside); the host composes and draws the screens; a new module needs a decision.
  3. Portfolio: the slim-lims sandbox is the crown jewel; slim-data, invoicing V1→V5 and slim-quality
     are featured on their own.
  4. Order: phase 0 housekeeping; then prep (Lab), quality trends (Quality), the landing page, samples
     and report delivery, quality records, admin screens, and the VPS last.
  - Rejected: departments as code (repo or package per department: churn, no runtime gain); six
    departments (front office, sample management, reporting split out: harder to hold in one's head).

- **D-86 Samples and prep are two modules** (Jamie, 2026-10-24; the roadmap's open question). `slim-prep`
  (phase 1, repo `slim-prep-oss`) holds bench prep: prep batches, weights and volumes, spikes with
  standard lot and expiry, balance and pipette, the run list; it hands the batch to slim-data's batch
  API. `slim-samples` (phase 4) holds custody: receipt condition, labels, storage, retention, disposal.
  The prep book's steps and columns come from Jamie's description, invented for OSS.
  - Rejected: one "lab operations" module (saves a repo; blurs two sets of users and records).

- **D-87 QC sample roles: check standard and CRM** (Jamie, 2026-10-25, as proposed). Phase 1's
  first slice; prep feeds it later and quality trends read it.
  1. Two new `ReadingRole` members: `CHECK = 4` (check standard / CCV, a solution of known
     concentration, measured on the instrument) and `CRM = 5` (certified reference material, prepped
     like a sample). Like BLANK (D-64) they have no sample: they belong to the prep batch; the CSV's
     `lab_sample_id` reads `CHECK` or `CRM`.
  2. The expected value is batch data, as spike amounts are: a new append-only table
     `prep_batch_qc` (batch, role, analyte, expected value and unit, material and lot as text), CSV
     column `expected` on CHECK / CRM rows. slim-quality's reference-material register (phase 5) can
     supply it later; until then the file or slim-prep does.
  3. A new step `qc_check@1`: recovery = measured ÷ expected per role and element, judged against a
     window in the ruleset (demo: ±25 % for both, 75–125 %, as Jamie set the same day; first
     drafted as CHECK 90–110 %, CRM 85–115 %). A failure flags every
     result of that element in the batch (`CHECK_FAILED`, `CRM_FAILED`; the ruleset gives the
     severity: demo CHECK → RERUN, the run is suspect; CRM → NEEDS_SUPERVISOR, a matrix effect can
     explain it); `CHECK_MISSING` / `CRM_MISSING` only when the ruleset says the role is required. The
     trace keeps measured, expected and recovery: that's what the control charts plot.
  4. Batch-wide, not bracketed: readings have no run order yet. Bracketing (a CCV judges only the
     samples run since the last good one) waits for slim-prep's run list.
  5. Existing rulesets and results don't change (new step, new ruleset versions; nothing is edited, D-04); the demo rulesets get
     new versions that add `qc_check`, and the demo scenarios add QC rows.
  - Options weighed: a separate QC reading type (a second path through import, persistence and replay,
    as D-64 rejected for blanks); expected values from a reference-material table in slim-data (that
    register is Quality's, phase 5); expected values typed into the ruleset (they change per lot, not
    per method).

- **D-88 Instrument data in: what to mine, optional fields, how it came in** (Jamie, 2026-10-26, as
  proposed but for point 7's demo). Phase 1's instrument readers. The test for every field: a step
  judges it, a chart plots it, a person acts on it, or an auditor asks for it. Storing more is cheap;
  the cost is upkeep (parser surface, the §7.11 validation pack, screens nobody reads, rules that
  grow because the data is there).
  1. A reader `qtegra/1` for Thermo Qtegra's SpreadsheetML export (per LabBook). It takes:
     concentrations; each reading's replicate RSD; the internal-standard recovery (%) per reading;
     calibration per analyte and run (R² or r, LOD, BEC); the sample list (run order, start time,
     user). It leaves out raw intensities, survey, instrument status, regression coefficients and
     the noticeboard. Qtegra's own QC pass/fail is not taken: its QC rows come in as CHECK readings
     and `qc_check@1` judges them (one judge, D-87). Sweeps are not imported: the analyst drops an
     outlier sweep in Qtegra, where they can see it, and the export's mean and RSD follow.
  2. The original file is kept beside its run (today only its hash is, `runs.source_sha256`), so a
     field left out now is never lost: a later reader version can mine it.
  3. Two optional fields on `Reading`: `rsd` and `istd_recovery`. Blank means no evidence, not a
     fault: the steps that use them stay silent. A ruleset may mark either as required (as D-87
     does for CHECK / CRM); then a blank raises `RSD_MISSING` / `ISTD_MISSING`. Existing readings
     simply have none (D-04).
  4. **Suggestions, not verdicts.** A new step `precision@1`: `RSD_HIGH` above a ruleset limit,
     applied only above a multiple of the MDL (near the MDL counts are low and a high RSD is
     normal: zones, as `agreement` has, D-28); `ISTD_LOW` / `ISTD_HIGH` outside a window. Demo
     severities INFO / WARN: shown with their reason and numbers in the trace, never blocking
     (D-56). A lab that wants a hard rule raises the severity in its ruleset. Calibration below its
     limit is a rule, not a suggestion: `CAL_FAILED`, demo RERUN, on every result of that analyte
     in the run.
  5. `cup_choice@2`: one new rung, *instrument precision*, after analyst exclusion and scale fault.
     When the cups disagree and one cup's reading is unstable (RSD high) or suppressed (internal
     standard low) and the other's is not, the stable cup is believed. Without the fields, the rung
     is inconclusive and the ladder goes on as in `cup_choice@1`. The analyst's exclusion stays first:
     no suggestion overrides a person (D-31).
  6. **Paste is a reader too.** `paste/1` reads a table copied from Excel (tab-separated), with the
     same columns and checks as `generic_csv/1`, so a lab with any instrument, or none wired, can
     work as it did in Excel. Qtegra is never enforced.
  7. **How it came in is recorded and is policy.** Every run already names its reader and who
     imported it; the run also gets its source kind (instrument file / CSV / paste). The policy
     lists the kinds the lab accepts and which need a second person's check before evaluation
     (pasted numbers can carry transcription errors; an instrument file is stronger evidence). Demo:
     all three accepted, none needs a check: the analysts are trusted, and the raw data stays on the
     instrument computer for audits (Jamie; first drafted as paste needing a second check).
  8. Run order (from the sample list) is stored but not judged yet: bracketing (D-87 point 4) and a
     carryover suggestion (a low result straight after a very high one) come later.
  9. Demo data is invented in Qtegra's layout (headers, Mean / RSD / SD rows); never a real
     LabBook (D-05).
  - Options weighed: raw intensities for every analyte (nobody acts on them; micromanaging);
    Qtegra's QC verdict alongside `qc_check` (two judges disagree); importing each sweep and judging
    outliers in slim-data (takes the call from the analyst, who sees the sweeps in Qtegra); RSD as a
    hard rule (trace-level results would all flag, and analysts learn to ignore flags); Qtegra
    only (ties a LIMS to one vendor); paste with no provenance (an auditor can't tell typed numbers
    from an instrument's).
  - Later, slim-quality counts how often each suggestion is overridden: that's how a guideline is
    tuned, without policing anyone.

- **D-89 How `qtegra/1` reads a Qtegra export** (Jamie, 2026-10-26, as proposed).
  Worked out from Jamie's full SpreadsheetML export of one LabBook (a real run: read for its layout
  only, never committed; the fixtures are invented in the same shape, D-05). One sheet: four header
  rows (block, sweep, isotope with mode, unit), then one row per measurement in run order.
  1. **The scheme to save in Qtegra ("SLIM"):** Sample list (user name, start / stop time, the iCAP
     node: label, comment, Sample Type, Dilution Factor, QC Action, Standard), Concentrations
     (`ExtCal.Average`, `ExtCal.RSD`), Quality Control (`ExtCal.QC.ReferenceAmount`), Calibration
     (`ExtCal.CorrelationCoefficient`, `ExtCal.CoefficientOfDetermination`, `ExtCal.LOD`,
     `ExtCal.BEC`). The reader finds blocks by header name, never by column number, and ignores
     every other block, so a fuller export reads the same.
  2. **Values:** `ExtCal.Average` and `ExtCal.RSD`. They respect the sweeps the analyst or Qtegra
     left out (shown blue in the file), where `MeasuredConcentration.Average` doesn't. Sweeps and
     Qtegra's cell colours are not read: slim-data judges for itself (D-88).
  3. **Internal standard:** a column whose `ExtCal.Average` unit is % (e.g. 103Rh). Its value is the
     recovery, stored on every reading of that row; with several, the lowest. Never a reading itself.
  4. **Rows by Sample Type:** BLK (calibration blank) is skipped; STD gives the calibration only; QC
     with QC Action CCV is a CHECK reading per element, expected = `QC.ReferenceAmount`, material =
     `Standard`; UNKNOWN is identified by its **Comment**: `TR00012-1 A` (sample, cup A = replicate 1,
     B = 2), `BLANK` (the batch's method blank). An UNKNOWN with no comment (e.g. a blank
     verification) and a row with no values (an aborted measurement) are skipped and listed at
     preview. The bench labels stay as they are. CRM and spike rows wait: their expected value and
     spike amount aren't in Qtegra (prep data, slim-prep).
  5. **Analytes:** `65Cu (CP-Off-None)` → Cu. Two isotopes of one element in a file is a file error
     until a lab needs a choice.
  6. **Calibration** (r, R², LOD, BEC per analyte, once per run) is stored with the run, for
     `CAL_FAILED` (D-88) and the quality charts.
  7. **MDL = the run's `ExtCal.LOD`** for that analyte: from that day's calibration blank, the same
     for every sample in the run. Not `ExtCal.MQL`, which Qtegra works out per sample. A lab that
     wants its validated MDL as a floor gets a ruleset option later.
  8. **Analyst = whoever imports** (the file's user name is the instrument PC's shared login); the
     preview lets them name someone else. Instrument = the iCAP node's name (`iCAPevoRQ`). Prep batch
     and analysis are chosen on the import screen (reader options), since Qtegra doesn't know them.
     `analyzed_at` = the first start time.
  9. **Dilution:** stored as Qtegra's Dilution Factor, and the value is divided by it, because
     slim-data keeps the instrument reading before dilution (`linear_range` compares that, D-59).
     Qtegra multiplies its concentrations by the factor (Jamie confirmed; the export had 1 on
     every row, other analyses won't).
  - Options weighed: the label for the sample ID (bench labels change; Comment is free); matching
    labels by hand at preview (transcription again); `MeasuredConcentration.Average` (ignores
    excluded sweeps); Qtegra's MQL or a method MDL (per-sample, or no table for it yet); Qtegra's QC
    recovery % (two judges, D-87); columns by position (breaks when the scheme changes).

- **D-90 The demo's precision and calibration limits** (Jamie, 2026-10-27). D-88 point 4's numbers,
  as ruleset data; two steps rather than one, since a calibration belongs to a run and RSD and
  internal standard to a reading.
  1. `precision@1`, per cup: `RSD_HIGH` when the RSD is above 10 %, judged only when the cup's value
     is at least 10 × its effective MDL (MDL × dilution factor, D-54); below that, or with no MDL,
     not judged (the trace says so). `ISTD_LOW` / `ISTD_HIGH` outside 50–150 % internal standard
     recovery. Demo severities WARN: suggestions, never blocking (D-88). `RSD_MISSING` /
     `ISTD_MISSING` only when the ruleset marks the evidence required (demo: not).
  2. `calibration@1`: `CAL_FAILED` when the run's R² is below 0.995 for the element, on every result
     whose readings came from that run (demo RERUN). A run with no R² for the element isn't judged.
     R², not r: the figure Jamie's lab reads.
  3. To link a reading to its run's calibration, `Reading` gains its `run_id` (stored since M3), and
     `Calibration` its run. The engine input carries the batch's runs' calibrations.
  4. The demo metals ruleset gets a new version with both steps (D-04: published results keep the
     version they were made with).

- **D-91 Where the MDL comes from: the run's calibration, or the lab's specified values** (Jamie,
  2026-10-27). The lab's practice: an MDL is worked out with every calibration (fresh, the default);
  a lab may instead set an MDL per element from a study. A dilution raises the MDL by its factor
  either way (effective MDL = MDL × dilution, D-54).
  1. A step, `mdl_source@1`, first in the ruleset: `mode` "calibration" (each reading keeps the MDL
     its file gave: Qtegra's LOD for the run, D-89, or a CSV's column) or "specified" with a table
     of MDLs per element and unit. Switching is a new ruleset version: the record shows when and to
     what, and published results keep the MDL they were made with (D-04).
  2. Under "specified", the step gives each reading the table's MDL before any other step reads it;
     the reading as stored never changes. An element missing from the table, or in another unit,
     falls back to the calibration MDL (Jamie); the trace says which source each result used.
  3. A step, not a new ruleset field: a field would change the content hash of every stored ruleset,
     and `verify` would refuse them (D-76). Without the step, a ruleset works as before, which is
     "calibration"; so the demo rulesets don't list it, and a lab switches by adding it with its
     table. Steps may now declare they must run first; the ruleset is refused otherwise.

- **D-92 `cup_choice@2`: the instrument-precision rung** (2026-10-27, building D-88 point 5 as signed
  off). When the cups disagree, a new rung `instrument_precision` compares the two cups' own
  instrument evidence:
  1. A cup is *unstable* if its RSD is above the limit (judged only at or above the multiple ×
     effective MDL, as `precision@1`) or its internal standard is outside the window; *stable* if at
     least one of those was judged and passed; *unknown* with no evidence (a CSV).
  2. Exactly one unstable and the other stable → believe the stable cup (`CUP_CHOSEN_BY_PRECISION`,
     demo WARN). Anything else is inconclusive and the ladder goes on, so a file without the
     evidence is decided exactly as by `cup_choice@1`.
  3. The analyst's exclusion and a scale fault still decide first; the rung's place among the
     per-element rungs is the ruleset's (demo: first, as D-88 says, before the spike witness).
  4. Its figures are the rung's own params (demo: D-90's numbers), not read from `precision@1`:
     released steps don't change, and a lab may suggest on one threshold and decide on another.
  5. `cup_choice@1` stays for replay; the demo metals ruleset moves to @2.

- **D-93 A dilution factor below 1** (Jamie, 2026-10-27, for slim-prep). An evaporation concentrates the
  sample (e.g. 10 g made up to 1 g: factor 0.1), so a reading's dilution factor only has to be positive.
  Value and MDL scale by it as before (D-54). `qtegra/1` and `generic_csv/1` take it; 0 or less is refused.
  - Rejected: a separate concentration factor (two names for one number).

- **D-94 The MDL of the cups the result kept** (Jamie, 2026-10-27). The lab's MDL is IDL × the average
  dilution of the sample's cups; Jamie: average only when both cups are kept. New step
  `reported_value@2`: when the cups agree, the effective MDL is the mean of both cups' (MDL × average
  dilution when the MDLs match); when one cup was chosen, that cup's own. The trace says which
  (`mdl_from`). Later steps (method blank, spec limits) see the kept MDL.
  1. Agreement and cup choice still use the larger effective MDL (`cup_shape@1`): nothing is kept yet,
     and the larger is the cautious one. Changing them would need new versions of three steps.
  2. `reported_value@1` stays for replay; the three demo rulesets move to @2. With equal dilutions
     (every demo result so far) nothing changes.
  - Rejected: the larger MDL as today (overstates the limit when one cup was diluted more and kept the
    other); averaging always (a cup set aside shouldn't lower the limit).

- **D-95 slim-prep hands its batches over; the prep book's dilution wins** (Jamie, 2026-10-27, as
  proposed; slim-prep P-11, P-12).
  1. "Send to results" in slim-prep registers the batch here before the run: samples, prep method,
     spike amounts (in the analysis's unit) and each cup's dilution. The instrument file then brings
     only readings, as for a batch registered by an earlier file (D-66).
  2. Cup dilutions are batch data, as spike amounts are: a new append-only table
     `prep_batch_dilutions` (sample, CUP or SPIKE, replicate, dilution). On import a prepped cup takes
     the prep book's dilution. The file's factor may be left at 1 (not given); any other value must
     agree, or the row is refused (a sequence mix-up), and with it the import (all-or-nothing, D-24).
  3. A batch no file came from slim-prep for works as before: the file's factor.
  4. Later slice: a prep correction after handoff (new dilution or spike amount) → slim-data
     re-evaluates; released results go through the correction flow (amended report, D-69).
  - Rejected: a warning only on disagreement (a mix-up would reach a report); slim-data asking
    slim-prep through a port at import (two modules in one import; the batch row already has a home).

- **D-96 A prep correction after handoff** (Jamie, 2026-10-27, as proposed: D-95 point 4).
  1. Cup dilutions and spike amounts are versioned (migration 0008 for spikes; 0007 for dilutions, amended
     the same day before release): `correct_prep` appends the next version of each changed entry with a
     reason and who. `get_batch` reads the newest version as of any moment.
  2. The prep dilution is applied when the batch is read for evaluation, not written into the readings:
     `evaluation_input` gives each prepped cup's reading the prep book's dilution as it stood. Readings
     stay as imported (D-04), and `verify` replays every version as of its making.
  3. The host then re-evaluates: drafts get a new version; released results it changes are reported as
     `released_affected` and go through the correction flow (D-69, amended report).
  4. Re-exports compare the stored readings as the prep book now says, so a correction doesn't make a
     re-export look different.
  - Rejected: re-importing the run (duplicate readings, and the file isn't what changed); editing the
    stored readings (append-only).

- **D-97 Check standards bracket the run; the rule is the lab's; an analyst may overrule** (Jamie,
  2026-10-27; D-87 point 4).
  1. Each reading keeps its line in the run's file as its run order (`readings.sequence`, migration
     0009); a run re-exported while it grows is one run (by run code). Readings imported before keep
     none and are judged batch-wide, as by `qc_check@1`.
  2. `qc_check@2`, per role: the window, `judge` = `each_element` (default: each element on its own,
     the sounder science, as Jamie holds) or `average` (one check passes or fails on the mean recovery
     of its elements, and the verdict applies to all; some labs' rule), and `bracket`.
  3. Bracketing (CHECK only): a sample's reading counts only if the check standards just before and
     just after it in its run both passed. A failed check flags the samples on either side of it
     (CHECK_FAILED, naming the check's line and run); a reading with no check before or after it is
     CHECK_NOT_BRACKETED (demo NEEDS_SUPERVISOR). A CRM is prepped like a sample: batch-wide.
  4. Overruling, auditable: a policy role rule may name a flag (`RoleRule.flag`): it applies when the
     flags behind the verdict are all that code. Demo: an analyst may record an exception on a failed
     check standard and accept a result that isn't bracketed, with a reason category and a note;
     four-eyes still keeps the analyst who ran it off their own results. The policy is versioned,
     published data: the quality section's to set.
  5. `qc_check@1` stays for replay; the demo metals and anions rulesets move to @2 (bracketing on).
     The demo scenarios put each check standard before and after their samples, as a real run does.
  - Rejected: bracketing from slim-prep's planned run list (the file is what was run; P-21); judging
    the average by default (one bad element can hide behind good ones).

- **D-98 The run calibrations reach the evaluation** (Jamie, 2026-10-27; a bug found building D-97).
  `evaluate_batch` rebuilt the engine's input without the run calibrations, so `calibration@1` (D-90)
  never judged in the app (the engine tests passed them directly, so they didn't catch it). Fixed:
  they go in. A version made with them says so in its trace (`"calibrations": true`); `verify` replays
  each version as it was made, with them only when its trace says so, so versions from before the fix
  still replay exactly. The next evaluation gives a draft from a run with a calibration a new version;
  a released one it changes is reported (`released_affected`) for the correction flow.
  - Rejected: replaying old versions with the calibrations (they would all "differ": the record must
    say what was decided then, D-21).

- **D-99 A TR's status follows the facts** (Jamie, 2026-10-28; architecture review A2). Hand-set statuses
  were a carry-over from before the modules were wired.
  1. *Partial / complete report* come only from issuing a report, *invoiced* only from sending the
     invoice (`fact=True` from those actions). A person can't set them by hand (409, saying how they
     come); the request page no longer offers them. The LIMS token (spec §12) issues reports itself, so
     it may still set the report statuses.
  2. Cancelling asks the modules first: refused once a report was issued or a result released. Otherwise
     it goes through, and the modules take no new work for the TR: no readings (`TrSampleTables`), no
     decisions or releases on its results, no prep batch sent.
  3. *In progress* stays a hand move as well as automatic (first readings, D-71): analyses without a
     module still need it. Re-check which statuses are still hand-set each time a module is wired
     (Jamie's rule).
  - Rejected: cancelling everything downstream (released results and issued reports are facts; a
    cancel can't unsay them).

- **D-100 Managing users is a capability** (Claude, Jamie amended, 2026-10-28; review A1). Any staff member
  could create users and deactivate anyone. Now:
  1. *Manage users* is a capability (`slim_lims/capabilities.py`). By default a lab role brings it
     (supervisor); the lab may grant it to a person too (an analyst who is the LIMS specialist), in
     `user_capabilities`.
  2. Only someone who holds it by their lab role grants or removes it. Nobody acts on someone with a
     higher lab role, or gives a lab role above their own.
  3. Demo personas never manage users (they are chosen, not logged in, D-74).
  - Rejected: supervisors only, fixed (Jamie: the lab decides); a full role/permission editor (more
    than the demo needs; capabilities are data and can grow).

- **D-101 Small hardening from the review** (Claude, agreed by Jamie, 2026-10-28; review A4, A7, A8, A10).
  1. *Login attempts* (host): 5 failures per account or 20 per address in 15 minutes lock that login
     until the oldest ages out (429, the password isn't checked); a success clears the account's count.
     In memory, per process: enough for the demo's one process.
  2. *The lab's clock* (host, `labtime.LabClock`): the prep screens' "today" and the prep time are the
     lab's (`LAB_TIMEZONE`), and a batch sent by API is put in the lab's zone, because slim-prep dates the
     prep by it (a standard's expiry); an evening's batch is no longer dated tomorrow by UTC. Instants are
     stored as they are. The demo seed uses the same clock.
  3. *A DOCTYPE in a Qtegra file* was already refused by a byte check; the check moved into expat
     itself, so a UTF-16 file can't slip one past it.
  4. *Reports*: a test that both renderers escape typed text; the CSP stays the backstop.
  - Rejected: a shared limiter store (Redis) for login attempts (one process; more moving parts than
    the demo needs).

- **D-102 A lab per visitor** (Jamie's requirement, Claude's mechanism, 2026-10-28; review A3). The public
  demo was one shared database anyone could fill with junk; Jamie: every visitor starts fresh, and no
  visitor sees another's.
  1. A visitor's first persona gives them a lab of their own: a Postgres schema `lab_<16 hex>` seeded
     with the whole demo, reached through `search_path`, so every module runs unchanged. Switching
     persona keeps the lab; "Start again with a fresh lab" drops it; an hour idle drops it too.
  2. Seeding takes ~3 s of CPU (measured; Render's free plan has a tenth of a CPU, so ~30 s), so two
     spare labs are kept seeded in the background and a visitor takes one at once. The registry
     (`public.demo_labs`) outlives restarts; at most 25 labs (the least recently used goes first).
  3. In labs mode public keeps no app tables (a lab would see them through its search path): the shared
     lab, for password logins and the CLI, is `DATABASE_SCHEMA` (e.g. `lab_shared`). The app refuses to
     start otherwise; `cli empty-public --yes` clears it once.
  4. Each lab has its own audit chain. slim-domain lists only public's tables to audit, so the host
     hands it the lab's (parked for slim-domain: list `current_schema()`'s).
  - Rejected: a reset on a timer (two visitors at once still share); a database per visitor (needs
    CREATEDB, not given on the free plan); cloning a template schema in SQL (sequences, triggers and the
    audit chain make it fragile; seeding is what the demo already trusts).

- **D-103 Customer scoping fails closed** (Claude, agreed by Jamie, 2026-10-28; review A6). Listing TRs
  took `customer_id=None` to mean "every customer"; a customer was kept to their own only because the
  database requires their ID. Now every listing takes a `Scope` (no default), made by
  `Scope.for_user`: a customer always gets their own customer, whatever they asked for; "every
  customer" is staff's (and the slim-lims token's); a customer user without a customer raises (403),
  never sees all. A test spies on each listing route a customer reaches.
  - Rejected: keeping the check in each route (the next route forgets it).

- **D-104 A TR's history is kept whole** (Claude, agreed by Jamie, 2026-10-28; review A5). The host's TRs
  were updated in place: on SQLite nothing recorded what a TR said before, and on Postgres only the audit
  log did.
  1. Every change (submitted, a chemical matched, checked in, each status) appends the whole TR as it now
     stands to `tr_versions` (append-only triggers, migration 0010), with when, what and who. The other TR
     tables stay as the current state, for reading fast.
  2. `as_of(tr, time)` reads the TR as it stood; the request page lists its versions and opens any of
     them read-only. TRs from before the history get their state as version 1 (`cli migrate`, the seed).
  3. The newest version equals the current tables (tested), as slim-data's and slim-prep's records do.
  - Rejected: change events replayed into a state (Claude had first proposed this; versions kept whole read
    back with no replay code, as the modules' records do); keeping it in the Postgres audit log only.

- **D-105 A batch the prep book registered takes no new samples or spikes from a file** (Claude, agreed by
  Jamie, 2026-10-28; review B5). A file may still add samples and spike amounts to a batch a file
  registered (a run exported while still going, D-66). A batch slim-prep registered (it has prep
  dilutions, D-95) is the prep book's: a row for a sample not in it, or a spike amount it doesn't record,
  is refused at preview. QC expectations still come from the file (the prep book doesn't record them).
  The prep book's own checks on the slim-prep side: P-23…P-27 (slim-prep-oss `docs/DECISIONS.md`).
  - Rejected: letting the file add and flagging it (the prep book is the record of what was prepped).

- **D-106 Instrument conditions are kept** (Jamie, 2026-10-29; Claude found the gap). `qtegra/1` dropped
  the mode from `65Cu (CP-Off-None)`, so cool and hot plasma (completely different conditions) were one
  series. Readings and calibrations get an optional `conditions` label (migration 0010); `qtegra/2`
  keeps each element's mode, `qtegra/1` is unchanged. A run may mix modes across elements; the quality
  charts keep every mode a series of its own. One element in two modes in one run stays a file error.
  The host imports Qtegra exports with `qtegra/2`.
  - Rejected: modes as part of the analyte (results would split by mode; nobody asked for that); a
    channel model with isotopes, wavelengths and a reported-channel step (too deep for now, Jamie).

- **D-107 slim-quality reads slim-data's QC history through the host** (Claude, agreed by Jamie,
  2026-10-29). `service.quality.qc_history(since, as_of)`: every blank, check standard and CRM reading
  with its run (instrument, time, run order, conditions), recovery (measured × dilution ÷ expected, as
  `qc_check` works it out) and exclusion, plus every run's calibration; one query, read-only. The host
  adapts it to slim-quality's `QcSource` port; modules never import each other (D-85).
  - Rejected: slim-quality reading slim-data's tables (couples two modules' schemas).

- **D-108 QC-only batches** (Jamie, 2026-10-29, as recommended). The demo needs months of QC stored for
  real, and an instrument's own QC run (calibration, check standards, blanks) has no customer samples. A
  prep batch may now have no samples: it evaluates to no results, `batches()` marks it `qc_only`, and the
  host keeps it off the results list and the board; its readings feed the quality charts.
  - Rejected: an internal "QC history" TR riding along in every batch (a hundred demo batches in the
    results list, a filter to keep it off the board); history generated on the fly (charts showing data
    in no record).

- **D-109 slim-quality, a new module** (Jamie, 2026-10-29). Roadmap phase 3, repo `slim-quality-oss`.
  1. **Split:** part 1 (now): QC charts, a toolbox of trend and forecast tools, change control, events,
     notes, signed QC reviews. Part 2 (phase 5): the audit tools (NC/CAPA, PT, a one-click vertical audit,
     uncertainty from QC, management review pack, readiness by clause, the §7.11 pack). Part 3 (parked):
     the tools' warnings on the review screen before release.
  2. **Not read-only after all:** it writes its own append-only tables (change requests and decisions,
     profile versions, limit sets, events, notes, reviews); it never changes a slim-data result.
  3. **Roles:** two capabilities, *quality* (sees everything, records events and notes, proposes) and
     *quality manager* (also decides changes and signs QC reviews), granted per person by whoever manages
     users (D-100); a quality persona of each (QM, QU; lab role analyst). A manager may approve their own
     proposal (a lab may have one): allowed, and shown as self-approved.
  4. **Library rules as slim-data's:** `domain/` and `monitors/` pure; tools `name@version`, never edited;
     own Alembic version table; SQLite + Postgres; 95 % branch coverage on domain + monitors.
  - Rejected: one tool (Westgard only; Jamie: "going in with one tool seems like the wrong idea");
    industry presets and industry-only tools (Jamie: keep it generic).

- **D-110 The toolbox, the profile and the limits** (Claude proposed, shaped by Jamie, 2026-10-29).
  1. **Series** keyed generically: instrument × analyte × conditions × measure × QC material × level.
     Measures: check and CRM recovery, method blank, internal standard recovery, check RSD, calibration
     LOD / BEC / r.
  2. **Tools:** Westgard (each rule toggleable: 1_2s, 1_3s, 2_2s, R_4s, 4_1s, nx), EWMA, CUSUM, a
     one-sided limit with an exceedance rate (blanks are skewed by contamination), a time-to-limit
     forecast (a line through the newest points: when it leaves the acceptance window), and a shared
     shift across instruments (a reagent or standard, not one instrument).
  3. **The profile** (policy data, versioned) switches tools on per measure, sets their settings and each
     flag's severity, and per measure the provisional-limit method and the acceptance window (starter:
     recovery 75–125 %, D-87). **Backtest** shows what a candidate would have flagged before it's proposed.
  4. **Limits** are set once and approved, never recomputed as data arrives (rolling limits absorb a
     drift): from a baseline (mean ± SD, or median ± 1.4826 × MAD so contamination spikes don't widen
     them) or entered (a certificate). Until approved, provisional limits from the first 40 points,
     drawn dotted. A new limit set starts a new track: no rule's run spans two baselines.
  5. **Charts** are SVG drawn on the server: no script (the CSP), and they print.
  6. **Demo history:** 90 days of invented QC on two ICP-MS instruments (Fe and Zn in KED, Na in cool
     plasma), imported through slim-data as `qtegra/2` files into QC-only batches; planted: cone wear on
     ICP-MS 1 (internal standard sags, checks creep up inside ±25 %, Na's BEC rises), a Zn acid lot on
     both, Na contamination spikes, a maintenance event, and one open change for a visitor to decide.
  - Rejected: percentile upper limits (too noisy from a 20–40 point baseline; centre + 3 SD on robust
    limits instead); limits recomputed as data arrives.

- **D-111 Where a sample should be: parked** (Jamie, 2026-10-29). A view of each sample against its
  turnaround (a stage plan per processing time: where it should be now, on track / at risk / late, a
  queue by slack, a forecast from stage durations) is worth having: the live view on the host's board,
  the after-the-fact statistics in slim-quality. Parked; questions for later: the stages, whether a hold
  pauses the clock, a holiday calendar, rush clocks from the time of receipt.

- **D-112 What an auditor reads from slim-data** (Claude, agreed by Jamie, 2026-10-30). Read-only
  `service.audit`: `report_audit(issue)` gives each line's result version with everything behind it:
  the readings the engine saw when it made the version (`as_of`, so later imports don't leak in), their
  runs (file, hash, reader, analyst, importer, whether the file is kept), the run's calibration, the
  batch's blanks and QC for that analyte with recoveries, the ruleset (name, id, content hash), the events
  on that version, and a fresh replay (`verify.replay_version`). `issues_between(start, end)` lists reports
  to pick from. `released_in_window(instrument, analyte, start, end)`: every released version made from a
  batch with sample readings measured on that instrument in the window, with the reports that printed it
  (what a nonconformity may have touched).
  - Rejected: the host reading slim-data's tables (modules meet through read APIs, D-85, D-107).

- **D-113 The vertical audit** (Jamie, 2026-10-30, as recommended). ISO 17025 §7.5, §8.4. The quality team
  audits a released report end to end on one page: chosen at random from a period, or by number. The host
  composes it from each module's read API: the TR as it stood when the report was issued (D-104), the
  report file against its hash, per line slim-data's trail (D-112), the prep book's batch (slim-prep), the
  QC charts as they stood at the run (slim-quality, `as_of`), and the audit-log chain on Postgres. The audit
  is recorded in slim-quality (append-only: who, which report, conforming or findings, the findings);
  findings raise a nonconformity in one click. The seed of internal audits (§8.8).

- **D-114 Nonconformities with an impact assessment** (Jamie, 2026-10-30, as recommended). ISO 17025 §7.10,
  §8.7. In slim-quality: a nonconformity is raised by hand or in one click from a flag, an event or an
  audit, naming an instrument (and an analyte, or all) and a window; an ACTION flag never raises one by
  itself (noise). Its record is a list of entries, append-only, and its state follows them: open →
  assessed (a manager records a decision per released result the window touched: no action, notify the
  customer, or amend; D-112 lists them; slim-quality never changes a result, an amendment is slim-data's
  own correction) → action taken (root cause, action, owner, due) → effective (or not: a new action) →
  closed (a manager, only once effective). The effectiveness check shows the flags on that instrument
  since the action.
  - Rejected: states set by hand (they'd drift from the facts, D-99); assessments that change results.
