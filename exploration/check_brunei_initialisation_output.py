#%%
"""Audit a Brunei clone-initialisation dashboard output.

The checker compares rendered navigation, page-assignment metadata, chart
traces, and demand representation status.  It is intentionally read-only and
can be run from a notebook or from the hard-coded run block at the bottom.
"""

from __future__ import annotations

import base64
import csv
import json
import re
import struct
from pathlib import Path
from typing import Any


BASE_YEAR = 2022
ECONOMY_KEY = "02_BD"
EXPECTED_PAGE_LABELS = {"industry": "Industry", "others": "Other demand"}


def _decode_plotly_array(value: Any) -> list[Any]:
    """Decode a Plotly typed-array object, while accepting ordinary lists."""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or "bdata" not in value:
        return []
    dtype = str(value.get("dtype", "f8"))
    formats = {
        "i1": ("b", 1), "u1": ("B", 1), "i2": ("h", 2), "u2": ("H", 2),
        "i4": ("i", 4), "u4": ("I", 4), "f4": ("f", 4), "f8": ("d", 8),
    }
    fmt, size = formats.get(dtype, ("d", 8))
    raw = base64.b64decode(str(value["bdata"]))
    return list(struct.unpack("<" + fmt * (len(raw) // size), raw))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _rendered_section_labels(html: str) -> list[str]:
    """Return labels from the jump-navigation chips, in rendered order."""
    nav = re.search(r'<div class="jump-nav".*?</div>\s*</div>', html, re.S)
    text = nav.group(0) if nav else ""
    return re.findall(r'class="jump-chip"[^>]*>(.*?)</a>', text, re.S)


def _expected_section_labels(rows: list[dict[str, str]]) -> list[str]:
    """Return the section labels that the metadata says should be visible."""
    labels: list[str] = []
    for row in rows:
        label = row.get("section_label", "").strip()
        flow = row.get("common_flow_label", "").strip()
        candidate = flow or label
        if candidate and candidate not in labels:
            labels.append(candidate)
    return labels


def _chart_traces(root: Path, page_key: str) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    bundle_paths = sorted((root / "chart_bundles").glob(f"{page_key}__charts.json"))
    bundle_paths += sorted((root / "chart_bundles").glob(f"{page_key}__charts.js"))
    for bundle in bundle_paths:
        text = bundle.read_text(encoding="utf-8")
        if bundle.suffix == ".js":
            text = text.split("=", 1)[1].rstrip(";\n")
        charts = json.loads(text).get("charts", {})
        for chart_key, figure in charts.items():
            for trace in figure.get("data", []):
                traces.append({"chart_key": chart_key, **trace})
    return traces


def _trace_summary(traces: list[dict[str, Any]], contains: str) -> list[dict[str, Any]]:
    result = []
    for trace in traces:
        if contains.lower() not in str(trace.get("chart_key", "")).lower():
            continue
        years = _decode_plotly_array(trace.get("x"))
        values = _decode_plotly_array(trace.get("y"))
        result.append({"name": trace.get("name", ""), "years": years, "values": values})
    return result


def _industry_target_difference(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare LEAP Target and 9th Target when the output contains both."""
    candidates = _trace_summary(traces, "14__14_industry_sector")
    leap = next((t for t in candidates if "leap target" in str(t["name"]).lower()), None)
    leap_label = "LEAP Target"
    if leap is None:
        leap = next((t for t in candidates if "leap reference" in str(t["name"]).lower()), None)
        leap_label = "LEAP Reference"
    ninth = next((t for t in candidates if "9th target" in str(t["name"]).lower()), None)
    if not leap or not ninth:
        return {
            "status": "unavailable",
            "reason": "The Industry aggregate chart does not contain a LEAP projection trace and a 9th Target trace.",
            "available_trace_names": [item["name"] for item in candidates],
        }
    paired = {
        int(year): (float(leap_value), float(ninth_value))
        for year, leap_value, ninth_value in zip(leap["years"], leap["values"], ninth["values"])
        if int(year) > BASE_YEAR
    }
    differences = [
        {"year": year, "leap_target": values[0], "ninth_target": values[1], "difference": values[0] - values[1]}
        for year, values in paired.items()
    ]
    return {
        "status": "available",
        "compared_traces": [leap_label, "9th Target"],
        "interpretation": (
            "This is a source/scenario comparison, not a like-for-like TGT comparison. "
            "The output has no LEAP Target trace, so LEAP Reference is compared with 9th Target."
            if leap_label == "LEAP Reference"
            else "Like-for-like LEAP Target and 9th Target comparison."
        ),
        "points": len(differences),
        "max_absolute_difference": max((abs(item["difference"]) for item in differences), default=0.0),
        "differences": differences,
    }


def check_brunei_output(dashboard_root: Path) -> dict[str, Any]:
    """Return a structured, read-only diagnostic report for one dashboard root."""
    assignment_path = dashboard_root / "supporting_files" / "page_assignment_summary.csv"
    status_path = dashboard_root.parent / "mapping_chain" / "leap_demand_representation_status.csv"
    industry_html = dashboard_root / "dashboards" / "industry.html"
    others_html = dashboard_root / "dashboards" / "other_demand.html"
    assignments = _read_rows(assignment_path)
    report: dict[str, Any] = {"economy": ECONOMY_KEY, "dashboard_root": str(dashboard_root), "checks": {}}

    for page_key, page_label in EXPECTED_PAGE_LABELS.items():
        page_rows = [row for row in assignments if row.get("page_key") == page_key]
        html_path = industry_html if page_key == "industry" else others_html
        html = html_path.read_text(encoding="utf-8") if html_path.exists() else ""
        expected = _expected_section_labels(page_rows)
        rendered = _rendered_section_labels(html)
        missing = [label for label in expected if label not in rendered]
        report["checks"][f"{page_key}_navigation"] = {
            "status": "fail" if missing else "pass",
            "expected_from_page_assignment": expected,
            "rendered_jump_navigation": rendered,
            "missing_labels": missing,
        }

    industry_traces = _chart_traces(dashboard_root, "industry")
    report["checks"]["industry_target_difference"] = _industry_target_difference(industry_traces)

    other_traces = _chart_traces(dashboard_root, "others")
    other_names = sorted({str(trace.get("name", "")) for trace in other_traces})
    report["checks"]["other_demand_projections"] = {
        "status": "fail" if not any("leap" in name.lower() for name in other_names) else "pass",
        "trace_names": other_names,
        "explanation": "Other Demand has no LEAP projection trace; this is expected when only the 9th source is represented, but it means LEAP projections are unavailable rather than zero.",
    }

    if status_path.exists():
        status_rows = _read_rows(status_path)
        other_status = sorted({row.get("representation_status", "") for row in status_rows if row.get("component_branch") in {"Other sector", "Industry"}})
        report["checks"]["representation_status"] = {"status_values": other_status, "source": str(status_path)}
    return report


def write_report(dashboard_root: Path, output_path: Path) -> Path:
    report = check_brunei_output(dashboard_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return output_path


#%% Manual run block: set RUN_ROOT to the clone-initialisation output to audit.
RUN_ROOT = Path(r"C:\Users\Work\github\leap_initialisation\outputs\local_export_dashboards_20260823\02_BD\output\02_BD\dashboard\02BD")
REPORT_PATH = Path("outputs") / "brunei_initialisation_output_check.json"


if __name__ == "__main__":
    print(write_report(RUN_ROOT, REPORT_PATH))

#%%
