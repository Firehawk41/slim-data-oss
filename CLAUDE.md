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
(slim-lims, built from LabPortal) calls it, owns the transaction and provides the screens.
**OSS sandbox only:** demo data, never a real lab's databases (`persistence/oss_guard.py`).

## Documents (read before changing behaviour)

| File | What |
|---|---|
| `docs/DESIGN.md` | **Signed off.** What gets built; §7.2 is the engine's step table. |
| `docs/DECISIONS.md` | **Authoritative.** D-01…D-63, each with the options weighed. Append new ones; never edit history except to mark a same-day revision. |

## Status

| # | Milestone | State |
|---|---|---|
| M0 | Skeleton: packaging, layers, OSS guard, Alembic (own version table), architecture test, CI | ✅ |
| M1 | Domain: values, observations, results, policies, workflow rules | ✅ |
| M2 | Engine: framework + `cup_shape`, `linear_range`, `agreement`, `cup_choice` | ✅ part 1 |
| | **Next — M2 part 2:** recovery + pooling (D-36, D-37, D-53), reported value / MDL / QL / rounding (D-54), ± (D-55), method blank (D-58; BLANK readings aren't TR samples — decide how they're keyed), spec-limit flags (D-60), verdict (D-56) | |
| M3 | Persistence: append-only tables + triggers, repository, `as_of`, audit log, ruleset publishing | |
| M4 | Data in: reader plugins, `generic_csv/1`, preview, all-or-nothing, file-hash idempotency | |
| M5 | Workflow service: review, release, exceptions, corrections, `report_issues`, amended reports | |
| M6 | Integration: ReferenceLookup adapter, report read model, host wiring, demo scenarios, e2e test | |
| M7 | Finish: `verify` command, README, walkthrough | |

Each milestone ends green on CI and with a walkthrough for Jamie. Work is on branch
`claude/wonderful-dijkstra-jwxssi`; PR Firehawk41/slim-data-oss#1 is open (merges on Jamie's approval).

## File map

```
slim_data/
├── domain/          # pure (stdlib + pydantic): base (Frozen, Dec), enums, values, actor,
│                    #   observations, results, policy, workflow
├── engine/          # pure (+ domain): numbers, model, step, ruleset, registry, runner
│   └── steps/       # cup_shape@1, linear_range@1, agreement@1, cup_choice@1
├── rulesets/        # ruleset files (JSON): demo_metals.json
├── pipeline/        # (M4) reader plugins -> import plan
├── persistence/     # oss_guard, db (make_engine = the only create_engine), migrate, migrations/
├── service/         # (M5) ResultService facade, DESIGN.md §11
└── adapters/        # (M6) ReferenceLookup over slim-domain
tests/               # factories.py; domain/; engine/ (helpers.py, step_manifest.txt); guard, architecture, migrations
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
