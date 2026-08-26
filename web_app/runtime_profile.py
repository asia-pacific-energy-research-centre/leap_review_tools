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

# Keep enough history to populate several distinct run shapes without one busy
# combination immediately evicting another. Estimates still prefer recent
# hosted behavior because this is a rolling window rather than an all-time log.
SAMPLE_LIMIT = 100
PROFILE_SCHEMA_VERSION = 3
PROCESS_GROUPS = (
    "workbook",
    "dashboard",
    "dashboard_trace_only",
    "dashboard_version_2_full",
    "full_run",
)
RUN_KIND_STANDARD = "standard"
RUN_KIND_VERSION_COMPARISON = "version_comparison"
RUN_KIND_LEGACY_UNKNOWN = "legacy_unknown"
SHAPE_MATCHED_GROUPS = ("dashboard", "full_run")
OUTLIER_MIN_SAMPLES = 8
OUTLIER_MAD_MULTIPLIER = 3.5
OUTLIER_MAD_FLOOR_RATIO = 0.01
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
        # Dashboard and full-run measurements are matched to the exact run
        # shape. A single dashboard must not train a two-economy or two-version
        # estimate.
        "samples_economies": {group: [] for group in PROCESS_GROUPS},
        "samples_run_kinds": {group: [] for group in PROCESS_GROUPS},
        "averages_seconds": {group: None for group in PROCESS_GROUPS},
        "outlier_policy": {
            "method": "median_absolute_deviation",
            "minimum_comparable_samples": OUTLIER_MIN_SAMPLES,
            "threshold": OUTLIER_MAD_MULTIPLIER,
            "mad_floor_ratio": OUTLIER_MAD_FLOOR_RATIO,
        },
    }


def _pad_left(values: list[Any], length: int, fill: Any) -> list[Any]:
    """Align old parallel metadata with the newest retained durations."""
    if length <= 0:
        return []
    return [fill] * max(length - len(values), 0) + values[-length:]


def _legacy_run_kind(schema_version: int, process_group: str) -> str:
    """Return a safe shape label for profiles created before shape tracking."""
    if schema_version >= PROFILE_SCHEMA_VERSION:
        return (
            RUN_KIND_LEGACY_UNKNOWN
            if process_group in SHAPE_MATCHED_GROUPS
            else RUN_KIND_STANDARD
        )
    if process_group == "dashboard_trace_only":
        return RUN_KIND_VERSION_COMPARISON
    if process_group in SHAPE_MATCHED_GROUPS:
        # Old dashboard samples can include Version 2 full renders, and old
        # full-run samples do not say which output combination produced them.
        # Do not silently relabel that mixed history as a standard run.
        return RUN_KIND_LEGACY_UNKNOWN
    return RUN_KIND_STANDARD


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
        try:
            raw_schema_version = int(raw.get("schema_version", 1))
        except (TypeError, ValueError):
            raw_schema_version = 1
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
            economies = raw.get("samples_economies", {}).get(group, [])
            if isinstance(economies, list):
                profile["samples_economies"][group] = [
                    max(int(value or 1), 1) for value in economies[-SAMPLE_LIMIT:]
                ]
            run_kinds = raw.get("samples_run_kinds", {}).get(group, [])
            if isinstance(run_kinds, list):
                profile["samples_run_kinds"][group] = [
                    str(value).strip() for value in run_kinds[-SAMPLE_LIMIT:]
                ]
        # Older profiles lack one or more parallel metadata arrays. Pad on the
        # left so the newest entries remain aligned and mark ambiguous shapes
        # unknown instead of contaminating a standard or comparison estimate.
        for group in PROCESS_GROUPS:
            durations = profile["samples_seconds"][group]
            length = len(durations)
            profile["samples_years"][group] = _pad_left(
                profile["samples_years"][group], length, None
            )
            profile["samples_economies"][group] = _pad_left(
                profile["samples_economies"][group], length, 1
            )
            profile["samples_run_kinds"][group] = _pad_left(
                profile["samples_run_kinds"][group],
                length,
                _legacy_run_kind(raw_schema_version, group),
            )
        profile["schema_version"] = PROFILE_SCHEMA_VERSION
        profile["sample_limit"] = SAMPLE_LIMIT
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
    economies: int = 1,
    version_comparison: bool = False,
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
    economy_counts = {
        group: list(profile.get("samples_economies", {}).get(group, []))
        for group in PROCESS_GROUPS
    }
    run_kinds = {
        group: list(profile.get("samples_run_kinds", {}).get(group, []))
        for group in PROCESS_GROUPS
    }
    for group in PROCESS_GROUPS:
        length = len(samples[group])
        counts[group] = _pad_left(counts[group], length, None)
        economy_counts[group] = _pad_left(economy_counts[group], length, 1)
        run_kinds[group] = _pad_left(
            run_kinds[group],
            length,
            _legacy_run_kind(int(profile.get("schema_version", 1)), group),
        )
    samples[process_group].append(round(float(elapsed_seconds), 1))
    samples[process_group] = samples[process_group][-SAMPLE_LIMIT:]
    counts[process_group].append(int(years) if years else None)
    counts[process_group] = counts[process_group][-SAMPLE_LIMIT:]
    economy_counts[process_group].append(max(int(economies or 1), 1))
    economy_counts[process_group] = economy_counts[process_group][-SAMPLE_LIMIT:]
    run_kinds[process_group].append(
        RUN_KIND_VERSION_COMPARISON
        if version_comparison
        else RUN_KIND_STANDARD
    )
    run_kinds[process_group] = run_kinds[process_group][-SAMPLE_LIMIT:]
    updated["samples_seconds"] = samples
    updated["samples_years"] = counts
    updated["samples_economies"] = economy_counts
    updated["samples_run_kinds"] = run_kinds
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


