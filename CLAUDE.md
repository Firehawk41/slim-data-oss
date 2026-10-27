# slim-data — Claude context

> **Read first.** Design signed off 2026-10-19; now building (Phase B). Jamie is the **designer**; Claude
> writes the code, then explains it until Jamie can defend every decision in an interview. Raise any
> design change as a new decision (with a recommendation) before building it; push back when you
> disagree. **Short replies; Jamie is token-constrained.**

## What this is

A public portfolio piece: the **analytical results** module of SLIM, a LIMS for an ISO 17025 analytical
chemistry lab. Raw instrument readings → a versioned rules engine decides each result → people review,
accept, release → the record keeps exactly what each report showed. Append-only (time travel to any
past state), upgradable (versioned rulesets and steps, migrations). A **library**: the SLIM host app
(slim-lims-oss, folded from LabPortal, D-82) calls it, owns the transaction and provides the screens.
**OSS sandbox only:** demo data, never a real lab's databases (`persistence/oss_guard.py`).

## Documents (read before changing behaviour)

| File | What |
|---|---|
| `docs/DESIGN.md` | **Signed off.** What gets built; §7.2 is the engine's step table. |
| `docs/DECISIONS.md` | **Authoritative.** D-01…D-91, each with the options weighed. Append new ones; never edit history except to mark a same-day revision. |

## Status

| # | Milestone | State |
|---|---|---|
| M0 | Skeleton: packaging, layers, OSS guard, Alembic (own version table), architecture test, CI | ✅ |
| M1 | Domain: values, observations, results, policies, workflow rules | ✅ |
| M2 | Engine: cup_shape, linear_range, agreement, cup_choice, recovery, recovery_pooling, reported_value, uncertainty, method_blank, spec_limits; verdict = worst flag (qc_check since, D-87) | ✅ |
| M3 | Persistence: append-only tables + triggers, repositories, `as_of`, audit context, ruleset/policy publishing | ✅ |
| M4 | Data in: reader plugins, `generic_csv/1`, preview, all-or-nothing, file-hash idempotency | ✅ |
| M5 | Workflow service: evaluate, review, release, exceptions, corrections, `report_issues`, amended reports | ✅ (incl. D-68 solo sign-off) |
| M6 | Integration: part 1 (slim-data) adapter, report/batch views, demo scenarios, e2e test; part 2 LabPortal wiring in 3 slices (D-70) | ✅ (part 2 walkthrough done 2026-10-21) |
| M7 | Finish: `verify` command, README, walkthrough | ✅ (`verify` D-76/D-77, README, walkthrough 2026-10-21) |

Each milestone ends green on CI and with a walkthrough for Jamie. M0–M7 are merged into `main`.

**M6 part 2 (D-70)** wires slim-data into LabPortal (the slim-lims host; `slim_lims/results.py`,
`results_routes.py`). Slice 1 ✅: migrations, `SlimReferenceLookup` + `StaffDirectory` over the portal,
demo ruleset bound to 10 Elements, CSV upload → preview / import → evaluate, batch page + API, staff
`lab_role`. Slice 2 ✅: review / accept / rerun / exception / reject / correct / release on the batch
page and API; first readings → TR in progress (D-71). Slice 3: report page + issues ✅ (D-73: canonical-JSON hash until the report engine is wired; → partial /
complete report, D-71); personas ✅ (D-74); demo scenarios seeded in the portal ✅ (D-75, host-agnostic
`run_scenarios`). Walkthrough ✅.

