#%%
"""Maintain deployment-owned runtime averages for the web-app UI.

The profile is deliberately a plain JSON artifact maintained for the hosted
Space.  It is not written by normal local or public-user runs.  Each process
group keeps its most recent successful measurements, up to SAMPLE_LIMIT.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Twenty-five measurements per process group rather than five. A handful of
# runs on shared hosted hardware is a noisy thing to average, and the
# interface quotes that average as what to expect; a longer window steadies
# it without going so far back that it describes a different machine.
SAMPLE_LIMIT = 25
PROFILE_SCHEMA_VERSION = 2
PROCESS_GROUPS = ("workbook", "dashboard", "dashboard_trace_only", "full_run")
# A workbook is built per requested year, so its cost scales with how many
# were asked for. The dashboard renders its own fixed year range regardless.
YEAR_SCALED_GROUPS = ("workbook", "full_run")


def empty_runtime_profile() -> dict[str, Any]:
    """Return a new empty profile suitable for committing to the HF repo."""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "sample_limit": SAMPLE_LIMIT,
        "updated_at": None,
        "source": "huggingface_space",
        # Free-text provenance for a committed seed; preserved across writes.
        "note": "",
        "samples_seconds": {group: [] for group in PROCESS_GROUPS},
        # Parallel to samples_seconds: how many review years each run covered,
        # so a per-year cost can be separated from fixed overhead.
        "samples_years": {group: [] for group in PROCESS_GROUPS},
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
            years = raw.get("samples_years", {}).get(group, [])
            if isinstance(years, list):
                profile["samples_years"][group] = [
                    int(value) if value else None for value in years[-SAMPLE_LIMIT:]
                ]
        # A schema 1 profile has durations but no year counts. Pad so the two
        # lists stay aligned rather than silently pairing the wrong entries.
        for group in PROCESS_GROUPS:
            durations = profile["samples_seconds"][group]
            counts = profile["samples_years"][group]
            if len(counts) < len(durations):
                profile["samples_years"][group] = (
                    [None] * (len(durations) - len(counts))
                ) + counts
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
    years: int | None = None,
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
    counts = {
        group: list(profile.get("samples_years", {}).get(group, []))
        for group in PROCESS_GROUPS
    }
    for group in PROCESS_GROUPS:
        if len(counts[group]) < len(samples[group]):
            counts[group] = [None] * (len(samples[group]) - len(counts[group])) + counts[group]
    samples[process_group].append(round(float(elapsed_seconds), 1))
    samples[process_group] = samples[process_group][-SAMPLE_LIMIT:]
    counts[process_group].append(int(years) if years else None)
    counts[process_group] = counts[process_group][-SAMPLE_LIMIT:]
    updated["samples_seconds"] = samples
    updated["samples_years"] = counts
    updated["averages_seconds"] = {
        group: round(sum(values) / len(values), 1) if values else None
        for group, values in samples.items()
    }
    updated["sample_limit"] = SAMPLE_LIMIT
    updated["schema_version"] = PROFILE_SCHEMA_VERSION
    # Hugging Face sets SPACE_ID in a Space container. A local run must not
    # claim its measurement was taken on the hosted hardware.
    updated["source"] = (
        "huggingface_space" if os.environ.get("SPACE_ID") else "local_seed"
    )
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated


def save_runtime_profile(path: Path | str, profile: dict[str, Any]) -> None:
    """Write the deployment profile for a deliberate HF benchmark update."""
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")


def format_duration(seconds: float) -> str:
    """Return a compact human duration such as ``3 min 05 sec``."""
    minutes, remainder = divmod(round(float(seconds)), 60)
    return f"{minutes} min {remainder:02d} sec" if minutes else f"{remainder} sec"


def estimate_runtime(
    profile: dict[str, Any],
    *,
    process_group: str,
    years: int = 1,
) -> tuple[float | None, float | None]:
    """Return ``(estimated_seconds, seconds_per_extra_year)`` for a run.

    A workbook is rebuilt for each requested year while the surrounding work
    happens once, so the cost is modelled as a fixed part plus a per-year
    part. Two different year counts are needed to separate them; with only one
    the split is unknowable, so no marginal figure is offered rather than a
    fabricated one.
    """
    durations = list(profile.get("samples_seconds", {}).get(process_group, []))
    counts = list(profile.get("samples_years", {}).get(process_group, []))
    counts += [None] * (len(durations) - len(counts))
    average = profile.get("averages_seconds", {}).get(process_group)

    if process_group not in YEAR_SCALED_GROUPS:
        return (float(average) if average is not None else None), None

    pairs = [
        (int(count), float(duration))
        for count, duration in zip(counts, durations)
        if count
    ]
    if len({count for count, _ in pairs}) >= 2:
        n = len(pairs)
        mean_x = sum(c for c, _ in pairs) / n
        mean_y = sum(d for _, d in pairs) / n
        variance = sum((c - mean_x) ** 2 for c, _ in pairs)
        slope = sum((c - mean_x) * (d - mean_y) for c, d in pairs) / variance
        intercept = mean_y - slope * mean_x
        return max(intercept + slope * max(years, 1), 0.0), max(slope, 0.0)

    if process_group == "full_run":
        # A whole run is a workbook plus a dashboard. Composing the two is far
        # better than extrapolating one full-run sample, because most of a
        # workbook's cost is fixed: scaling a single measurement by the year
        # count would inflate a five-year estimate several times over.
        workbook_estimate, workbook_per_year = estimate_runtime(
            profile, process_group="workbook", years=years
        )
        dashboard_estimate, _ = estimate_runtime(
            profile, process_group="dashboard", years=years
        )
        if workbook_estimate is not None and dashboard_estimate is not None:
            return workbook_estimate + dashboard_estimate, workbook_per_year

    if pairs:
        # One year count only: the fixed and per-year parts cannot be told
        # apart, so report what was measured rather than scaling it and
        # claiming a precision the data does not support.
        return sum(d for _, d in pairs) / len(pairs), None

    return (float(average) if average is not None else None), None


def samples_behind_estimate(
    profile: dict[str, Any],
    *,
    process_group: str,
    years: int = 1,
) -> int:
    """Return how many measurements the quoted estimate actually rests on.

    This follows the same branches as ``estimate_runtime``, because the answer
    differs by branch: a whole run quoted as workbook plus dashboard rests on
    those two groups' samples, not on the one full-run measurement that was
    too thin to fit. The interface says what it is comparing a slow run
    against, so the number has to be the one that was really used.
    """
    durations = list(profile.get("samples_seconds", {}).get(process_group, []))
    counts = list(profile.get("samples_years", {}).get(process_group, []))
    counts += [None] * (len(durations) - len(counts))

    if process_group not in YEAR_SCALED_GROUPS:
        return len(durations)

    pairs = [(int(count), duration) for count, duration in zip(counts, durations) if count]
    if len({count for count, _ in pairs}) >= 2:
        return len(pairs)

    if process_group == "full_run":
        workbook = samples_behind_estimate(
            profile, process_group="workbook", years=years
        )
        dashboard = samples_behind_estimate(
            profile, process_group="dashboard", years=years
        )
        if workbook and dashboard:
            return min(workbook, dashboard)

    return len(durations)


def format_runtime_note(
    profile: dict[str, Any],
    *,
    process_group: str,
    years: int = 1,
    economies: int = 1,
    version_comparison: bool = False,
) -> str:
    """Return concise UI copy for a process card.

    A dashboard is rendered per economy, so several multiply the wait. The
    per-economy cost is quoted whenever more than one could be rendered.
    """
    estimate, per_year = estimate_runtime(
        profile, process_group=process_group, years=years
    )
    if version_comparison and process_group == "dashboard":
        trace_estimate, _ = estimate_runtime(
            profile, process_group="dashboard_trace_only", years=years
        )
        estimate = (
            trace_estimate + estimate
            if trace_estimate is not None and estimate is not None
            else None
        )
        if estimate is None:
            return (
                "Version 1 / Version 2 comparison timing will appear after the "
                "first measured trace-only Version 1 and full Version 2 run."
            )
        lead = (
            "Average on Hugging Face"
            if str(profile.get("source", "")) == "huggingface_space"
            else "Typical Version 1 / Version 2 comparison"
        )
        note = (
            f"{lead}: about {format_duration(estimate)} for trace-only Version 1 "
            "plus the full Version 2 dashboard."
        )
        if economies > 1:
            note = (
                f"{lead}: about {format_duration(estimate * economies)} for "
                f"{economies} economy comparisons."
            )
        note += (
            " A comparison for each extra economy adds about "
            f"{format_duration(estimate)}."
        )
        return note
    if estimate is None:
        return "HF average will appear after the first hosted benchmark."
    lead = (
        "Average on Hugging Face"
        if str(profile.get("source", "")) == "huggingface_space"
        else "Typical run"
    )
    per_economy = estimate if process_group == "dashboard" else None
    if process_group == "dashboard" and economies > 1:
        estimate = estimate * economies
    if process_group in YEAR_SCALED_GROUPS and years > 1:
        note = f"{lead}: about {format_duration(estimate)} for {years} years."
    elif process_group == "dashboard" and economies > 1:
        note = f"{lead}: about {format_duration(estimate)} for {economies} economies."
    else:
        note = f"{lead}: about {format_duration(estimate)}."
    if per_year is not None and process_group in YEAR_SCALED_GROUPS:
        note += f" Each extra year adds about {format_duration(per_year)}."
    if per_economy is not None:
        note += (
            f" Each extra economy adds about {format_duration(per_economy)}; "
            "extra scenarios for the same economy are rendered in the same pass, "
            "so they add far less."
        )
    return note


#%%
