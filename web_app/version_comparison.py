"""Static-dashboard decoration for two user-selected LEAP export versions."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import numpy as np


def _total_index(figure: dict, scenario: str) -> int | None:
    scenario_key = str(scenario or "").strip().casefold()
    accepted_tags = {scenario_key, f"scenario:{scenario_key}"}
    if scenario_key == "reference":
        accepted_tags.add("ref")
    elif scenario_key == "target":
        accepted_tags.add("tgt")
    metadata = figure.get("layout", {}).get("meta", {}).get("trace_meta", [])
    candidates = [
        (index, str(trace.get("name", "")).strip())
        for index, (trace, meta) in enumerate(zip(figure.get("data", []), metadata))
        if str(meta.get("source_system", "")).upper() == "LEAP"
        and (
            str(meta.get("scenario", "")).strip().casefold() == scenario_key
            or str(meta.get("tag", "")).strip().casefold() in accepted_tags
        )
    ]
    for index, name in candidates:
        if "total" in name.casefold():
            return index

    # Several renderer chart families give the aggregate a scoped name such
    # as ``LEAP Target (Domestic TFC)`` or simply ``LEAP Target``. Requiring
    # the literal word "total" silently omitted those otherwise-comparable
    # charts from the Version 1 / Version 2 overlay.
    aggregate_prefix = f"leap {scenario}".casefold()
    for index, name in candidates:
        normalised = " ".join(name.casefold().split())
        if normalised == aggregate_prefix or normalised.startswith(
            aggregate_prefix + " ("
        ):
            return index

    # A lone LEAP scenario trace is unambiguous even when its display label is
    # specialised. Multiple component traces are deliberately not guessed at.
    if len(candidates) == 1:
        return candidates[0][0]
    return None


def _axis_values(values: object) -> list[object]:
    """Decode an x-axis without assuming it contains numeric years."""
    if isinstance(values, list):
        return values
    if isinstance(values, dict) and {"dtype", "bdata"} <= set(values):
        return np.frombuffer(
            base64.b64decode(values["bdata"]), dtype=np.dtype(values["dtype"])
        ).tolist()
    raise ValueError("This chart uses an unsupported chart-axis format.")


def _values(values: object) -> list[float]:
    if isinstance(values, list):
        return [float(value) for value in values]
    if isinstance(values, dict) and {"dtype", "bdata"} <= set(values):
        return (
            np.frombuffer(
                base64.b64decode(values["bdata"]), dtype=np.dtype(values["dtype"])
            )
            .astype(float)
            .tolist()
        )
    raise ValueError("This chart uses an unsupported chart-value format.")


def _write_bundle(path: Path, charts: dict) -> None:
    payload = json.dumps({"charts": charts}, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8")
    path.with_suffix(".js").write_text(
        "window.COMMON_ESTO_CHART_BUNDLE_DATA=" + payload.replace("</", "<\\/") + ";\n",
        encoding="utf-8",
    )


def _version_trace_name(trace_name: object, scenario: str, version: str) -> str:
    """Add a version suffix without mislabelling a specialised trace as a total."""
    name = str(trace_name or "").strip()
    aggregate_prefix = f"leap {scenario}".casefold()
    normalised = " ".join(name.casefold().split())
    if "total" in normalised or normalised == aggregate_prefix or normalised.startswith(
        aggregate_prefix + " ("
    ):
        name = f"LEAP {scenario} Total"
    return f"{name} — {version}"


def apply_version_comparison(
    original_dashboard: Path,
    new_dashboard: Path,
    *,
    scenario: str,
    green_percent: float,
    yellow_percent: float,
) -> dict[str, int]:
    """Add Version 1/Version 2 total lines without decorating chart borders."""
    # Retain these arguments for compatibility with existing callers and run
    # records. Difference thresholds no longer affect the rendered dashboard.
    del green_percent, yellow_percent
    comparable_keys: set[str] = set()
    for new_bundle in (new_dashboard / "chart_bundles").glob("*.json"):
        original_bundle = original_dashboard / "chart_bundles" / new_bundle.name
        if not original_bundle.is_file():
            continue
        old_charts = json.loads(original_bundle.read_text(encoding="utf-8"))["charts"]
        new_charts = json.loads(new_bundle.read_text(encoding="utf-8"))["charts"]
        changed = False
        for key, new_figure in new_charts.items():
            old_figure = old_charts.get(key)
            old_index = _total_index(old_figure or {}, scenario)
            new_index = _total_index(new_figure, scenario)
            if old_index is None or new_index is None:
                continue
            old_trace = old_figure["data"][old_index]
            new_trace = new_figure["data"][new_index]
            old_years = _axis_values(old_trace["x"])
            new_years = _axis_values(new_trace["x"])
            old_values, new_values = _values(old_trace["y"]), _values(new_trace["y"])
            if old_years != new_years or len(old_values) != len(new_values):
                continue
            original_trace = copy.deepcopy(old_trace)
            original_trace.update(
                name=_version_trace_name(
                    old_trace.get("name"), scenario, "Version 1 (original)"
                ),
                line={"color": "#6b7280", "dash": "dot", "width": 3},
            )
            new_trace.update(
                name=_version_trace_name(
                    new_trace.get("name"), scenario, "Version 2 (new)"
                ),
                line={"color": "#1d4ed8", "dash": "solid", "width": 3},
            )
            new_figure["data"].insert(new_index, original_trace)
            new_figure["layout"]["meta"]["trace_meta"].insert(
                new_index,
                copy.deepcopy(old_figure["layout"]["meta"]["trace_meta"][old_index]),
            )
            comparable_keys.add(key)
            changed = True
        if changed:
            _write_bundle(new_bundle, new_charts)
    if not comparable_keys:
        raise ValueError(
            "No comparable Version 1 / Version 2 total traces were found in the "
            "rendered chart bundles."
        )
    return {"green": 0, "yellow": 0, "red": 0}
