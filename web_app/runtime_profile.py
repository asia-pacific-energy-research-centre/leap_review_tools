#%%
"""Maintain deployment-owned runtime averages for the web-app UI.

The profile is deliberately a plain JSON artifact maintained for the hosted
Space.  It is not written by normal local or public-user runs.  Each process
group keeps only its five most recent successful measurements.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAMPLE_LIMIT = 5
PROFILE_SCHEMA_VERSION = 1
PROCESS_GROUPS = ("workbook", "dashboard", "full_run")


def empty_runtime_profile() -> dict[str, Any]:
    """Return a new empty profile suitable for committing to the HF repo."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "sample_limit": SAMPLE_LIMIT,
        "updated_at": None,
        "source": "huggingface_space",
        "samples_seconds": {group: [] for group in PROCESS_GROUPS},
        "averages_seconds": {group: None for group in PROCESS_GROUPS},
    }


def load_runtime_profile(path: Path | str) -> dict[str, Any]:
    """Load a profile, returning an empty compatible profile if absent/invalid."""
    profile_path = Path(path)
    if not profile_path.is_file():
        return empty_runtime_profile()
    try:
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty_runtime_profile()
    profile = empty_runtime_profile()
    if isinstance(raw, dict):
        profile.update({key: raw[key] for key in profile if key in raw})
        for group in PROCESS_GROUPS:
            values = raw.get("samples_seconds", {}).get(group, [])
            if isinstance(values, list):
                profile["samples_seconds"][group] = [
                    float(value) for value in values[-SAMPLE_LIMIT:]
                ]
    for group in PROCESS_GROUPS:
        values = profile["samples_seconds"][group]
        profile["averages_seconds"][group] = (
            round(sum(values) / len(values), 1) if values else None
        )
    return profile


def record_runtime_sample(
    profile: dict[str, Any],
    *,
    process_group: str,
    elapsed_seconds: float,
) -> dict[str, Any]:
    """Return an updated profile containing one successful HF measurement."""
    if process_group not in PROCESS_GROUPS:
        raise ValueError(f"Unknown process group: {process_group!r}")
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must not be negative")
    updated = empty_runtime_profile()
    updated.update(profile)
    samples = {
        group: list(profile.get("samples_seconds", {}).get(group, []))
        for group in PROCESS_GROUPS
    }
    samples[process_group].append(round(float(elapsed_seconds), 1))
    samples[process_group] = samples[process_group][-SAMPLE_LIMIT:]
    updated["samples_seconds"] = samples
    updated["averages_seconds"] = {
        group: round(sum(values) / len(values), 1) if values else None
        for group, values in samples.items()
    }
    updated["sample_limit"] = SAMPLE_LIMIT
    updated["schema_version"] = PROFILE_SCHEMA_VERSION
    updated["source"] = "huggingface_space"
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated


def save_runtime_profile(path: Path | str, profile: dict[str, Any]) -> None:
    """Write the deployment profile for a deliberate HF benchmark update."""
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")


def format_runtime_note(
    profile: dict[str, Any],
    *,
    process_group: str,
) -> str:
    """Return concise UI copy for a process card."""
    average = profile.get("averages_seconds", {}).get(process_group)
    if average is None:
        return "HF average will appear after the first hosted benchmark."
    minutes, seconds = divmod(round(float(average)), 60)
    if minutes:
        duration = f"about {minutes} min {seconds:02d} sec"
    else:
        duration = f"about {seconds} sec"
    return f"Average on Hugging Face: {duration} (last 5 successful runs)."


#%%