def _median(values: list[float]) -> float:
    """Return the median without adding a scientific-stack dependency."""
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _filter_outlier_rows(
    rows: list[tuple[float, int | None]],
    *,
    process_group: str,
) -> list[tuple[float, int | None]]:
    """Remove extreme timings only after enough comparable evidence exists."""
    if len(rows) < OUTLIER_MIN_SAMPLES:
        return rows

    buckets: dict[int | None, list[tuple[float, int | None]]] = {}
    if process_group in YEAR_SCALED_GROUPS:
        for row in rows:
            buckets.setdefault(row[1], []).append(row)
    else:
        buckets[None] = rows

    kept: list[tuple[float, int | None]] = []
    for bucket in buckets.values():
        if len(bucket) < OUTLIER_MIN_SAMPLES:
            kept.extend(bucket)
            continue
        durations = [row[0] for row in bucket]
        median = _median(durations)
        mad = _median([abs(value - median) for value in durations])
        # Repeated rounded timings can make MAD exactly zero. A small floor
        # still rejects a gross spike without treating sub-second noise as an
        # outlier.
        mad = max(mad, abs(median) * OUTLIER_MAD_FLOOR_RATIO, 1.0)
        robust_sigma = 1.4826 * mad
        threshold = OUTLIER_MAD_MULTIPLIER * robust_sigma
        kept.extend(row for row in bucket if abs(row[0] - median) <= threshold)
    return kept or rows


def _matching_sample_rows(
    profile: dict[str, Any],
    *,
    process_group: str,
    economies: int,
    version_comparison: bool,
) -> list[tuple[float, int | None]]:
    """Return outlier-filtered measurements for one exact run shape."""
    durations = list(profile.get("samples_seconds", {}).get(process_group, []))
    years = _pad_left(
        list(profile.get("samples_years", {}).get(process_group, [])),
        len(durations),
        None,
    )
    economy_counts = _pad_left(
        list(profile.get("samples_economies", {}).get(process_group, [])),
        len(durations),
        1,
    )
    run_kinds = _pad_left(
        list(profile.get("samples_run_kinds", {}).get(process_group, [])),
        len(durations),
        _legacy_run_kind(int(profile.get("schema_version", 1)), process_group),
    )
    requested_kind = (
        RUN_KIND_VERSION_COMPARISON
        if version_comparison
        else RUN_KIND_STANDARD
    )
    rows = []
    for duration, year_count, economy_count, run_kind in zip(
        durations, years, economy_counts, run_kinds
    ):
        if process_group in SHAPE_MATCHED_GROUPS and (
            max(int(economy_count or 1), 1) != max(int(economies or 1), 1)
            or str(run_kind) != requested_kind
        ):
            continue
        rows.append((float(duration), int(year_count) if year_count else None))
    return _filter_outlier_rows(rows, process_group=process_group)