**M7:** `service/verify.py` ✅: replays every stored version `as_of` its making, and runs the host's
audit-chain check if passed one (D-76); LabPortal installs slim-domain's audit log and has `cli verify`
(D-77). README ✅, walkthrough ✅. **All milestones done.** Since: D-78 (four demo analyses), D-79 (reports print the MDL, not ±), D-80 (½Δ only near a spec limit), D-81 (the batch view shows every cup), D-82 (the host is slim-lims-oss), D-83 (invoicing V5), D-84 (lowest total for the customer), D-85 (roadmap: Admin · Lab · Quality · IT, in slim-lims-oss docs/ROADMAP.md), D-86 (samples and prep: two modules), D-87 (roadmap phase 1, first slice: CHECK / CRM reading roles, `prep_batch_qc` expected values, `qc_check@1`, demo `qc_failed`). D-88 (instrument data in; step 1 ✅ data model: `SourceKind`, `Policy.source_kinds`, `Reading.rsd` / `istd_recovery`, `run_files`, migration 0006; step 2 ✅ `qtegra/1` + `run_calibrations`; step 3 ✅ `paste/1`; host import screen ✅ (slim-lims-oss); step 4 ✅ `precision@1` + `calibration@1` (D-90); next: `cup_choice@2`). D-89 (how `qtegra/1` reads Qtegra's export: Comment = sample ID, LOD = MDL, analyst = importer). D-90 (demo limits: RSD 10 % above 10 × MDL × DF, internal standard 50–150 %, R² ≥ 0.995). D-91 (MDL from the run's calibration by default; `mdl_source@1` switches to the lab's specified MDLs, falling back per element). Next in phase 1: slim-prep (`slim-prep-oss`, waits for Jamie's prep book layout), instrument readers.
Read slim-lims-oss's CLAUDE.md first (OSS guard, CSRF, CSP rules).

## File map

```
slim_data/
├── domain/          # pure (stdlib + pydantic): base (Frozen, Dec), enums, values, actor,
│                    #   observations, results, policy, workflow, ports (ReferenceLookup, StaffDirectory)
├── engine/          # pure (+ domain): numbers, model, step, ruleset, registry, runner
│   └── steps/       # cup_shape, linear_range, agreement, cup_choice, recovery, recovery_pooling,
│                    #   reported_value, uncertainty, method_blank, qc_check, precision, calibration, mdl_source, spec_limits (all @1)
├── rulesets/        # ruleset files (JSON): demo_metals, demo_anions, demo_assay (D-78)
├── pipeline/        # reader contract (reader.py) + generic_csv/1 + paste/1 + qtegra/1 (D-89); pure
├── persistence/     # oss_guard, db (make_engine = the only create_engine), migrate, migrations/,
│                    #   types, tables, append_only, audit; repositories: rulesets, observations, results
├── service/         # imports (preview / all-or-nothing import), workflow (evaluate, review, release,
│                    #   issues), views (report_view, batch_view, trace), verify (replay + host's chain check)
├── demo/            # scenarios.py: the named demo scenarios (DemoHost, run_scenarios), seeded through the service
└── adapters/        # slim.py: ReferenceLookup over slim-domain + the host's TR samples
tests/               # factories.py; conftest (session); domain/; engine/; persistence/; pipeline/ (fixtures); service/
.github/workflows/ci.yml  # ruff + {SQLite, Postgres 16} × {3.11, 3.12} + 95% branch coverage on domain + engine
```

Run: `pip install -e ".[dev]" && pytest` (Postgres: `TEST_DATABASE_URL=postgresql+psycopg2://...`);
`ruff check . && ruff format --check .`.

## Rules for the build

- `domain/` and `engine/` import only stdlib + pydantic (+ domain for engine); enforced by
  `tests/test_architecture.py`.
- Values are `Dec` (Decimal, floats refused). Engine math runs in `engine_arithmetic()`; traces store
  Decimals as strings. Compare unrounded; round once at the end.
- Steps are `name@version`, never edited or removed once released: a change is a new version plus a
  line in `tests/engine/step_manifest.txt`. Steps declare every flag code; the ruleset gives each a
  severity; verdict = worst flag.
- Thresholds and tables are ruleset/policy **data**; never `if demo:` branches. Demo values are
  invented, never a real lab's (D-05).
- Portable schema (SQLite + Postgres): enums as ints, JSON via SQLAlchemy `JSON`, no ARRAY, no FKs
  into other modules' tables. Repositories flush; the caller commits.
- Every push: ruff, full tests, coverage gate; keep the 95% branch coverage on domain + engine.
