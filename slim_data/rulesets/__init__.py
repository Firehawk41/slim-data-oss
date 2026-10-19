"""Ruleset files (D-14): authored here as JSON, then published into the database (M3).

The demo values are invented but plausible; they are never a real lab's thresholds (D-05).
"""

from importlib.resources import files


def ruleset_text(name: str) -> str:
    return files(__package__).joinpath(f"{name}.json").read_text()
