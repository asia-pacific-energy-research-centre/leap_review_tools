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
    """Add Version 1/Version 2 total lines and colour each comparable chart card.

    Percent differences use absolute values and the original value as the
    denominator. A zero original with a non-zero new value is red.
    """
    if green_percent < 0 or yellow_percent < green_percent:
        raise ValueError("Yellow tolerance must be at least the green tolerance.")
    statuses: dict[str, str] = {}
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
                statuses[key] = "red"
                continue
            differences = [
                abs(new - old) / abs(old) * 100
                if old
                else (0 if new == 0 else float("inf"))
                for old, new in zip(old_values, new_values)
            ]
            maximum = max(differences, default=0)
            statuses[key] = (
                "green"
                if maximum <= green_percent
                else "yellow"
                if maximum <= yellow_percent
                else "red"
            )
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
            changed = True
        if changed:
            _write_bundle(new_bundle, new_charts)
    if not statuses:
        raise ValueError(
            "No comparable Version 1 / Version 2 total traces were found in the "
            "rendered chart bundles."
        )
    css = (
        "<style>.chart-card.version-green{border:4px solid #1b7f3a}"
        ".chart-card.version-yellow{border:4px solid #d9a400}"
        ".chart-card.version-red{border:4px solid #c62828}</style>"
    )
    for page in (new_dashboard / "dashboards").glob("*.html"):
        text = page.read_text(encoding="utf-8").replace("</head>", css + "</head>", 1)
        for key, status in statuses.items():
            marker = f'data-chart-key="{key}"'
            position = text.find(marker)
            if position < 0:
                continue
            start = text.rfind("<figure", 0, position)
            end = text.find(">", start)
            if start >= 0 and end >= 0:
                text = (
                    text[:start]
                    + text[start:end].replace(
                        'class="chart-card"', f'class="chart-card version-{status}"'
                    )
                    + text[end:]
                )
        page.write_text(text, encoding="utf-8")
    return {
        status: list(statuses.values()).count(status)
        for status in ("green", "yellow", "red")
    }
