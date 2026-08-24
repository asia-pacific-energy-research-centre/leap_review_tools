"""Static-dashboard decoration for two user-selected LEAP export versions."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import numpy as np


def _total_index(figure: dict, scenario: str) -> int | None:
    tag = "ref" if scenario.casefold() == "reference" else "tgt"
    metadata = figure.get("layout", {}).get("meta", {}).get("trace_meta", [])
    for index, (trace, meta) in enumerate(zip(figure.get("data", []), metadata)):
        if (
            str(meta.get("source_system", "")).upper() == "LEAP"
            and str(meta.get("tag", "")).lower() == tag
            and "total" in str(trace.get("name", "")).lower()
        ):
            return index
    return None


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
            old_years, new_years = _values(old_trace["x"]), _values(new_trace["x"])
            old_values, new_values = _values(old_trace["y"]), _values(new_trace["y"])
            if old_years != new_years or len(old_values) != len(new_values):
                continue
            original_trace = copy.deepcopy(old_trace)
            original_trace.update(
                name=f"LEAP {scenario} Total — Version 1 (original)",
                line={"color": "#6b7280", "dash": "dot", "width": 3},
            )
            new_trace.update(
                name=f"LEAP {scenario} Total — Version 2 (new)",
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
