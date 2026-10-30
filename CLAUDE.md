# slim-data — Claude context

> **Read first.** Design signed off 2026-10-19; now building (Phase B). Jamie is the **designer**; Claude
> writes the code, then explains it until Jamie can defend every decision in an interview. Raise any
> design change as a new decision (with a recommendation) before building it; push back when you
> disagree. **Short replies; Jamie is token-constrained.** Log who decided what in slim-lims-oss `docs/DEV_DIARY.md`.

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
| `docs/DECISIONS.md` | **Authoritative.** D-01…D-111, each with the options weighed. Append new ones; never edit history except to mark a same-day revision. |

## Status

M0–M7 are done and merged (skeleton, domain, engine, persistence, data in, workflow, host wiring,
`verify`); since then D-78…D-111 (latest decisions at the end of `docs/DECISIONS.md`). What exists now:
- **Engine steps:** `cup_shape`, `linear_range`, `agreement`, `recovery`, `recovery_pooling`, `uncertainty`,
  `method_blank`, `precision`, `calibration`, `mdl_source`, `spec_limits` (@1); `cup_choice`,
  `reported_value`, `qc_check` (@1 and @2). Verdict = worst flag.
- **Readers:** `generic_csv/1`, `paste/1`, `qtegra/1` (D-89; DOCTYPE refused by expat, D-101), `qtegra/2`
  (keeps each element's mode as the reading's `conditions`, D-106; what the host uses).
- **Prep handoff:** slim-prep registers batches and sends corrections (D-95, D-96); the prep book's dilution
  beats the file's; dilution may be below 1 (D-93); a file adds no samples or spikes to such a batch (D-105).
- **Quality:** slim-quality-oss reads `service.quality.qc_history` through the host (D-107); a batch may be
  QC-only (no samples, D-108). slim-quality's own decisions are D-109, D-110.
- **Host:** slim-lims-oss (D-82); host-side decisions (D-99…D-104, D-111) are logged here too.

Next for slim-data: nothing scheduled; the ecosystem's next phase is in slim-lims-oss `docs/ROADMAP.md`.
Working defaults (batches, quiet runs, reviews with the checklist): slim-lims-oss CLAUDE.md.
Read slim-lims-oss's CLAUDE.md first (OSS guard, CSRF, CSP rules).

## File map

```
slim_data/
├── domain/          # pure (stdlib + pydantic): base (Frozen, Dec), enums, values, actor,
│                    #   observations, results, policy, workflow, ports (ReferenceLookup, StaffDirectory)
├── engine/          # pure (+ domain): numbers, model, step, ruleset, registry, runner
│   └── steps/       # cup_shape, linear_range, agreement, cup_choice, recovery, recovery_pooling,
│                    #   reported_value, uncertainty, method_blank, qc_check, precision, calibration, mdl_source, spec_limits (@1); cup_choice@2, reported_value@2, qc_check@2
├── rulesets/        # ruleset files (JSON): demo_metals, demo_anions, demo_assay (D-78)
├── pipeline/        # reader contract (reader.py) + generic_csv/1 + paste/1 + qtegra/1, /2 (D-89, D-106); pure
├── persistence/     # oss_guard, db (make_engine = the only create_engine), migrate, migrations/,
│                    #   types, tables, append_only, audit; repositories: rulesets, observations, results
├── service/         # imports (preview / all-or-nothing import), workflow (evaluate, review, release,
│                    #   issues), views (report_view, batch_view, trace), verify (replay + host's chain check),
│                    #   quality (qc_history: what slim-quality reads, D-107)
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
