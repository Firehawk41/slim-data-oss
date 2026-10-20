"""Rules engine: step protocol, versioned step registry, ruleset model, steps, trace and replay.

May import only the standard library, pydantic, slim_data.domain and slim_data.engine
(tests/test_architecture.py). Pure: no I/O, no clock, no database, so replay is exact.
"""