def estimate_runtime(
    profile: dict[str, Any],
    *,
    process_group: str,
    years: int = 1,
    economies: int = 1,
    version_comparison: bool = False,
) -> tuple[float | None, float | None]:
    """Return ``(estimated_seconds, seconds_per_extra_year)`` for a run.

    A workbook is rebuilt for each requested year while the surrounding work
    happens once, so the cost is modelled as a fixed part plus a per-year
    part. Two different year counts are needed to separate them; with only one
    the split is unknowable, so no marginal figure is offered rather than a
    fabricated one.
    """
    rows = _matching_sample_rows(
        profile,
        process_group=process_group,
        economies=economies,
        version_comparison=version_comparison,
    )
    durations = [duration for duration, _ in rows]
    counts = [count for _, count in rows]
    average = sum(durations) / len(durations) if durations else None

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
            profile,
            process_group="dashboard",
            years=years,
            economies=economies,
            version_comparison=version_comparison,
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
    economies: int = 1,
    version_comparison: bool = False,
) -> int:
    """Return how many measurements the quoted estimate actually rests on.

    This follows the same branches as ``estimate_runtime``, because the answer
    differs by branch: a whole run quoted as workbook plus dashboard rests on
    those two groups' samples, not on the one full-run measurement that was
    too thin to fit. The interface says what it is comparing a slow run
    against, so the number has to be the one that was really used.
    """
    rows = _matching_sample_rows(
        profile,
        process_group=process_group,
        economies=economies,
        version_comparison=version_comparison,
    )
    durations = [duration for duration, _ in rows]
    counts = [count for _, count in rows]

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
            profile,
            process_group="dashboard",
            years=years,
            economies=economies,
            version_comparison=version_comparison,
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

    Dashboard and full-run estimates use only measurements for the requested
    economy count and whether a two-version comparison was selected.
    """
    estimate, per_year = estimate_runtime(
        profile,
        process_group=process_group,
        years=years,
        economies=economies,
        version_comparison=version_comparison,
    )
    if version_comparison and process_group == "dashboard":
        if estimate is None:
            return (
                "Version 1 / Version 2 comparison timing will appear after the "
                "first comparable measured run for this economy count."
            )
        lead = (
            "Average on Hugging Face"
            if str(profile.get("source", "")) == "huggingface_space"
            else "Typical Version 1 / Version 2 comparison"
        )
        note = (
            f"{lead}: about {format_duration(estimate)} for the complete "
            "two-version dashboard comparison."
        )
        if economies > 1:
            note = (
                f"{lead}: about {format_duration(estimate)} for "
                f"{economies} economy comparisons."
            )
        note += " Different economy counts are measured separately."
        return note
    if estimate is None:
        return "HF average will appear after the first hosted benchmark."
    lead = (
        "Average on Hugging Face"
        if str(profile.get("source", "")) == "huggingface_space"
        else "Typical run"
    )
    if process_group in YEAR_SCALED_GROUPS and years > 1:
        note = f"{lead}: about {format_duration(estimate)} for {years} years."
    elif process_group == "dashboard" and economies > 1:
        note = f"{lead}: about {format_duration(estimate)} for {economies} economies."
    else:
        note = f"{lead}: about {format_duration(estimate)}."
    if per_year is not None and process_group in YEAR_SCALED_GROUPS:
        note += f" Each extra year adds about {format_duration(per_year)}."
    if process_group == "dashboard":
        note += (
            " Different economy counts and two-version comparisons are "
            "measured separately."
        )
    return note


#%%
